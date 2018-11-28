# Demo on using LSTM, tensorflow
"""
LOG:
Sep. 13th: add graph freeze on the feature fusion + ConvLSTM part
Sep. 23th: add tensorboard visualization on sub loss
Oct

"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import tensorflow.contrib.slim as slim
import inspect
import tensorflow as tf
import _init_paths
import utils.util as util
from lstm.cell import ConvGRUCell
from model import modelEAST
from config.configrnn import get_config
# from bayes.correlation import cost_volume, feature_propagation

import numpy as np
############ Macros ############
BASIC = "baisc"
CUDNN = "cudnn"
BLOCK = "block"
CONV  = "conv2d"
CUDNN_INPUT_LINEAR_MODE = "linear_input"
CUDNN_RNN_BIDIRECTION = "bidirection"
CUDNN_RNN_UNIDIRECTION  = "unidirection"

FLAGS = tf.app.flags.FLAGS
###################################################
# Model definition for video processing
###################################################
def lineno():
    """Returns the current line number in our program."""
    return inspect.currentframe().f_back.f_lineno

#from pdb import set_trace as pb
def pb():
    print('running through debugging line ', lineno())

class ArrayModel(object):
    """Model used for PTB processing"""
    # here input is totally an object with all kinds of features created by Input class,
    # which use reader functions
    def __init__(self, is_training, config, input_feature, input_flow_maps, reuse_variables=None, initializer=None):
        self.input_score_maps = tf.placeholder(tf.float32, shape=[None, config.num_steps, None, None, 1], name='input_score_maps')

        if FLAGS.geometry == 'RBOX':
            self.input_geo_maps = tf.placeholder(tf.float32, shape=[None, config.num_steps, None, None, 5], name='input_geo_maps')
        else:
            self.input_geo_maps = tf.placeholder(tf.float32, shape=[None, config.num_steps, None, None, 8], name='input_geo_maps')
        self.input_training_masks = tf.placeholder(tf.float32, shape=[None, config.num_steps, None, None, 1], name='input_training_masks')
        self.sum_kernel = tf.placeholder(tf.float32, shape=[11, 11, 32, 32])
        self._is_training = is_training
        self._rnn_params = None
        self._cell = None
        self._input_data = input_data
        self.batch_size = config.batch_size
        self.num_steps = config.num_steps

        loss_set = []
        L_Dice_set = []
        L_AABB_set = []
        L_Theta_set = []
        self.score_map_set = []
        state = None
        reuse_variables = reuse_variables
        for k in range(config.num_steps):
            F_score, F_geometry, state = self._cnn_gru(input_feature[:, k, :, :, :], input_flow_maps[], state, feature_prev, self.sum_kernel, config, reuse_variables, lstm=True)
            l_cls, l_aabb, l_theta, l_model = loss(self.input_score_maps[:, k, :, :, :], F_score,
                     self.input_geo_maps[:, k, :, :, :], F_geometry,
                     self.input_training_masks[:, k, :, :, :])
            L_Dice_set.append(l_cls)
            L_AABB_set.append(l_aabb)
            L_Theta_set.append(l_theta)
            loss_set.append(l_model)
            self.score_map_set.append(F_score[0, :, :, :])
            reuse_variables=True
        self._loss = tf.stack(loss_set, 0)
        self._loss_cls = tf.reduce_mean(tf.stack(L_Dice_set, 0))
        self._loss_aabb = tf.reduce_mean(tf.stack(L_AABB_set, 0))
        self._loss_theta = tf.reduce_mean(tf.stack(L_Theta_set, 0))
        self._cost = tf.reduce_mean(tf.stack(loss_set, 0))
        # self._loss = tf.reshape(self._loss, [self.batch_size, self.num_steps, `])
        # try to transform the loss array with tf.reshape
        self._final_state = state
        if not is_training:
            return
        # training details
        # since _lr is a variable, so we could assign number to it later by assignment
        self._lr = tf.Variable(0.0, trainable=False)
        tvar1 = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='multi_rnn_cell')
        tvar2 = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='pred_module')
        tvars = tvar1 + tvar2
        # gradient clipping
        grads, _ = tf.clip_by_global_norm(tf.gradients(self._loss, tvars), config.max_grad_norm)
        # optimizer
        # optimizer = tf.train.GradientDescentOptimizer(self._lr)
        optimizer = tf.train.AdamOptimizer(self._lr)
        # how to manipulate the training gradient, the optimizer actually gives us an function to do that
        self._train_op = optimizer.apply_gradients(
                zip(grads, tvars),
                global_step=tf.train.get_or_create_global_step())
        self._new_lr = tf.placeholder(
                tf.float32, shape=[], name="new_learning_rate")
        self._lr_update = tf.assign(self._lr, self._new_lr)

    def _build_rnn_graph(self, inputs, config, is_training):
        if config.rnn_mode == CUDNN:
            return self._build_rnn_graph_cudnn(inputs, config, is_training)
        else:
            return self._build_rnn_graph_lstm(inputs, config, is_training)

    # for cnn+lstm model design
    def _cnn_gru(self, input_fe, state, feature_prev, config, reuse_variables, is_training = True, lstm=True):
        def make_cell():
            cell = self._get_lstm_cell(config, is_training)
            if is_training and config.keep_prob < 1:
                cell = tf.contrib.rnn.DropoutWrapper(cell, output_keep_prob=config.keep_prob)
            return cell
        with tf.variable_scope(tf.get_variable_scope(), reuse=reuse_variables):
            _, _, feature = modelEAST.model(inputs, is_training=False)
            # if feature_prev is not None:
            #     # add correlation computation to state, so the state is updated;
            #     m_corr = feature_propagation(feature_prev, state[0], 5, sum_kernel)
            if lstm:
                cell = tf.contrib.rnn.MultiRNNCell(
                    [make_cell() for _ in range(config.num_layers)], state_is_tuple=True)
                self._initial_state = cell.zero_state(config.batch_size, tf.float32)
                if state is None:
                    state = self._initial_state
                # else:
                #     state = (m_corr,)
                output, state = cell(feature, state)
                # F_heatmap = slim.conv2d(output, 1, 1, activation_fn=tf.nn.sigmoid, normalizer_fn=None)
                with tf.variable_scope('pred_module', reuse=reuse_variables):
                    F_score = slim.conv2d(output, 1, 1, activation_fn=tf.nn.sigmoid, normalizer_fn=None)
                    # 4 channel of axis aligned bbox and 1 channel rotation angle
                    geo_map = slim.conv2d(output, 4, 1, activation_fn=tf.nn.sigmoid, normalizer_fn=None) * 512
                    angle_map = (slim.conv2d(output, 1, 1, activation_fn=tf.nn.sigmoid, normalizer_fn=None) - 0.5) * np.pi/2 # angle is between [-45, 45]
                    F_geometry = tf.concat([geo_map, angle_map], axis=-1)
                feature_prev = feature
                return F_score, F_geometry, state, feature_prev
            else:
                pass


    def _build_rnn_graph_cudnn(self, inputs, config, is_training):
        """Build the inference graph using CUDNN cell"""
        # here we want to pemute the dimensions
        # inputs = tf.transpose(inputs, [1, 0, 2])
        self._cell = tf.contrib.cudnn_rnn.CudnnLSTM(
            num_layers= config.num_layers,
            num_units = config.hidden_size,
            input_size= config.vocab_size,
            dropout = 1 - config.keep_prob if is_training else 0
        )
        # what is this used for
        #params_size_t = self.
        params_size_t = self._cell.params_size()
        self._rnn_params = tf.get_variable(
                "lstm_params",
                initializer=tf.random_uniform(
                        [params_size_t], -config.init_scale, config.init_scale),
                validate_shape=False)
        c = tf.zeros([config.num_layers, self.batch_size, config.hidden_size],
                                  tf.float32)
        h = tf.zeros([config.num_layers, self.batch_size, config.hidden_size],
                                  tf.float32)
        self._initial_state = (tf.contrib.rnn.LSTMStateTuple(h=h, c=c),)
        outputs, h, c = self._cell(inputs, h, c, self._rnn_params, is_training)
        outputs = tf.transpose(outputs, [1, 0, 2])
        outputs = tf.reshape(outputs, [-1, config.hidden_size])
        return outputs, (tf.contrib.rnn.LSTMStateTuple(h=h, c=c),)

    def _get_lstm_cell(self, config, is_training):
        if config.rnn_mode == BASIC:
            return tf.contrib.rnn.BasicLSTMCell(config.hidden_size, forget_bias=0.0, state_is_tuple=True,
                                               reuse = not is_training)
        if config.rnn_mode == BLOCK:
            return tf.contrib.rnn.LSTMBlockCell(config.hidden_size, forget_bias = 0.0)
        if config.rnn_mode == CONV:
            # kwargs = {"conv_ndims": 3, "input_shape": config.shape, "output_channels": config.filters,
            #           "kernel_shape": config.kernel}
            # return tf.contrib.rnn.Conv2DLSTMCell(**kwargs)
            return ConvGRUCell([128, 128], config.filters, config.kernel, activation=tf.nn.relu)
        raise ValueError("rnn_mode %s not supported" % config.rnn_mode)

    def _build_rnn_graph_lstm(self, inputs, config, is_training):
        """Build the inference graph using canonial LSTM cells."""
        """Self defined functions """
        def make_cell():
            cell = self._get_lstm_cell(config, is_training)
            # when a cell is constructed, we will need to use the mechanism called wrapper
            if is_training and config.keep_prob < 1:
                cell = tf.contrib.rnn.DropoutWrapper(cell, output_keep_prob=config.keep_prob)
            return cell
        cell = tf.contrib.rnn.MultiRNNCell(
            [make_cell() for _ in range(config.num_layers)], state_is_tuple=True
        )
        self._initial_state = cell.zero_state(config.batch_size, tf.float32)
        state = self._initial_state
        outputs = []
        with tf.variable_scope("RNN"):
            for time_step in range(self.num_steps):
                if time_step > 0:
                    tf.get_variable_scope().reuse_variables()
                (cell_output, state) = cell(inputs[:, time_step, :, :, :], state)
                outputs.append(cell_output)
        # pb()
        output = tf.concat(outputs, 0)
        # Performs fully dynamic unrolling of inputs
        # output, state = tf.nn.dynamic_rnn(cell=cell,
        #                            inputs=data,
        #                            dtype=tf.float32)

        return output, state

    def export_ops(self, name):
        """Exports ops to collections."""
        self._name = name
        # import pdb;pdb.set_trace()
        ops = {util.with_prefix(self._name, "cost"): self._cost}
        if self._is_training:
            ops.update(lr=self._lr, new_lr=self._new_lr, lr_update=self._lr_update)
            if self._rnn_params:
                ops.update(rnn_params=self._rnn_params)
        for name, op in ops.items():
            tf.add_to_collection(name, op)
        self._initial_state_name = util.with_prefix(self._name, "initial")
        self._final_state_name = util.with_prefix(self._name, "final")
        util.export_state_tuples(self._initial_state, self._initial_state_name)
        util.export_state_tuples(self._final_state, self._final_state_name)

    def assign_lr(self, session, lr_value):
        session.run(self._lr_update, feed_dict={self._new_lr: lr_value})

    @property
    def input_data(self):
        return self._input_data

    @property
    def targets(self):
        return self._targets

    @property
    def initial_state(self):
        return self._initial_state

    @property
    def heatmap_predict(self):
        return self._heatmap_predict

    @property
    def cost(self):
        return self._cost

    @property
    def loss_cls(self):
        return self._loss_cls


    @property
    def loss_aabb(self):
        return self._loss_aabb

    @property
    def loss_theta(self):
        return self._loss_theta

    @property
    def loss(self):
        return self._loss

    # @property
    # def grads(self):
    #     return self._grads
    @property
    def final_state(self):
        return self._final_state

    @property
    def lr(self):
        return self._lr

    @property
    def train_op(self):
        return self._train_op

    @property
    def initial_state_name(self):
        return self._initial_state_name

    @property
    def final_state_name(self):
        return self._final_state_name


def tower_loss(feature, flow_maps, score_maps, geo_maps, training_masks, config=None, reuse_variables=None):
    """
    input with score maps, geo maps
    """
    loss_set = []
    L_Dice_set = []
    L_AABB_set = []
    L_Theta_set = []
    score_map_set = []
    state = None
    # initialize with global setup
    reuse_variables = reuse_variables
    for k in range(config.num_steps):
        F_score, F_geometry, state = self._cnn_gru(input_feature[:, k, :, :, :], input_flow_maps[], state, self.sum_kernel, config, reuse_variables, lstm=True)
        l_cls, l_aabb, l_theta, l_model = loss(self.input_score_maps[:, k, :, :, :], F_score,
                 self.input_geo_maps[:, k, :, :, :], F_geometry,
                 self.input_training_masks[:, k, :, :, :])
        L_Dice_set.append(l_cls)
        L_AABB_set.append(l_aabb)
        L_Theta_set.append(l_theta)
        loss_set.append(l_model)
        self.score_map_set.append(F_score[0, :, :, :])
        reuse_variables=True
    self._loss = tf.stack(loss_set, 0)
    self._loss_cls = tf.reduce_mean(tf.stack(L_Dice_set, 0))
    self._loss_aabb = tf.reduce_mean(tf.stack(L_AABB_set, 0))
    self._loss_theta = tf.reduce_mean(tf.stack(L_Theta_set, 0))
    self._cost = tf.reduce_mean(tf.stack(loss_set, 0))
    # self._loss = tf.reshape(self._loss, [self.batch_size, self.num_steps, `])
    # try to transform the loss array with tf.reshape
    self._final_state = state
    if not is_training:
        return


def _gru(self, input_feature, state, feature_prev, config, reuse_variables, is_training = True, lstm=True):
    def make_cell():
        cell = self._get_lstm_cell(config, is_training)
        if is_training and config.keep_prob < 1:
            cell = tf.contrib.rnn.DropoutWrapper(cell, output_keep_prob=config.keep_prob)
        return cell
    with tf.variable_scope(tf.get_variable_scope(), reuse=reuse_variables):
        feature = None
        # if feature_prev is not None:
        #     # add correlation computation to state, so the state is updated;
        #     m_corr = feature_propagation(feature_prev, state[0], 5, sum_kernel)
        if lstm:
            cell = tf.contrib.rnn.MultiRNNCell(
                [make_cell() for _ in range(config.num_layers)], state_is_tuple=True)
            self._initial_state = cell.zero_state(config.batch_size, tf.float32)
            if state is None:
                state = self._initial_state
            # else:
            #     state = (m_corr,)
            output, state = cell(feature, state)
            # F_heatmap = slim.conv2d(output, 1, 1, activation_fn=tf.nn.sigmoid, normalizer_fn=None)
            with tf.variable_scope('pred_module', reuse=reuse_variables):
                F_score = slim.conv2d(output, 1, 1, activation_fn=tf.nn.sigmoid, normalizer_fn=None)
                # 4 channel of axis aligned bbox and 1 channel rotation angle
                geo_map = slim.conv2d(output, 4, 1, activation_fn=tf.nn.sigmoid, normalizer_fn=None) * 512
                angle_map = (slim.conv2d(output, 1, 1, activation_fn=tf.nn.sigmoid, normalizer_fn=None) - 0.5) * np.pi/2 # angle is between [-45, 45]
                F_geometry = tf.concat([geo_map, angle_map], axis=-1)
            feature_prev = feature
            return F_score, F_geometry, state, feature_prev
        else:
            pass




# def loss(y_true_htm, y_pred_htm):
#     """
#     y_true_htm: groundtruth heat_map,
#     y_pred_htm: predicted heat_map from 32-channel feature layers
#     """
#     loss_l2 = tf.nn.l2_loss(y_true_htm - y_pred_htm)
#     tf.summary.scalar('heatmap_loss', loss_l2)
#
#     return loss_l2
# with input of score maps, geo maps
def loss(y_true_cls, y_pred_cls,
         y_true_geo, y_pred_geo,
         training_mask):
    '''
    define the loss used for training, contraning two part,
    the first part we use dice loss instead of weighted logloss,
    the second part is the iou loss defined in the paper
    :param y_true_cls: ground truth of text
    :param y_pred_cls: prediction os text
    :param y_true_geo: ground truth of geometry
    :param y_pred_geo: prediction of geometry
    :param training_mask: mask used in training, to ignore some text annotated by ###
    :return:
    '''
    classification_loss = dice_coefficient(y_true_cls, y_pred_cls, training_mask)
    # scale classification loss to match the iou loss part
    classification_loss *= 0.01

    # d1 -> top, d2->right, d3->bottom, d4->left
    d1_gt, d2_gt, d3_gt, d4_gt, theta_gt = tf.split(value=y_true_geo, num_or_size_splits=5, axis=3)
    d1_pred, d2_pred, d3_pred, d4_pred, theta_pred = tf.split(value=y_pred_geo, num_or_size_splits=5, axis=3)
    area_gt = (d1_gt + d3_gt) * (d2_gt + d4_gt)
    area_pred = (d1_pred + d3_pred) * (d2_pred + d4_pred)
    w_union = tf.minimum(d2_gt, d2_pred) + tf.minimum(d4_gt, d4_pred)
    h_union = tf.minimum(d1_gt, d1_pred) + tf.minimum(d3_gt, d3_pred)
    area_intersect = w_union * h_union
    area_union = area_gt + area_pred - area_intersect
    L_AABB = -tf.log((area_intersect + 1.0)/(area_union + 1.0))
    L_theta = 1 - tf.cos(theta_pred - theta_gt)
    L_g = L_AABB + 20 * L_theta

    return classification_loss, tf.reduce_mean(L_AABB * y_true_cls * training_mask), tf.reduce_mean(L_theta * y_true_cls * training_mask), tf.reduce_mean(L_g * y_true_cls * training_mask) + classification_loss



def dice_coefficient(y_true_cls, y_pred_cls,
                     training_mask):
    '''
    dice loss
    :param y_true_cls:
    :param y_pred_cls:
    :param training_mask:
    :return:
    '''
    eps = 1e-5
    intersection = tf.reduce_sum(y_true_cls * y_pred_cls * training_mask)
    union = tf.reduce_sum(y_true_cls * training_mask) + tf.reduce_sum(y_pred_cls * training_mask) + eps
    loss = 1. - (2 * intersection / union)

    return loss


if __name__ == "__main__":
    flags = tf.app.flags
    # The only part you need to modify during training
    flags.DEFINE_string("system", "local", "deciding running env")
    flags.DEFINE_string("data_path", "/media/dragonx/DataStorage/ARC/EASTRNN/data/GAP_process", "Where data is stored")
    flags.DEFINE_string('checkpoint_path', '/media/dragonx/DataStorage/ARC/checkpoints/', '')
    flags.DEFINE_boolean('restore', False, 'whether to resotre from checkpoint')
    flags.DEFINE_string("rnn_mode", CONV, "one of CUDNN: BASIC, BLOCK")
    flags.DEFINE_integer("num_readers", 4, "process used to fetch data")
    flags.DEFINE_string("model", "test", "A type of model. Possible options are: small, medium, large.")
    flags.DEFINE_integer("num_gpus", 1, "Larger than 1 will create multiple training replicas")
    flags.DEFINE_boolean("random", True, "style when feeding grouped frames data")
    flags.DEFINE_boolean("source", False, "whether load data from source")
    flags.DEFINE_boolean("dis_plt", False, "whether using pyplot real-time display ")
    flags.DEFINE_integer('save_checkpoint_steps', 1000, '')
    flags.DEFINE_integer('save_summary_steps', 100, '')
    flags.DEFINE_string('pretrained_model_path', '/media/dragonx/DataStorage/ARC/EASTRNN/weights/EAST/resnet_v1_50.ckpt', '')
    flags.DEFINE_string('geometry', 'RBOX', 'set for bb')
    FLAGS = flags.FLAGS

    save_path = '/media/dragonx/DataStorage/ARC/EASTRNN/checkpoints/LSTM/'
    #train_input = DetectorInputMul(save_path, 1, 2, 0)
    print("data has been loaded")
    config = get_config(FLAGS)
    # Global initializer for Variables in the model
    gpu_options = tf.GPUOptions(allow_growth=True)
    #global_step = tf.get_variable('global_step', [], initializer=tf.constant_initializer(0), trainable=False)
    # log: May 3rd, we need to adapt the model input, with config
    with tf.name_scope("Train"):
        # use placeholder to stand for input and targets
        initializer = tf.random_normal_initializer()
        x_train = tf.placeholder(tf.float32, shape=[None, config.num_steps, None, None, 3])
        m = ArrayModel(True, config, x_train, reuse_variables=None, initializer=initializer)
    with tf.name_scope("Val"):
        # use placeholder to stand for input and targets
        initializer = tf.random_normal_initializer()
        x_val = tf.placeholder(tf.float32, shape=[None, config.num_steps, None, None, 3])
        mval = ArrayModel(True, config, x_val, reuse_variables=True, initializer=initializer)

    if FLAGS.pretrained_model_path is not None:
        variable_restore_op = slim.assign_from_checkpoint_fn(FLAGS.pretrained_model_path, slim.get_trainable_variables(),
                                                             ignore_missing_vars=True)
    with tf.Session(config=tf.ConfigProto(allow_soft_placement=True)) as sess:
        if FLAGS.restore:
            print('continue training from previous checkpoint')
            ckpt = tf.train.latest_checkpoint(FLAGS.checkpoint_path)
            saver.restore(sess, ckpt)
        else:
            initializer = tf.random_normal_initializer()
            sess.run(initializer)
            if FLAGS.pretrained_model_path is not None:
                variable_restore_op(sess)

    print('hahaha')
