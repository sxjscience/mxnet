import matplotlib as mpl
mpl.use('Agg')
from matplotlib import pyplot as plt

import argparse
import mxnet as mx
from mxnet import gluon
from mxnet.gluon import nn
from mxnet import autograd
from data import cifar10_iterator
import numpy as np
import logging
import cv2
from datetime import datetime
import os
import time

def fill_buf(buf, i, img, shape):
    n = buf.shape[0]/shape[1]
    m = buf.shape[1]/shape[0]

    sx = (i%m)*shape[0]
    sy = (i/m)*shape[1]
    buf[sy:sy+shape[1], sx:sx+shape[0], :] = img
    return None

def visual(title, X, name):
    assert len(X.shape) == 4
    X = X.transpose((0, 2, 3, 1))
    X = np.clip((X - np.min(X))*(255.0/(np.max(X) - np.min(X))), 0, 255).astype(np.uint8)
    n = np.ceil(np.sqrt(X.shape[0]))
    buff = np.zeros((int(n*X.shape[1]), int(n*X.shape[2]), int(X.shape[3])), dtype=np.uint8)
    for i, img in enumerate(X):
        fill_buf(buff, i, img, X.shape[1:3])
    buff = cv2.cvtColor(buff, cv2.COLOR_BGR2RGB)
    plt.imshow(buff)
    plt.title(title)
    plt.savefig(name)
    return None

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', type=str, default='cifar10', help='dataset to use. options are cifar10 and imagenet.')
    parser.add_argument('--batchSize', type=int, default=64, help='input batch size')
    parser.add_argument('--imageSize', type=int, default=64, help='the height / width of the input image to network')
    parser.add_argument('--nz', type=int, default=100, help='size of the latent z vector')
    parser.add_argument('--ngf', type=int, default=64)
    parser.add_argument('--ndf', type=int, default=64)
    parser.add_argument('--niter', type=int, default=25, help='number of epochs to train for')
    parser.add_argument('--lr', type=float, default=0.0002, help='learning rate, default=0.0002')
    parser.add_argument('--beta1', type=float, default=0.5, help='beta1 for adam. default=0.5')
    parser.add_argument('--cuda', action='store_true', help='enables cuda')
    parser.add_argument('--ngpu', type=int, default=1, help='number of GPUs to use')
    parser.add_argument('--netG', default='', help="path to netG (to continue training)")
    parser.add_argument('--netD', default='', help="path to netD (to continue training)")
    parser.add_argument('--outf', default='./results', help='folder to output images and model checkpoints')
    parser.add_argument('--manualSeed', type=int, help='manual seed')
    parser.add_argument('--check_point', default=True, help="save results at each epoch or not")

    opt = parser.parse_args()
    print(opt)

    logging.basicConfig(level=logging.DEBUG)
    ngpu = int(opt.ngpu)
    nz = int(opt.nz)
    ngf = int(opt.ngf)
    ndf = int(opt.ndf)
    nc = 3
    ctx = mx.gpu(0)
    check_point = bool(opt.check_point)
    outf = opt.outf

    if not os.path.exists(outf):
        os.makedirs(outf)

    if opt.dataset == 'cifar10':
        train_iter, val_iter = cifar10_iterator(opt.batchSize, (3, 64, 64), 64)

    # build the generator
    netG = nn.Sequential()
    with netG.name_scope():
        # input is Z, going into a convolution
        netG.add(nn.Conv2DTranspose(ngf * 8, 4, 1, 0, use_bias=False))
        netG.add(nn.BatchNorm())
        netG.add(nn.Activation('relu'))
        # state size. (ngf*8) x 4 x 4
        netG.add(nn.Conv2DTranspose(ngf * 4, 4, 2, 1, use_bias=False))
        netG.add(nn.BatchNorm())
        netG.add(nn.Activation('relu'))
        # state size. (ngf*8) x 8 x 8
        netG.add(nn.Conv2DTranspose(ngf * 2, 4, 2, 1, use_bias=False))
        netG.add(nn.BatchNorm())
        netG.add(nn.Activation('relu'))
        # state size. (ngf*8) x 16 x 16
        netG.add(nn.Conv2DTranspose(ngf, 4, 2, 1, use_bias=False))
        netG.add(nn.BatchNorm())
        netG.add(nn.Activation('relu'))
        # state size. (ngf*8) x 32 x 32
        netG.add(nn.Conv2DTranspose(nc, 4, 2, 1, use_bias=False))
        netG.add(nn.Activation('tanh'))
        # state size. (nc) x 64 x 64

    # build the discriminator
    netD = nn.Sequential()
    with netD.name_scope():
        # input is (nc) x 64 x 64
        netD.add(nn.Conv2D(ndf, 4, 2, 1, use_bias=False))
        netD.add(nn.LeakyReLU(0.2))
        # state size. (ndf) x 32 x 32
        netD.add(nn.Conv2D(ndf * 2, 4, 2, 1, use_bias=False))
        netD.add(nn.BatchNorm())
        netD.add(nn.LeakyReLU(0.2))
        # state size. (ndf) x 16 x 16
        netD.add(nn.Conv2D(ndf * 4, 4, 2, 1, use_bias=False))
        netD.add(nn.BatchNorm())
        netD.add(nn.LeakyReLU(0.2))
        # state size. (ndf) x 8 x 8
        netD.add(nn.Conv2D(ndf * 8, 4, 2, 1, use_bias=False))
        netD.add(nn.BatchNorm())
        netD.add(nn.LeakyReLU(0.2))
        # state size. (ndf) x 4 x 4
        netD.add(nn.Conv2D(2, 4, 1, 0, use_bias=False))

    # loss
    loss = gluon.loss.SoftmaxCrossEntropyLoss()

    # initialize the generator and the discriminator
    netG.collect_params().initialize(mx.init.Normal(0.02), ctx=ctx)
    netD.collect_params().initialize(mx.init.Normal(0.02), ctx=ctx)

    # trainer for the generator and the discriminator
    trainerG = gluon.Trainer(netG.collect_params(), 'adam', {'learning_rate': opt.lr, 'beta1': opt.beta1})
    trainerD = gluon.Trainer(netD.collect_params(), 'adam', {'learning_rate': opt.lr, 'beta1': opt.beta1})

    # ============printing==============
    real_label = mx.nd.ones((opt.batchSize,), ctx=ctx)
    fake_label = mx.nd.zeros((opt.batchSize,), ctx=ctx)

    metric = mx.metric.Accuracy()
    print('Training... ')
    stamp =  datetime.now().strftime('%Y_%m_%d-%H_%M')

    iter = 0
    for epoch in range(opt.niter):
        tic = time.time()
        train_iter.reset()
        btic = time.time()
        for batch in train_iter:
            ############################
            # (1) Update D network: maximize log(D(x)) + log(1 - D(G(z)))
            ###########################
            # train with real_t
            data = batch.data[0].copyto(ctx)
            noise = mx.nd.random_normal(0, 1, shape=(opt.batchSize, nz, 1, 1), ctx=ctx)

            with autograd.record():
                output = netD(data)
                output = output.reshape((opt.batchSize, 2))
                errD_real = loss(output, real_label)
                metric.update([real_label,], [output,])

                fake = netG(noise)
                output = netD(fake.detach())
                output = output.reshape((opt.batchSize, 2))
                errD_fake = loss(output, fake_label)
                errD = errD_real + errD_fake
                errD.backward()
                metric.update([fake_label,], [output,])

            trainerD.step(opt.batchSize)

            ############################
            # (2) Update G network: maximize log(D(G(z)))
            ###########################
            with autograd.record():
                output = netD(fake)
                output = output.reshape((opt.batchSize, 2))
                errG = loss(output, real_label)
                errG.backward()

            trainerG.step(opt.batchSize)

            name, acc = metric.get()
            # logging.info('speed: {} samples/s'.format(opt.batchSize / (time.time() - btic)))
            logging.info('discriminator loss = %f, generator loss = %f, binary training acc = %f at iter %d epoch %d' %(mx.nd.mean(errD).asscalar(), mx.nd.mean(errG).asscalar(), acc, iter, epoch))
            if iter % 200 == 0:
                visual('gout', fake.asnumpy(), name=os.path.join(outf,'fake_img_iter_%d.png' %iter))
                visual('data', batch.data[0].asnumpy(), name=os.path.join(outf,'real_img_iter_%d.png' %iter))

            iter = iter + 1
            btic = time.time()

        name, acc = metric.get()
        metric.reset()
        logging.info('\nbinary training acc at epoch %d: %s=%f' % (epoch, name, acc))
        logging.info('time: %f' % (time.time() - tic))

        if check_point:
            netG.collect_params().save(os.path.join(outf,'generator_epoch_%d.params' %epoch))
            netD.collect_params().save(os.path.join(outf,'discriminator_epoch_%d.params' % epoch))

    netG.collect_params().save(os.path.join(outf, 'generator.params'))
    netD.collect_params().save(os.path.join(outf, 'discriminator.params'))

