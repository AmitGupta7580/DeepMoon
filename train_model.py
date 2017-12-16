#!/usr/bin/env python
"""Convolutional Neural Network Training Functions

Functions for building and training a (UNET) Convolutional Neural Network on
images of the Moon and binary ring targets.
"""

import numpy as np
import pandas as pd
import h5py

from keras.models import Model
from keras.layers.core import Dropout, Reshape
from keras.layers import merge, Input
from keras.layers.convolutional import Convolution2D, MaxPooling2D
from keras.regularizers import l2

from keras.optimizers import Adam
from keras.callbacks import EarlyStopping
from keras import __version__ as keras_version
from keras import backend as K
K.set_image_dim_ordering('tf')

from utils.template_match_target import *
from utils.processing import *

########################
def get_param_i(param, i):
    """Gets correct parameter for iteration i.

    Parameters
    ----------
    param : list
        List of model hyperparameters to be iterated over.
    i : integer
        Hyperparameter iteration.

    Returns
    -------
    Correct hyperparameter for iteration i.
    """
    if len(param) > i:
        return param[i]
    else:
        return param[0]

########################
def custom_image_generator(data, target, bs=32):
    """Custom image generator that manipulates image/target pairs to prevent
    overfitting in the Convolutional Neural Network.

    Parameters
    ----------
    data : array
        Input images.
    target : array
        Target images.
    bs : int, optional
        Batch size for image manipulation.

    Yields
    ------
    Manipulated images and targets.
        
    """
    L, W = data[0].shape[0], data[0].shape[1]
    while True:
        for i in range(0, len(data), bs):
            d, t = data[i:i + bs].copy(), target[i:i + bs].copy()

            # Random color inversion
            # for j in np.where(np.random.randint(0, 2, bs) == 1)[0]:
            #     d[j][d[j] > 0.] = 1. - d[j][d[j] > 0.]

            # Horizontal/vertical flips
            for j in np.where(np.random.randint(0, 2, bs) == 1)[0]:
                d[j], t[j] = np.fliplr(d[j]), np.fliplr(t[j])      # left/right
            for j in np.where(np.random.randint(0, 2, bs) == 1)[0]:
                d[j], t[j] = np.flipud(d[j]), np.flipud(t[j])      # up/down

            # Random up/down & left/right pixel shifts, 90 degree rotations
            npix = 15
            h = np.random.randint(-npix, npix + 1, bs)    # Horizontal shift
            v = np.random.randint(-npix, npix + 1, bs)    # Vertical shift
            r = np.random.randint(0, 4, bs)               # 90 degree rotations
            for j in range(bs):
                d[j] = np.pad(d[j], ((npix, npix), (npix, npix), (0, 0)),
                              mode='constant')[npix + h[j]:L + h[j] + npix,
                                               npix + v[j]:W + v[j] + npix, :]
                t[j] = np.pad(t[j], (npix,), mode='constant')[npix + h[j]:L + h[j] + npix, 
                                                              npix + v[j]:W + v[j] + npix]
                d[j], t[j] = np.rot90(d[j], r[j]), np.rot90(t[j], r[j])
            yield (d, t)

########################
def get_metrics(data, craters, dim, model, beta=1):
    """Function that prints pertinent metrics at the end of each epoch. 

    Parameters
    ----------
    data : hdf5
        Input images.
    craters : hdf5
        Pandas arrays of human-counted crater data. 
    dim : int
        Dimension of input images (assumes square).
    model : keras model object
        Keras model
    beta : int, optional
        Beta value when calculating F-beta score. Defaults to 1.
    """
    X, Y = data[0], data[1]

    # Get csvs of human-counted craters
    csvs = []
    minrad, maxrad, cutrad, n_csvs = 2, 50, 0.8, len(X)
    diam = 'Diameter (pix)'
    for i in range(n_csvs):
        csv = craters[get_id(i)]
        # remove small/large/half craters
        csv = csv[(csv[diam] < 2 * maxrad) & (csv[diam] > 2 * minrad)]
        csv = csv[(csv['x'] + cutrad * csv[diam] / 2 <= dim)]
        csv = csv[(csv['y'] + cutrad * csv[diam] / 2 <= dim)]
        csv = csv[(csv['x'] - cutrad * csv[diam] / 2 > 0)]
        csv = csv[(csv['y'] - cutrad * csv[diam] / 2 > 0)]
        if len(csv) < 3:    # Exclude csvs with few craters
            csvs.append([-1])
        else:
            csv_coords = np.asarray((csv['x'], csv['y'], csv[diam] / 2)).T
            csvs.append(csv_coords)

    # Calculate custom metrics
    print("")
    print("*********Custom Loss*********")
    recall, precision, fscore = [], [], []
    frac_new, frac_new2, maxrad = [], [], []
    err_lo, err_la, err_r = [], [], []
    preds = model.predict(X)
    for i in range(n_csvs):
        if len(csvs[i]) < 3:
            continue
        (N_match, N_csv, N_templ, maxr,
         elo, ela, er, csv_duplicates) = template_match_t2c(preds[i], csvs[i],
                                                            rmv_oob_csvs=0)
        if N_match > 0:
            p = float(N_match) / float(N_match + (N_templ - N_match))
            r = float(N_match) / float(N_csv)
            f = (1 + beta**2) * (r * p) / (p * beta**2 + r)
            fn = float(N_templ - N_match) / float(N_templ)
            fn2 = float(N_templ - N_match) / float(N_csv)
            recall.append(r)
            precision.append(p)
            fscore.append(f)
            frac_new.append(fn)
            frac_new2.append(fn2)
            maxrad.append(maxr)
            err_lo.append(elo)
            err_la.append(ela)
            err_r.append(er)
            if len(csv_duplicates) > 0:
                print "duplicate(s) (shown above) found in image %d" % i
        else:
            print("skipping iteration %d,N_csv=%d,N_templ=%d,N_match=%d" %
                  (i, N_csv, N_templ, N_match))

    print("binary XE score = %f" % model.evaluate(X, Y))
    if len(recall) > 3:
        print("mean and std of N_match/N_csv (recall) = %f, %f" %
              (np.mean(recall), np.std(recall)))
        print("mean and std of N_match/(N_match + (N_templ-N_match)) " /
              "(precision) = %f, %f" % (np.mean(precision), np.std(precision)))
        print("mean and std of F_%d score = %f, %f" %
              (beta, np.mean(fscore), np.std(fscore)))
        print("mean and std of (N_template - N_match)/N_template (fraction " /
              "of craters that are new) = %f, %f" %
              (np.mean(frac_new), np.std(frac_new)))
        print("mean and std of (N_template - N_match)/N_csv (fraction of " /
              "craters that are new, 2) = %f, %f" %
              (np.mean(frac_new2), np.std(frac_new2)))
        print("mean fractional difference between pred and GT craters = " /
              "%f, %f, %f" % (np.mean(err_lo), np.mean(err_la), np.mean(err_r)))
        print("mean and std of maximum detected pixel radius in an image = " /
              "%f, %f" % (np.mean(maxrad), np.std(maxrad)))
        print("absolute maximum detected pixel radius over all images = " /
              "%f" % np.max(maxrad))
        print("")

########################
def build_model(dim, learn_rate, lmbda, drop, FL, init, n_filters):
    """Function that builds the (UNET) convolutional neural network. 

    Parameters
    ----------
    dim : int
        Dimension of input images (assumes square).
    learn_rate : float
        Learning rate.
    lmbda : float
        Convolution2D regularization parameter. 
    drop : float
        Dropout fraction.
    FL : int
        Filter length.
    init : string
        Weight initialization type.
    n_filters : int
        Number of filters in each layer.

    Returns
    -------
    model : keras model object
        Constructed Keras model.
    """
    print('Making UNET model...')
    img_input = Input(batch_shape=(None, dim, dim, 1))

    a1 = Convolution2D(n_filters, FL, FL, activation='relu', init=init,
                       W_regularizer=l2(lmbda), border_mode='same')(img_input)
    a1 = Convolution2D(n_filters, FL, FL, activation='relu', init=init,
                       W_regularizer=l2(lmbda), border_mode='same')(a1)
    a1P = MaxPooling2D((2, 2), strides=(2, 2))(a1)

    a2 = Convolution2D(n_filters * 2, FL, FL, activation='relu', init=init,
                       W_regularizer=l2(lmbda), border_mode='same')(a1P)
    a2 = Convolution2D(n_filters * 2, FL, FL, activation='relu', init=init,
                       W_regularizer=l2(lmbda), border_mode='same')(a2)
    a2P = MaxPooling2D((2, 2), strides=(2, 2))(a2)

    a3 = Convolution2D(n_filters * 4, FL, FL, activation='relu', init=init,
                       W_regularizer=l2(lmbda), border_mode='same')(a2P)
    a3 = Convolution2D(n_filters * 4, FL, FL, activation='relu', init=init,
                       W_regularizer=l2(lmbda), border_mode='same')(a3)
    a3P = MaxPooling2D((2, 2), strides=(2, 2),)(a3)

    u = Convolution2D(n_filters * 4, FL, FL, activation='relu', init=init,
                      W_regularizer=l2(lmbda), border_mode='same')(a3P)
    u = Convolution2D(n_filters * 4, FL, FL, activation='relu', init=init,
                      W_regularizer=l2(lmbda), border_mode='same')(u)

    u = UpSampling2D((2, 2))(u)
    u = merge((a3, u), mode='concat', concat_axis=3)
    u = Dropout(drop)(u)
    u = Convolution2D(n_filters * 2, FL, FL, activation='relu', init=init,
                      W_regularizer=l2(lmbda), border_mode='same')(u)
    u = Convolution2D(n_filters * 2, FL, FL, activation='relu', init=init,
                      W_regularizer=l2(lmbda), border_mode='same')(u)

    u = UpSampling2D((2, 2))(u)
    u = merge((a2, u), mode='concat', concat_axis=3)
    u = Dropout(drop)(u)
    u = Convolution2D(n_filters, FL, FL, activation='relu', init=init,
                      W_regularizer=l2(lmbda), border_mode='same')(u)
    u = Convolution2D(n_filters, FL, FL, activation='relu', init=init,
                      W_regularizer=l2(lmbda), border_mode='same')(u)

    u = UpSampling2D((2, 2))(u)
    u = merge((a1, u), mode='concat', concat_axis=3)
    u = Dropout(drop)(u)
    u = Convolution2D(n_filters, FL, FL, activation='relu', init=init,
                      W_regularizer=l2(lmbda), border_mode='same')(u)
    u = Convolution2D(n_filters, FL, FL, activation='relu', init=init,
                      W_regularizer=l2(lmbda), border_mode='same')(u)

    # Final output
    final_activation = 'sigmoid'
    u = Convolution2D(1, 1, 1, activation=final_activation, init=init,
                      W_regularizer=l2(lmbda), name='output',
                      border_mode='same')(u)
    u = Reshape((dim, dim))(u)
    model = Model(input=img_input, output=u)

    optimizer = Adam(lr=learn_rate)
    model.compile(loss='binary_crossentropy', optimizer=optimizer)
    print(model.summary())

    return model

########################
def train_and_test_model(Data, Craters, MP, i_MP):
    """Function that trains, tests and saves the model, printing out metrics
    after each model. 

    Parameters
    ----------
    Data : dict
        Inputs and Target Moon data.
    Craters : dict
        Human-counted crater data.
    MP : dict
        Contains all relevant parameters.
    i_MP : int
        Iteration number (when iterating over hypers).
    """
    # Static params
    dim, learn_rate, nb_epoch, bs = MP['dim'], MP['lr'], MP['epochs'], MP['bs']

    # Iterating params
    lmbda = get_param_i(MP['lambda'], i_MP)
    drop = get_param_i(MP['dropout'], i_MP)
    FL = get_param_i(MP['filter_length'], i_MP)
    init = get_param_i(MP['init'], i_MP)
    n_filters = get_param_i(MP['n_filters'], i_MP)

    # Build model
    model = build_model(dim, learn_rate, lmbda, drop, FL, init, n_filters)

    # Main loop
    n_samples = MP['n_train']
    for nb in range(nb_epoch):
        model.fit_generator(custom_image_generator(Data['train'][0], Data['train'][1], batch_size=bs),
                            samples_per_epoch=n_samples, nb_epoch=1, verbose=1,
                            #validation_data=(Data['dev'][0],Data['dev'][1]), #no generator for validation data
                            validation_data=custom_image_generator(Data['dev'][0], Data['dev'][1], batch_size=bs),
                            nb_val_samples=n_samples,
                            callbacks=[EarlyStopping(monitor='val_loss', patience=3, verbose=0)])

        get_metrics(Data['dev'], Craters['dev'], dim, model)

    if MP['save_models'] == 1:
        model.save(MP['save_dir'])

    print('###################################')
    print('##########END_OF_RUN_INFO##########')
    print('learning_rate=%e, batch_size=%d, filter_length=%e, n_epoch=%d, ' /
          'n_train=%d, img_dimensions=%d, init=%s, n_filters=%d, lambda=%e,' /
          'dropout=%f' % (learn_rate, bs, FL, nb_epoch, MP['n_train'],
                          MP['dim'], init, n_filters, lmbda, drop))
    get_metrics(Data['test'], Craters['test'], dim, model)
    print('###################################')
    print('###################################')

########################
def get_models(MP):
    """Top-level function that loads data files and calls train_and_test_model.

    Parameters
    ----------
    MP : dict
        Model Parameters.
    """
    dir = MP['dir']
    n_train, n_dev, n_test = MP['n_train'], MP['n_dev'], MP['n_test']

    # Load data
    train = h5py.File('%strain_images.hdf5' % dir, 'r')
    dev = h5py.File('%sdev_images.hdf5' % dir, 'r')
    test = h5py.File('%stest_images.hdf5' % dir, 'r')
    Data = {
        'train': [train['input_images'][:n_train].astype('float32'),
                  train['target_masks'][:n_train].astype('float32')],
        'dev': [dev['input_images'][:n_dev].astype('float32'),
                  dev['target_masks'][:n_dev].astype('float32')],
        'test': [test['input_images'][:n_test].astype('float32'),
                 test['target_masks'][:n_test].astype('float32')]
    }
    train.close()
    dev.close()
    test.close()

    # Rescale, normalize, add extra dim
    preprocess(Data)

    # Load ground-truth craters
    Craters = {
        'train': pd.HDFStore('%strain_craters.hdf5' % dir, 'r'),
        'dev': pd.HDFStore('%sdev_craters.hdf5' % dir, 'r'),
        'test': pd.HDFStore('%stest_craters.hdf5' % dir, 'r')
    }

    # Iterate over parameters
    for i in range(MP['N_runs']):
        train_and_test_model(Data, Craters, MP, i)

########################
if __name__ == '__main__':
    print('Keras version: {}'.format(keras_version))
    MP = {}

    # Model Parameters
    MP['dir'] = '/scratch/m/mhvk/czhu/moondata/final_data/'
    MP['dim'] = 256             #image width/height, assuming square images. Shouldn't change
    MP['lr'] = 0.0001           #learning rate
    MP['bs'] = 8                #batch size: smaller values = less memory but less accurate gradient estimate
    MP['epochs'] = 4            #number of epochs. 1 epoch = forward/back pass through all train data
    MP['n_train'] = 30000       #number of training samples, needs to be a multiple of batch size. Big memory hog. (30000)
    MP['n_dev'] = 1000         #number of examples to calculate recall on after each epoch. Expensive operation. (1000)
    MP['n_test'] = 5000         #number of examples to calculate recall on after training. Expensive operation. (5000)
    MP['save_models'] = 1       #save keras models upon training completion
    MP['save_dir'] = 'models/HEAD_final.h5'

    # Model Parameters (to potentially iterate over, keep in lists)
    MP['N_runs'] = 1
    MP['filter_length'] = [3]
    MP['n_filters'] = [112]
    MP['init'] = ['he_normal']                      #See unet model. Initialization of weights.
    MP['lambda'] = [1e-6]
    MP['dropout'] = [0.15]

    # Example for iterating over parameters
#    MP['N_runs'] = 2
#    MP['lambda']=[1e-4,1e-4]              #regularization

    # Run models
    get_models(MP)
