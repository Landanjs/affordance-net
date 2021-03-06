#!/usr/bin/env python
"""
See README.md for installation instructions before running.
Demo script to perform affordace detection from images
"""

from os import path
import sys

# Initialize python paths
this_dir = path.dirname(__file__)

# Add caffe to PYTHONPATH
caffe_path = path.join(this_dir, '..', 'caffe-affordance-net', 'python')
if caffe_path not in sys.path:
    sys.path.append(caffe_path)

# Add lib to PYTHONPATH
lib_path = path.join(this_dir, '..', 'lib')
if lib_path not in sys.path:
    sys.path.append(lib_path)

from fast_rcnn.config import cfg
from fast_rcnn.test import im_detect2
from fast_rcnn.nms_wrapper import nms
from utils.timer import Timer
from utils.camera_to_marker import aruco_camPose
from utils.handy import write_pddl

import numpy as np
import os, cv2
import argparse
import caffe
import time
import subprocess

### ROS STUFF
import rospy

# Message type for publishing can be reused
from visualization_msgs.msg import Marker, MarkerArray
from geometry_msgs.msg import Pose, Point, PoseStamped
from sensor_msgs.msg import Image, PointField, PointCloud2
import sensor_msgs.point_cloud2 as pc2
from std_msgs import msg
import cv_bridge
import matplotlib.pyplot as plt
#from ros_image_io import ImageIO


# start ros node and imageio
rospy.init_node('AffordanceNet_Node')
pub_obj_pose_3D = rospy.Publisher("vs_obj_pose_3D", PoseStamped) # pose of object in camera frame
pub_point_cloud = rospy.Publisher('transformed_scene', PointCloud2)

#KINECT_FX = 525
#KINECT_FY = 525
#KINECT_CX = 319.5
#KINECT_CY = 239.5

KINECT_FX = 494.042
KINECT_FY = 490.682
KINECT_CX = 330.273
KINECT_CY = 247.443

CONF_THRESHOLD = 0.01
good_range = 0.005
    
# get current dir
cwd = os.getcwd()
root_path = os.path.abspath(os.path.join(cwd, os.pardir))  # get parent path
print 'AffordanceNet root folder: ', root_path
img_folder = cwd + '/img'

OBJ_CLASSES = ('__background__', 'bowl', 'tvm', 'pan', 'hammer', 'knife', 'cup', 'drill', 'racket', 'spatula', 'bottle')
OBJ_INDS = {'__background__' : 0, 'bowl' : 1, 'tvm' : 2, 'pan' : 3, 'hammer' : 4,
            'knife' : 5, 'cup' : 6, 'drill' : 7, 'racket' : 8, 'spatula' : 9, 'bottle' : 10}

ACTION_INDS = {'pickup' : 0, 'dropoff' : 1}

# Mask
background = [200, 222, 250]  
c1 = [0,0,205]   
c2 = [34,139,34] 
c3 = [192,192,128]   
c4 = [165,42,42]    
c5 = [128,64,128]   
c6 = [204,102,0]  
c7 = [184,134,11] 
c8 = [0,153,153]
c9 = [0,134,141]
c10 = [184,0,141] 
c11 = [184,134,0] 
c12 = [184,134,223]
label_colours = np.array([background, c1, c2, c3, c4, c5, c6, c7, c8, c9, c10, c11, c12])

# Object
col0 = [0, 0, 0]
col1 = [0, 255, 255]
col2 = [255, 0, 255]
col3 = [0, 125, 255]
col4 = [55, 125, 0]
col5 = [255, 50, 75]
col6 = [100, 100, 50]
col7 = [25, 234, 54]
col8 = [156, 65, 15]
col9 = [215, 25, 155]
col10 = [25, 25, 155]

col_map = [col0, col1, col2, col3, col4, col5, col6, col7, col8, col9, col10]



def reset_mask_ids(mask, before_uni_ids):
    # reset ID mask values from [0, 1, 4] to [0, 1, 2] to resize later 
    counter = 0
    for id in before_uni_ids:
        mask[mask == id] = counter
        counter += 1
        
    return mask
    

    
def convert_mask_to_original_ids_manual(mask, original_uni_ids):
    #TODO: speed up!!!
    temp_mask = np.copy(mask) # create temp mask to do np.around()
    temp_mask = np.around(temp_mask, decimals=0)  # round 1.6 -> 2., 1.1 -> 1.
    current_uni_ids = np.unique(temp_mask)
     
    out_mask = np.full(mask.shape, 0, 'float32')
     
    mh, mw = mask.shape
    for i in range(mh-1):
        for j in range(mw-1):
            for k in range(1, len(current_uni_ids)):
                if mask[i][j] > (current_uni_ids[k] - good_range) and mask[i][j] < (current_uni_ids[k] + good_range):  
                    out_mask[i][j] = original_uni_ids[k] 
                    #mask[i][j] = current_uni_ids[k]
           
#     const = 0.005
#     out_mask = original_uni_ids[(np.abs(mask - original_uni_ids[:,None,None]) < const).argmax(0)]
              
    #return mask
    return out_mask
        



def draw_arrow(image, p, q, color, arrow_magnitude, thickness, line_type, shift):
    # draw arrow tail
    cv2.line(image, p, q, color, thickness, line_type, shift)
    # calc angle of the arrow
    angle = np.arctan2(p[1]-q[1], p[0]-q[0])
    # starting point of first line of arrow head
    p = (int(q[0] + arrow_magnitude * np.cos(angle + np.pi/4)),
    int(q[1] + arrow_magnitude * np.sin(angle + np.pi/4)))
    # draw first half of arrow head
    cv2.line(image, p, q, color, thickness, line_type, shift)
    # starting point of second line of arrow head
    p = (int(q[0] + arrow_magnitude * np.cos(angle - np.pi/4)),
    int(q[1] + arrow_magnitude * np.sin(angle - np.pi/4)))
    # draw second half of arrow head
    cv2.line(image, p, q, color, thickness, line_type, shift)
    
def draw_reg_text(img, obj_info):
    #print 'tbd'
    
    obj_id = obj_info[0]
    cfd = obj_info[1]
    xmin = obj_info[2]
    ymin = obj_info[3]
    xmax = obj_info[4]
    ymax = obj_info[5]
    
    draw_arrow(img, (xmin, ymin), (xmax, ymin), col_map[obj_id], 0, 5, 8, 0)
    draw_arrow(img, (xmax, ymin), (xmax, ymax), col_map[obj_id], 0, 5, 8, 0)
    draw_arrow(img, (xmax, ymax), (xmin, ymax), col_map[obj_id], 0, 5, 8, 0)
    draw_arrow(img, (xmin, ymax), (xmin, ymin), col_map[obj_id], 0, 5, 8, 0)
    
    # put text
    txt_obj = OBJ_CLASSES[obj_id] + ' ' + str(cfd)
    cv2.putText(img, txt_obj, (xmin, ymin-5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 1) # draw with red
    #cv2.putText(img, txt_obj, (xmin, ymin), cv2.FONT_HERSHEY_SIMPLEX, 1, col_map[obj_id], 2)
    
#     # draw center
#     center_x = (xmax - xmin)/2 + xmin
#     center_y = (ymax - ymin)/2 + ymin
#     cv2.circle(img,(center_x, center_y), 3, (0, 255, 0), -1)
    
    return img



def visualize_mask_asus(im, rois_final, rois_class_score, rois_class_ind, masks, thresh):

    list_bboxes = []
    list_masks = []

    if rois_final.shape[0] == 0:
        print 'No object detection!'
        return list_bboxes, list_masks
    print(rois_class_score[:, -1])
    inds = np.where(rois_class_score[:, -1] >= thresh)[0]
    if len(inds) == 0:
        print 'No detected box with probality > thresh = ', thresh, '-- Choossing highest confidence bounding box.'
        inds = [np.argmax(rois_class_score)]  
        max_conf = np.max(rois_class_score)
        if max_conf < 0.001: 
            return list_bboxes, list_masks
            
    rois_final = rois_final[inds, :]
    rois_class_score = rois_class_score[inds,:]
    rois_class_ind = rois_class_ind[inds,:]
    
    # get mask
    masks = masks[inds, :, :, :]
    
    im_width = im.shape[1]
    im_height = im.shape[0]
    
    # transpose
    im = im[:, :, (2, 1, 0)]

    num_boxes = rois_final.shape[0]
    
    for i in xrange(0, num_boxes):
        
        curr_mask = np.full((im_height, im_width), 0.0, 'float') # convert to int later
            
        class_id = int(rois_class_ind[i,0])
    
        bbox = rois_final[i, 1:5]
        score = rois_class_score[i,0]
        
        if cfg.TEST.MASK_REG:

            x1 = int(round(bbox[0]))
            y1 = int(round(bbox[1]))
            x2 = int(round(bbox[2]))
            y2 = int(round(bbox[3]))

            x1 = np.min((im_width - 1, np.max((0, x1))))
            y1 = np.min((im_height - 1, np.max((0, y1))))
            x2 = np.min((im_width - 1, np.max((0, x2))))
            y2 = np.min((im_height - 1, np.max((0, y2))))
            
            cur_box = [class_id, score, x1, y1, x2, y2]
            list_bboxes.append(cur_box)
            
            h = y2 - y1
            w = x2 - x1
                        
            mask = masks[i, :, :, :]
            mask = np.argmax(mask, axis=0)
            
            original_uni_ids = np.unique(mask)
            
            # sort before_uni_ids and reset [0, 1, 7] to [0, 1, 2]
            original_uni_ids.sort()
            mask = reset_mask_ids(mask, original_uni_ids)
            
            mask = cv2.resize(mask.astype('float'), (int(w), int(h)), interpolation=cv2.INTER_LINEAR)
            #mask = convert_mask_to_original_ids(mask, original_uni_ids)
            mask = convert_mask_to_original_ids_manual(mask, original_uni_ids)
            
            # for mult masks
            curr_mask[y1:y2, x1:x2] = mask 
            
            # visualize each mask
            curr_mask = curr_mask.astype('uint8')
            list_masks.append(curr_mask)
            color_curr_mask = label_colours.take(curr_mask, axis=0).astype('uint8')
            cv2.imshow('Mask' + str(i), color_curr_mask)
            cv2.waitKey()
            #cv2.imwrite('mask'+str(i)+'.jpg', color_curr_mask)
            

    img_org = im.copy()
    for ab in list_bboxes:
        print 'box: ', ab
        img_out = draw_reg_text(img_org, ab)
    
    cv2.imshow('Obj Detection', img_out)
    #cv2.imwrite('obj_detction.jpg', img_out)
    #cv2.waitKey(0)
    
    return list_bboxes, list_masks


def get_list_centroid(current_mask, obj_id):
    list_uni_ids = list(np.unique(current_mask))
    list_uni_ids.remove(0) ## remove background id
    
    list_centroid = []  ## each row is: obj_id, mask_id, xmean, ymean
    for val in list_uni_ids:
        inds = np.where(current_mask == val) 
        x_index = inds[1]
        y_index = inds[0]
        
        xmean = int(np.mean(x_index))
        ymean = int(np.mean(y_index))
        
        cur_centroid = [obj_id, val, xmean, ymean]
        list_centroid.append(cur_centroid)
        
    return list_centroid   

def convert_bbox_to_centroid(list_boxes, list_masks):
    assert len(list_boxes) == len(list_masks), 'ERROR: len(list_boxes) and len(list_masks) must be equal'
    list_final = []
    for i in range(len(list_boxes)):
        obj_id = list_boxes[i][0] 
        list_centroids = get_list_centroid(list_masks[i], obj_id)  # return [[obj_id, mask_id, xmean, ymean]]
        if len(list_centroids) > 0:
            for l in list_centroids:
                list_final.append(l)
    return list_final


def select_object_and_aff(list_obj_centroids, obj_id, aff_id):
    # select the first object with object id and aff id
    selected_obj_aff = []
    for l in list_obj_centroids:
        if len(l) > 0:
            if l[0] == obj_id and l[1] == aff_id:
                selected_obj_aff.append(l)
                break
    
    selected_obj_aff = np.squeeze(selected_obj_aff, 0)
    return selected_obj_aff  

    
def project_to_3D(width_x, height_y, depth):
    X = (width_x - KINECT_CX) * depth / KINECT_FX
    Y = (height_y - KINECT_CY) * depth / KINECT_FY
    Z = depth
    p3D = [X, Y, Z]
    
    return p3D

def run_affordance_net_asus(net, im):

    # Detect all object classes and regress object bounds
    timer = Timer()
    timer.tic()
    if cfg.TEST.MASK_REG:
        rois_final, rois_class_score, rois_class_ind, masks, scores, boxes = im_detect2(net, im)
    else:
        1
    timer.toc()
    print ('Detection took {:.3f}s for '
           '{:d} object proposals').format(timer.total_time, rois_final.shape[0])
    
    # Visualize detections for each class
    return visualize_mask_asus(im, rois_final, rois_class_score, rois_class_ind, masks, thresh=CONF_THRESHOLD)


def parse_args():
    """Parse input arguments."""
    parser = argparse.ArgumentParser(description='AffordanceNet demo')
    parser.add_argument('--gpu', dest='gpu_id', help='GPU device id to use [0]',
                        default=0, type=int)
    parser.add_argument('--cpu', dest='cpu_mode',
                        help='Use CPU mode (overrides --gpu)',
                        action='store_true')
    parser.add_argument('--sim', help='Use simulated data',
                        action='store_true')

    args = parser.parse_args()

    return args

    

if __name__ == '__main__':
    cfg.TEST.HAS_RPN = True  # Use RPN for proposals

    args = parse_args()    

    prototxt = root_path + '/models/pascal_voc/VGG16/faster_rcnn_end2end/test.prototxt'
    caffemodel = root_path + '/pretrained/AffordanceNet_200K.caffemodel'   
    
    if not os.path.isfile(caffemodel):
        raise IOError(('{:s} not found.\n').format(caffemodel))

    if args.cpu_mode:
        caffe.set_mode_cpu()
    else:
        caffe.set_mode_gpu()
        caffe.set_device(args.gpu_id)
        cfg.GPU_ID = args.gpu_id
    
    # load network
    net = caffe.Net(prototxt, caffemodel, caffe.TEST)
    print '\n\nLoaded network {:s}'.format(caffemodel)

    # Load Kinect data
    print("Waiting for Kinect data...")
    if args.sim and os.path.exists('../tools/rgb.npy') and os.path.exists('../tools/depth.npy'):
        arr_rgb = np.load('../tools/rgb.npy')
        arr_depth = np.load('../tools/depth.npy')
            
    else:
        rgb =  rospy.wait_for_message("/camera/rgb/image_color", Image)
        # would this receive data corresponding to different samples or will the queueing help this?
        depth = rospy.wait_for_message("/camera/depth_registered/image_raw", Image)
    
        bridge = cv_bridge.CvBridge()
        rgb = bridge.imgmsg_to_cv2(rgb, desired_encoding="passthrough")
        depth = bridge.imgmsg_to_cv2(depth, desired_encoding="passthrough")
        arr_rgb = np.asarray(rgb[:, :, :])

        arr_rgb = np.concatenate((arr_rgb[:, :, 2:], arr_rgb[:, :, 1:2], arr_rgb[:, :, 0:1]), axis=2)
        plt.imshow(arr_rgb)
        plt.show()
        arr_depth = np.asarray(depth[:, :])
        
        if args.sim:
            np.save('rgb.npy', arr_rgb)
            np.save('depth.npy', arr_depth)
    

    # get camera to aruco marker transformation
    marker_to_camera = aruco_camPose(arr_rgb)
    camera_to_marker = np.linalg.inv(marker_to_camera)

    if (arr_rgb.shape[0] > 100 and arr_rgb.shape[1] > 100):
        print '-------------------n-------------------------------------'
        list_boxes, list_masks = run_affordance_net_asus(net, arr_rgb)
        print 'len list boxes: ', len(list_boxes)
        list_obj_centroids = convert_bbox_to_centroid(list_boxes, list_masks)

    width, height = arr_rgb.shape[0:2]
    rows = np.arange(0, height)
    cols = np.arange(0, width)

    coord1, coord2 = np.meshgrid(rows, cols)
    # coords N x 3
    coords = np.concatenate([coord1.reshape(-1, 1), coord2.reshape(-1, 1), arr_depth.reshape(-1, 1)], axis=1).T.astype(float)
    # convert the pixel coordinates to 3D coordinates 3 x N
    coords_3D = np.concatenate(project_to_3D(coords[np.newaxis, 0], coords[np.newaxis, 1], coords[np.newaxis, 2]), axis=0)

    # make homogeneous coordinates
    coords_hom = np.concatenate([coords_3D/1000, np.ones((1, width*height))], axis=0)
    
    #coords_cam = np.dot(camera_to_marker, coords_hom) # 4 x N
    coords_cam = coords_hom

    # Replace 1s with rgb values
    arr_rgb = arr_rgb.astype(int)
    red = np.left_shift(arr_rgb[:, :, 0].reshape(-1), 16)
    green = np.left_shift(arr_rgb[:, :, 1].reshape(-1), 8)
    blue = arr_rgb[:, :, 2].reshape(-1)
    coords_cam[-1] = red + green + blue

    fields = [PointField('x', 0, PointField.FLOAT32, 1),
              PointField('y', 4, PointField.FLOAT32, 1),
              PointField('z', 8, PointField.FLOAT32, 1),
              PointField('rgb', 16, PointField.FLOAT32, 1)]
    h = msg.Header()
    h.stamp = rospy.Time.now()
    h.frame_id = '/affordance_net'

    pc2_msg = pc2.create_cloud(h, fields, coords_cam.T)
        
    for _ in range(len(list_boxes)*100000):
        # get array of all pixel coordinates

        pub_point_cloud.publish(pc2_msg)
                                  
        print('List object centroids:', list_obj_centroids)
        # select object and affordance to project to 3D
        obj_id = 6 # cup
        aff_id = 9  # grasp affordance
        
        selected_obj_aff = select_object_and_aff(list_obj_centroids, obj_id, aff_id) 
        print(selected_obj_aff)
        # get depth value from depth map    
        dval = arr_depth[selected_obj_aff[3], selected_obj_aff[2]].astype(float)
        print(selected_obj_aff[3], selected_obj_aff[2], dval)
        print(coords[:, selected_obj_aff[3]*640 + selected_obj_aff[2]])
        if dval != 'nan':
            # find 3D point
            p3Dc_camera = project_to_3D(selected_obj_aff[2], selected_obj_aff[3], dval/1000)
            print('Center relative to camera:', p3Dc_camera)
            print(coords_3D[:, selected_obj_aff[3]*640 + selected_obj_aff[2]])
            p3Dc_camera = np.append(p3Dc_camera, 1)
            p3Dc_marker = np.dot(camera_to_marker, p3Dc_camera)
            print('Center relative to marker:', p3Dc_marker)
            obj_pose_3D = PoseStamped()
            obj_pose_3D.header.frame_id = "camera_depth_optical_frame"
            
            obj_pose_3D.pose.position.x = round(p3Dc_marker[0], 2) + 0.3 
            obj_pose_3D.pose.position.y = round(p3Dc_marker[1], 2) + 0.3 - 0.05
            obj_pose_3D.pose.position.z = round(p3Dc_marker[2], 2) - 0.12
            obj_pose_3D.pose.orientation.x = 0
            obj_pose_3D.pose.orientation.y = 0
            obj_pose_3D.pose.orientation.z = 0
            obj_pose_3D.pose.orientation.w = 1 ## no rotation
            # publish pose
            pub_obj_pose_3D.publish(obj_pose_3D)

        
