import mxnet as mx
import numpy
from critics import Critic

class DQNOutputOp(mx.operator.NumpyOp):
    def __init__(self):
        super(DQNOutputOp, self).__init__(need_top_grad=False)

    def list_arguments(self):
        return ['data', 'action', 'reward']

    def list_outputs(self):
        return ['output']

    def infer_shape(self, in_shape):
        data_shape = in_shape[0]
        action_shape = (in_shape[0][0],)
        reward_shape = (in_shape[0][0],)
        output_shape = in_shape[0]
        return [data_shape, action_shape, reward_shape], [output_shape]

    def forward(self, in_data, out_data):
        x = in_data[0]
        y = out_data[0]
        y[:] = x

    def backward(self, out_grad, in_data, out_data, in_grad):
        x = out_data[0]
        action = in_data[1].astype(numpy.int)
        reward = in_data[2]
        dx = in_grad[0]
        dx[:] = 0
        #dx[numpy.arange(action.shape[0]), action] = numpy.clip(x[numpy.arange(action.shape[0]), action] - reward, -1, 1)
        dx[numpy.arange(action.shape[0]), action] = (x[numpy.arange(action.shape[0]), action] - reward)

action_num = 4
minibatch_size = 128
net = mx.symbol.Variable('data')
net = mx.symbol.Convolution(data=net, name='conv1', kernel=(8, 8), stride=(4, 4), num_filter=16)
net = mx.symbol.Activation(data=net, name='relu1', act_type="relu")
net = mx.symbol.Convolution(data=net, name='conv2', kernel=(4, 4), stride=(2, 2), num_filter=32)
net = mx.symbol.Activation(data=net, name='relu2', act_type="relu")
net = mx.symbol.Flatten(data=net)
net = mx.symbol.FullyConnected(data=net, name='fc3', num_hidden=256)
net = mx.symbol.Activation(data=net, name='relu3', act_type="relu")
net = mx.symbol.FullyConnected(data=net, name='fc4', num_hidden=action_num)
DQNOutput = DQNOutputOp()
net = DQNOutput(data=net, name='dqn')

data_shapes = {'data': (minibatch_size, action_num) + (84, 84),
               'dqn_action': (minibatch_size,), 'dqn_reward':(minibatch_size,)}
optimizer_params = {'name':'sgd', 'learning_rate':0.01, 'rescale_grad':1.0 / float(minibatch_size),
                    'wd':0.00001}
q_net = Critic(data_shapes=data_shapes, sym=net, optimizer_params=optimizer_params, name='QNet')
target_q_net = Critic(data_shapes=data_shapes, sym=net, optimizer_params=optimizer_params, name='Target_QNet')
print q_net.calc_score(batch_size=minibatch_size,
                        data=numpy.random.normal(0, 1, data_shapes['data']).astype('float32'))[0].asnumpy()

q_net.print_stat()
target_q_net.print_stat()
