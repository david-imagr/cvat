
# Copyright (C) 2018 Intel Corporation
#
# SPDX-License-Identifier: MIT

from django.http import HttpResponse, JsonResponse, HttpResponseBadRequest, QueryDict
from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import render
from rules.contrib.views import permission_required, objectgetter
from cvat.apps.authentication.decorators import login_required
from cvat.apps.engine.models import Task as TaskModel
from cvat.apps.engine import annotation, task


import django_rq
import fnmatch
import logging
import json
import os
import rq

import tensorflow as tf
import numpy as np

from PIL import Image
from cvat.apps.engine.log import slogger
import random

def load_image_into_numpy(image):
    (im_width, im_height) = image.size
    #return np.array(image.getdata()).reshape((im_height, im_width, 3)).astype(np.uint8)
    return np.asarray(image)


def run_inference_engine_annotation(image_list, labels_mapping, treshold):
    from cvat.apps.auto_annotation.inference_engine import make_plugin, make_network

    def _normalize_box(box, w, h, dw, dh):
        xmin = min(int(box[0] * dw * w), w)
        ymin = min(int(box[1] * dh * h), h)
        xmax = min(int(box[2] * dw * w), w)
        ymax = min(int(box[3] * dh * h), h)
        return xmin, ymin, xmax, ymax

    result = {}
    MODEL_PATH = os.environ.get('TF_ANNOTATION_MODEL_PATH')
    if MODEL_PATH is None:
        raise OSError('Model path env not found in the system.')

    plugin = make_plugin()
    network = make_network('{}.xml'.format(MODEL_PATH), '{}.bin'.format(MODEL_PATH))
    input_blob_name = next(iter(network.inputs))
    output_blob_name = next(iter(network.outputs))
    executable_network = plugin.load(network=network)
    job = rq.get_current_job()

    del network

    try:
        for image_num, im_name in enumerate(image_list):

            job.refresh()
            if 'cancel' in job.meta:
                del job.meta['cancel']
                job.save()
                return None
            job.meta['progress'] = image_num * 100 / len(image_list)
            job.save_meta()

            image = Image.open(im_name)
            width, height = image.size
            image.thumbnail((600, 600), Image.ANTIALIAS)
            dwidth, dheight = 600 / image.size[0], 600 / image.size[1]
            image = image.crop((0, 0, 600, 600))
            image_np = load_image_into_numpy(image)
            image_np = np.transpose(image_np, (2, 0, 1))
            prediction = executable_network.infer(inputs={input_blob_name: image_np[np.newaxis, ...]})[output_blob_name][0][0]
            for obj in prediction:
                obj_class = int(obj[1])
                obj_value = obj[2]
                if obj_class and obj_class in labels_mapping and obj_value >= treshold:
                    label = labels_mapping[obj_class]
                    if label not in result:
                        result[label] = []
                    xmin, ymin, xmax, ymax = _normalize_box(obj[3:7], width, height, dwidth, dheight)
                    result[label].append([image_num, xmin, ymin, xmax, ymax])
    finally:
        del executable_network
        del plugin

    return result


def run_tensorflow_annotation(image_list, labels_mapping, treshold):
    def _normalize_box(box, w, h):
        slogger.glob.info("BUG BOX : {}".format(box))
        xmin = int(box[1] * w)
        ymin = int(box[0] * h)
        xmax = int(box[3] * w)
        ymax = int(box[2] * h)
        return xmin, ymin, xmax, ymax
    import cv2
    def _convert_mask_to_polygon(img, mask, box):
        """mask_resized = cv2.resize(mask, (bb_width, bb_height))xmin, ymin, xmax, ymax ="""
        mask_temp = np.zeros((img.shape[0], img.shape[1]))
        bb_width = box[2]-box[0]
        bb_height = box[3]-box[1]
        mask_resized = cv2.resize(mask, (bb_width, bb_height))
        mask_resized = cv2.blur(mask_resized,(10,10))
        mask_resized[mask_resized>0.5] = 1

        mask_temp[ymin:ymax, xmin:xmax] = mask_resized
        #cv2.normalize(mask_temp,mask_temp,0,255,cv2.NORM_MINMAX)
        slogger.glob.info("Mask shape : {}".format(mask_temp.shape))

        contour = cv2.findContours(mask_temp.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_TC89_KCOS)[0]
        contours = max(contour, key=lambda arr: arr.size)
        contours = np.squeeze(contours)


        slogger.glob.info("Contours : {}".format(contours))
        return contours




    result = {}
    model_path = os.environ.get('TF_ANNOTATION_MODEL_PATH')
    if model_path is None:
        raise OSError('Model path env not found in the system.')
    job = rq.get_current_job()

    detection_graph = tf.Graph()
    with detection_graph.as_default():
        od_graph_def = tf.GraphDef()
        with tf.gfile.GFile(model_path + '.pb', 'rb') as fid:
            serialized_graph = fid.read()
            od_graph_def.ParseFromString(serialized_graph)
            tf.import_graph_def(od_graph_def, name='')

        try:
            config = tf.ConfigProto()
            config.gpu_options.allow_growth=True
            sess = tf.Session(graph=detection_graph, config=config)
            for image_num, image_path in enumerate(image_list):

                job.refresh()
                if 'cancel' in job.meta:
                    del job.meta['cancel']
                    job.save()
                    return None
                job.meta['progress'] = image_num * 100 / len(image_list)
                job.save_meta()

                image = Image.open(image_path)
                image_original_np = load_image_into_numpy(image)
                width, height = image.size
                #if width > 1920 or height > 1080:
                image = image.resize((width // 2, height // 2), Image.ANTIALIAS)
                image_np = load_image_into_numpy(image)
                image_np_expanded = np.expand_dims(image_np, axis=0)

                # TODO : add variable to determine if box or polygon
                use_polygon = True 


                image_tensor = detection_graph.get_tensor_by_name('image_tensor:0')
                boxes = detection_graph.get_tensor_by_name('detection_boxes:0')
                scores = detection_graph.get_tensor_by_name('detection_scores:0')
                classes = detection_graph.get_tensor_by_name('detection_classes:0')
                num_detections = detection_graph.get_tensor_by_name('num_detections:0')

                if use_polygon == True:
                    masks = detection_graph.get_tensor_by_name('detection_masks:0')
                    (boxes, scores, classes, masks, num_detections) = sess.run([boxes, scores, classes, masks, num_detections], feed_dict={image_tensor: image_np_expanded})
                else:
                    (boxes, scores, classes, num_detections) = sess.run([boxes, scores, classes, num_detections], feed_dict={image_tensor: image_np_expanded})



                slogger.glob.info("score {}".format(scores))

                n_points = 100

                for i in range(len(classes[0])):
                    if classes[0][i] in labels_mapping.keys():
                        if scores[0][i] >= treshold:
                            slogger.glob.info("boxes : {}".format(boxes[0]))
                            xmin, ymin, xmax, ymax = _normalize_box(boxes[0][i], width, height)
                            label = labels_mapping[classes[0][i]]
                            if label not in result:
                                result[label] = []

                            if use_polygon == True:
                                contours = _convert_mask_to_polygon(image_original_np, masks[0][i], [xmin,ymin,xmax,ymax])
                                if label not in result:
                                    result[label] = []
                                contour_string = ""
                                step_size = max(1, int(len(contours)/float(n_points)))
                                contour_clean = [contours[_c] for _c in range(0, len(contours), step_size)]

                                for point in contour_clean:

                                    contour_string += "{},{} ".format(int(point[0]), int(point[1]))

                                array_output = [image_num, contour_string]

                                slogger.glob.info("Output array : {}".format(array_output))
                            
                            
                            if use_polygon is False:
                                #slogger.glob.info("output {} ".format([image_num, xmin, ymin, xmax, ymax]))
                                #slogger.glob.info("label {}".format(label))
                                #slogger.glob.info("results key {}".format(result.keys()))
                                #result[label].append([image_num, xmin, ymin, xmax, ymax])
                                # Move bounding box to polygon representation
                                boxes_out = [
                                        (xmin, ymin),
                                        (xmax, ymin),
                                        (xmax, ymax),
                                        (xmin, ymax)
                                        ]
                                contour_string = ""

                                for p in boxes_out:
                                    contour_string += "{},{} ".format(int(p[0]), int(p[1]))

                                array_output = [image_num, contour_string]
                                result[label].append(array_output)
                                slogger.glob.info("box contour : {}".format(array_output))

                                



                            else:
                                result[label].append(array_output)
        finally:
            sess.close()
            del sess
    return result


def make_image_list(path_to_data):
    def get_image_key(item):
        return int(os.path.splitext(os.path.basename(item))[0])

    image_list = []
    for root, dirnames, filenames in os.walk(path_to_data):
        for filename in fnmatch.filter(filenames, '*.jpg'):
                image_list.append(os.path.join(root, filename))

    image_list.sort(key=get_image_key)
    return image_list


def convert_to_cvat_format(data):
    def create_anno_container():
        return {
            "boxes": [],
            "polygons": [],
            "polylines": [],
            "points": [],
            "box_paths": [],
            "polygon_paths": [],
            "polyline_paths": [],
            "points_paths": [],
        }

    result = {
        'create': create_anno_container(),
        'update': create_anno_container(),
        'delete': create_anno_container(),
    }

    for label in data:
        boxes = data[label]

        # TODO Add args
        use_polygon = True 
        if use_polygon is False:
            for box in boxes:
                result['create']['boxes'].append({
                    "label_id": label,
                    "frame": box[0],
                    "xtl": box[1],
                    "ytl": box[2],
                    "xbr": box[3],
                    "ybr": box[4],
                    "z_order": 0,
                    "group_id": 0,
                    "occluded": False,
                    "attributes": [],
                    "id": -1,
                })
        else:
            for box in boxes:
                result['create']['polygons'].append({
                    "label_id": label,
                    "frame": box[0],
                    "points": box[1],
                    "z_order": 0,
                    "group_id": 0,
                    "occluded": False,
                    "attributes": [],
                    "id": -1,
                })


    return result

def create_thread(tid, labels_mapping):
    try:
        TRESHOLD = 0.55
        # Init rq job
        job = rq.get_current_job()
        job.meta['progress'] = 0
        job.save_meta()
        # Get job indexes and segment length
        db_task = TaskModel.objects.get(pk=tid)
        # Get image list
        image_list = make_image_list(db_task.get_data_dirname())

        # Run auto annotation by tf
        result = None
        if os.environ.get('CUDA_SUPPORT') == 'yes' or os.environ.get('OPENVINO_TOOLKIT') != 'yes':
            slogger.glob.info("tf annotation with tensorflow framework for task {}".format(tid))
            result = run_tensorflow_annotation(image_list, labels_mapping, TRESHOLD)
        else:
            slogger.glob.info('tf annotation with openvino toolkit for task {}'.format(tid))
            result = run_inference_engine_annotation(image_list, labels_mapping, TRESHOLD)

        if result is None:
            slogger.glob.info('tf annotation for task {} canceled by user'.format(tid))
            return

        # Modify data format and save
        result = convert_to_cvat_format(result)
        annotation.clear_task(tid)
        annotation.save_task(tid, result)
        slogger.glob.info('tf annotation for task {} done'.format(tid))
    except:
        try:
            slogger.task[tid].exception('exception was occured during tf annotation of the task', exc_info=True)
        except:
            slogger.glob.exception('exception was occured during tf annotation of the task {}'.format(tid), exc_into=True)

@login_required
def get_meta_info(request):
    try:
        queue = django_rq.get_queue('low')
        tids = json.loads(request.body.decode('utf-8'))
        result = {}
        for tid in tids:
            job = queue.fetch_job('tf_annotation.create/{}'.format(tid))
            if job is not None:
                result[tid] = {
                    "active": job.is_queued or job.is_started,
                    "success": not job.is_failed
                }

        return JsonResponse(result)
    except Exception as ex:
        slogger.glob.exception('exception was occured during tf meta request', exc_into=True)
        return HttpResponseBadRequest(str(ex))


@login_required
@permission_required(perm=['engine.task.change'],
    fn=objectgetter(TaskModel, 'tid'), raise_exception=True)
def create(request, tid):
    slogger.glob.info('tf annotation create request for task {}'.format(tid))
    try:
        db_task = TaskModel.objects.get(pk=tid)
        queue = django_rq.get_queue('low')
        job = queue.fetch_job('tf_annotation.create/{}'.format(tid))
        if job is not None and (job.is_started or job.is_queued):
            raise Exception("The process is already running")

        db_labels = db_task.label_set.prefetch_related('attributespec_set').all()
        db_labels = {db_label.id:db_label.name for db_label in db_labels}

        tf_annotation_labels = {"object":1}



        labels_mapping = {}
        for key, labels in db_labels.items():
            if labels in tf_annotation_labels.keys():
                labels_mapping[tf_annotation_labels[labels]] = key

        if not len(labels_mapping.values()):
            raise Exception('No labels found for tf annotation')

        # Run tf annotation job
        queue.enqueue_call(func=create_thread,
            args=(tid, labels_mapping),
            job_id='tf_annotation.create/{}'.format(tid),
            timeout=604800)     # 7 days

        slogger.task[tid].info('tensorflow annotation job enqueued with labels {}'.format(labels_mapping))

    except Exception as ex:
        try:
            slogger.task[tid].exception("exception was occured during tensorflow annotation request", exc_info=True)
        except:
            pass
        return HttpResponseBadRequest(str(ex))

    return HttpResponse()

@login_required
@permission_required(perm=['engine.task.access'],
    fn=objectgetter(TaskModel, 'tid'), raise_exception=True)
def check(request, tid):
    try:
        queue = django_rq.get_queue('low')
        job = queue.fetch_job('tf_annotation.create/{}'.format(tid))
        if job is not None and 'cancel' in job.meta:
            return JsonResponse({'status': 'finished'})
        data = {}
        if job is None:
            data['status'] = 'unknown'
        elif job.is_queued:
            data['status'] = 'queued'
        elif job.is_started:
            data['status'] = 'started'
            data['progress'] = job.meta['progress']
        elif job.is_finished:
            data['status'] = 'finished'
            job.delete()
        else:
            data['status'] = 'failed'
            job.delete()

    except Exception:
        data['status'] = 'unknown'

    return JsonResponse(data)


@login_required
@permission_required(perm=['engine.task.change'],
    fn=objectgetter(TaskModel, 'tid'), raise_exception=True)
def cancel(request, tid):
    try:
        queue = django_rq.get_queue('low')
        job = queue.fetch_job('tf_annotation.create/{}'.format(tid))
        if job is None or job.is_finished or job.is_failed:
            raise Exception('Task is not being annotated currently')
        elif 'cancel' not in job.meta:
            job.meta['cancel'] = True
            job.save()

    except Exception as ex:
        try:
            slogger.task[tid].exception("cannot cancel tensorflow annotation for task #{}".format(tid), exc_info=True)
        except:
            pass
        return HttpResponseBadRequest(str(ex))

    return HttpResponse()
