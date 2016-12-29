from __future__ import print_function
import tensorflow as tf
import numpy as np
import scipy.misc
import os
from glob import glob

import TensorflowUtils as utils
from six.moves import xrange

FLAGS = tf.flags.FLAGS
tf.flags.DEFINE_string("data_dir", "/data/vllab1/dataset/CITYSCAPES/CITY/human_image", "path to dataset")
tf.flags.DEFINE_string("label_dir", "/data/vllab1/dataset/CITYSCAPES/CITY/human_mask", "path to annotation")
tf.flags.DEFINE_string("model_dir", "/data/vllab1/checkpoint/", "Path to vgg model mat")
tf.flags.DEFINE_string("logs_dir", "/data/vllab1/checkpoint/FCN/heatmap_new_loss/", "path to logs directory")

tf.flags.DEFINE_integer("batch_size", "4", "batch size for training")
tf.flags.DEFINE_float("learning_rate", "1e-4", "Learning rate for Adam Optimizer")
tf.flags.DEFINE_bool('debug', "False", "Debug mode: True/ False")
tf.flags.DEFINE_string('mode', "train", "Mode train/ test/ visualize")

MODEL_URL = 'http://www.vlfeat.org/matconvnet/models/beta16/imagenet-vgg-verydeep-19.mat'

MAX_ITERATION = int(1e5 + 1)
NUM_OF_CLASSESS = 2
SAMPLE_SHAPE = (2, 2)
IMAGE_SIZE_h = 256
IMAGE_SIZE_w = 512


def vgg_net(weights, image):
    layers = (
        'conv1_1', 'relu1_1', 'conv1_2', 'relu1_2', 'pool1',

        'conv2_1', 'relu2_1', 'conv2_2', 'relu2_2', 'pool2',

        'conv3_1', 'relu3_1', 'conv3_2', 'relu3_2', 'conv3_3',
        'relu3_3', 'conv3_4', 'relu3_4', 'pool3',

        'conv4_1', 'relu4_1', 'conv4_2', 'relu4_2', 'conv4_3',
        'relu4_3', 'conv4_4', 'relu4_4', 'pool4',

        'conv5_1', 'relu5_1', 'conv5_2', 'relu5_2', 'conv5_3',
        'relu5_3', 'conv5_4', 'relu5_4'
    )

    net = {}
    current = image
    for i, name in enumerate(layers):
        kind = name[:4]
        if kind == 'conv':
            kernels, bias = weights[i][0][0][0][0]
            # matconvnet: weights are [width, height, in_channels, out_channels]
            # tensorflow: weights are [height, width, in_channels, out_channels]
            kernels = utils.get_variable(np.transpose(kernels, (1, 0, 2, 3)), name=name + "_w")
            bias = utils.get_variable(bias.reshape(-1), name=name + "_b")
            current = utils.conv2d_basic(current, kernels, bias)
        elif kind == 'relu':
            current = tf.nn.relu(current, name=name)
            if FLAGS.debug:
                utils.add_activation_summary(current)
        elif kind == 'pool':
            current = utils.avg_pool_2x2(current)
        net[name] = current

    return net


def inference(image, keep_prob):
    """
    Semantic segmentation network definition
    :param image: input image. Should have values in range 0-255
    :param keep_prob:
    :return:
    """
    print("setting up vgg initialized conv layers ...")
    #model_data = utils.get_model_data(FLAGS.model_dir, MODEL_URL)
    model_data = scipy.io.loadmat(os.path.join(FLAGS.model_dir, 'imagenet-vgg-verydeep-19.mat'))

    mean = model_data['normalization'][0][0][0]
    mean_pixel = np.mean(mean, axis=(0, 1))

    weights = np.squeeze(model_data['layers'])

    processed_image = utils.process_image(image, mean_pixel)

    with tf.variable_scope("inference"):
        image_net = vgg_net(weights, processed_image)
        conv_final_layer = image_net["conv5_3"]

        pool5 = utils.max_pool_2x2(conv_final_layer)

        W6 = utils.weight_variable([7, 7, 512, 4096], name="W6")
        b6 = utils.bias_variable([4096], name="b6")
        conv6 = utils.conv2d_basic(pool5, W6, b6)
        relu6 = tf.nn.relu(conv6, name="relu6")
        if FLAGS.debug:
            utils.add_activation_summary(relu6)
        relu_dropout6 = tf.nn.dropout(relu6, keep_prob=keep_prob)

        W7 = utils.weight_variable([1, 1, 4096, 4096], name="W7")
        b7 = utils.bias_variable([4096], name="b7")
        conv7 = utils.conv2d_basic(relu_dropout6, W7, b7)
        relu7 = tf.nn.relu(conv7, name="relu7")
        if FLAGS.debug:
            utils.add_activation_summary(relu7)
        relu_dropout7 = tf.nn.dropout(relu7, keep_prob=keep_prob)

        W8 = utils.weight_variable([1, 1, 4096, NUM_OF_CLASSESS], name="W8")
        b8 = utils.bias_variable([NUM_OF_CLASSESS], name="b8")
        conv8 = utils.conv2d_basic(relu_dropout7, W8, b8)
        # annotation_pred1 = tf.argmax(conv8, dimension=3, name="prediction1")

        # now to upscale to actual image size
        deconv_shape1 = image_net["pool4"].get_shape()
        W_t1 = utils.weight_variable([4, 4, deconv_shape1[3].value, NUM_OF_CLASSESS], name="W_t1")
        b_t1 = utils.bias_variable([deconv_shape1[3].value], name="b_t1")
        conv_t1 = utils.conv2d_transpose_strided(conv8, W_t1, b_t1, output_shape=tf.shape(image_net["pool4"]))
        fuse_1 = tf.add(conv_t1, image_net["pool4"], name="fuse_1")

        deconv_shape2 = image_net["pool3"].get_shape()
        W_t2 = utils.weight_variable([4, 4, deconv_shape2[3].value, deconv_shape1[3].value], name="W_t2")
        b_t2 = utils.bias_variable([deconv_shape2[3].value], name="b_t2")
        conv_t2 = utils.conv2d_transpose_strided(fuse_1, W_t2, b_t2, output_shape=tf.shape(image_net["pool3"]))
        fuse_2 = tf.add(conv_t2, image_net["pool3"], name="fuse_2")

        shape = tf.shape(image)
        deconv_shape3 = tf.pack([shape[0], shape[1], shape[2], NUM_OF_CLASSESS])
        W_t3 = utils.weight_variable([16, 16, NUM_OF_CLASSESS, deconv_shape2[3].value], name="W_t3")
        b_t3 = utils.bias_variable([NUM_OF_CLASSESS], name="b_t3")
        conv_t3 = utils.conv2d_transpose_strided(fuse_2, W_t3, b_t3, output_shape=deconv_shape3, stride=8)

        annotation_pred = tf.argmax(conv_t3, dimension=3, name="prediction")

    return tf.expand_dims(annotation_pred, dim=3), conv_t3


def train(loss_val, var_list):
    optimizer = tf.train.AdamOptimizer(FLAGS.learning_rate)
    grads = optimizer.compute_gradients(loss_val, var_list=var_list)
    if FLAGS.debug:
        # print(len(var_list))
        for grad, var in grads:
            utils.add_gradient_summary(grad, var)
    return optimizer.apply_gradients(grads)


def main(argv=None):
    if not os.path.exists(FLAGS.logs_dir):
        os.makedirs(FLAGS.logs_dir)

    keep_probability = tf.placeholder(tf.float32, name="keep_probabilty")
    image = tf.placeholder(tf.float32, shape=[None, IMAGE_SIZE_h, IMAGE_SIZE_w, 3], name="input_image")
    annotation = tf.placeholder(tf.int32, shape=[None, IMAGE_SIZE_h, IMAGE_SIZE_w, 2], name="annotation")

    pred_annotation, logits = inference(image, keep_probability)
    # tf.image_summary("input_image", image, max_images=2)
    # tf.image_summary("ground_truth", tf.cast(annotation, tf.uint8), max_images=2)
    # tf.image_summary("pred_annotation", tf.cast(pred_annotation, tf.uint8), max_images=2)
    loss = tf.reduce_mean((tf.nn.sparse_softmax_cross_entropy_with_logits(logits, annotation[:, :, :, 1])))
    loss_bg = tf.reduce_mean((tf.nn.softmax_cross_entropy_with_logits(logits[:, :, :, 0], annotation[:, :, :, 0])))
    loss_fg = tf.reduce_mean((tf.nn.softmax_cross_entropy_with_logits(logits[:, :, :, 1], annotation[:, :, :, 1])))
    loss_new = loss_bg + 3 * loss_fg
    # tf.scalar_summary("entropy", loss)

    trainable_var = tf.trainable_variables()
    if FLAGS.debug:
        for var in trainable_var:
            utils.add_to_regularization_and_summary(var)

    # TODO new loss
    train_op = train(loss, trainable_var)

    # print("Setting up summary op...")
    # summary_op = tf.merge_all_summaries()

    '''
    print("Setting up image reader...")
    train_records, valid_records = scene_parsing.read_dataset(FLAGS.data_dir)
    print(len(train_records))t
    print(len(valid_records))

    print("Setting up dataset reader")
    image_options = {'resize': True, 'resize_size': IMAGE_SIZE}
    if FLAGS.mode == 'train':
        train_dataset_reader = dataset.BatchDatset(train_records, image_options)
    validation_dataset_reader = dataset.BatchDatset(valid_records, image_options)
    '''

    # TODO next batch file name suffle
    data = sorted(glob(os.path.join(FLAGS.data_dir, "*.png")))
    label = sorted(glob(os.path.join(FLAGS.label_dir, "*.png")))
    train_size = len(data)

    gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=1)
    sess = tf.Session(config=tf.ConfigProto(gpu_options=gpu_options))

    print("Setting up Saver...")
    saver = tf.train.Saver()
    # summary_writer = tf.train.SummaryWriter(FLAGS.logs_dir, sess.graph)

    sess.run(tf.global_variables_initializer())
    ckpt = tf.train.get_checkpoint_state(FLAGS.logs_dir)
    if ckpt and ckpt.model_checkpoint_path:
        saver.restore(sess, ckpt.model_checkpoint_path)
        print("Model restored...{}".format(ckpt.model_checkpoint_path))

    if FLAGS.mode == "train":
        for epoch in xrange(MAX_ITERATION):
            for batch_itr in xrange(train_size / FLAGS.batch_size):
                print(epoch, batch_itr)
                train_images_name = data[batch_itr * FLAGS.batch_size:(batch_itr + 1) * FLAGS.batch_size]
                train_annotations_name = label[batch_itr * FLAGS.batch_size:(batch_itr + 1) * FLAGS.batch_size]

                train_images = [scipy.misc.imread(train_image_name).astype(np.uint8)
                                        for train_image_name in train_images_name]

                # Is this the normal way?
                train_annotations = [np.dstack((1 - scipy.misc.imread(train_annotation_name).astype(np.uint8) / 255,
                                                scipy.misc.imread(train_annotation_name).astype(np.uint8) / 255))
                                             for train_annotation_name in train_annotations_name]

                # train_images, train_annotations = traitn_dataset_reader.next_batch(FLAGS.batch_size)
                feed_dict = {image: train_images, annotation: train_annotations, keep_probability: 0.85}

                sess.run(train_op, feed_dict=feed_dict)

                step = epoch * train_size + batch_itr
                if step % 50 == 0:
                    # train_loss, summary_str = sess.run([loss, summary_op], feed_dict=feed_dict)
                    train_loss_new, train_loss, train_loss_bg, train_loss_fg = sess.run([loss_new, loss, loss_bg, loss_fg], feed_dict=feed_dict)
                    print("Step: %d, Train_loss_new:%g, Train_loss:%g, Train_loss_bg:%g, Train_loss_fg:%g" %
                          (step, train_loss_new, train_loss, train_loss_bg, train_loss_fg))
                    # summary_writer.add_summary(summary_str, step)

                if step % 200 == 0:
                    # train_loss, summary_str = sess.run([loss, summary_op], feed_dict=feed_dict)
                    scipy.misc.imsave('logs/{:d}_image.png'.format(step), utils.merge(
                        np.array(train_images), SAMPLE_SHAPE))
                    scipy.misc.imsave('logs/{:d}_gt_bg.png'.format(step), utils.heatmap_visualize(utils.merge(
                        np.array(train_annotations)[:, :, :, 0], SAMPLE_SHAPE, is_gray=True)))
                    scipy.misc.imsave('logs/{:d}_gt_fg.png'.format(step), utils.heatmap_visualize(utils.merge(
                        np.array(train_annotations)[:, :, :, 1], SAMPLE_SHAPE, is_gray=True)))

                    pred = sess.run(pred_annotation, feed_dict={image: train_images, annotation: train_annotations,
                                                                keep_probability: 1.0})

                    pred = np.squeeze(pred, axis=3)
                    scipy.misc.imsave('logs/{:d}_pred.png'.format(step), utils.heatmap_visualize(
                        utils.merge(pred, SAMPLE_SHAPE, is_gray=True)))
                    # summary_writer.add_summary(summary_str, step)

                if step % 600 == 0:
                    # valid_images, valid_annotations = validation_dataset_reader.next_batch(FLAGS.batch_size)
                    # valid_loss = sess.run(loss, feed_dict={image: valid_images, annotation: valid_annotations,
                    #                                       keep_probability: 1.0})
                    # print("%s ---> Validation_loss: %g" % (datetime.datetime.now(), valid_loss))
                    print('checkpoint')
                    saver.save(sess, FLAGS.logs_dir + "model.ckpt", step)

    elif FLAGS.mode == "visualize":
        # valid_images, valid_annotations = validation_dataset_reader.get_random_batch(FLAGS.batch_size)
        train_images = scipy.misc.imread('/data/vllab1/Github/streetview_synthesize/aachen_000009_000019_leftImg8bit.png').astype(
            np.uint8)
        train_annotations = scipy.misc.imread('/data/vllab1/Github/streetview_synthesize/aachen_000009_000019_gtFine_labelIds.png').astype(
            np.uint8)
        valid_images = [train_images]
        #valid_annotations = [np.expand_dims(train_annotations, axis=3)]
        valid_annotations = [train_annotations]

        pred = sess.run(pred_annotation, feed_dict={image: valid_images, annotation: valid_annotations,
                                                    keep_probability: 1.0})
        #valid_annotations = [np.squeeze(valid_annotations, axis=3)]
        pred = np.squeeze(pred, axis=3)

        for itr in range(FLAGS.batch_size):
            scipy.misc.imsave("inp_" + str(5 + itr) + '.png', valid_images[itr].astype(np.uint8))
            scipy.misc.imsave("gt_" + str(5 + itr) + '.png', valid_annotations[itr].astype(np.uint8))
            scipy.misc.imsave("pred_" + str(5 + itr) + '.png', pred[itr].astype(np.uint8) * 255)
            #utils.save_image(pred[itr].astype(np.uint8), FLAGS.logs_dir, name="pred_" + str(5 + itr))
            print("Saved image: %d" % itr)


if __name__ == "__main__":
    tf.app.run()
