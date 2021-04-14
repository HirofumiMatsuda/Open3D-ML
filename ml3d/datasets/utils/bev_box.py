from ...vis import BoundingBox3D
import numpy as np
from copy import copy


class BEVBox3D(BoundingBox3D):
    """Class that defines a special bounding box for object detection, with only
    one rotation axis (yaw)."""

    def __init__(self,
                 center,
                 size,
                 yaw,
                 label_class,
                 confidence,
                 world_cam=None,
                 cam_img=None,
                 **kwargs):
        """Creates a bounding box. Front, up, left define the axis of the box
        and must be normalized and mutually orthogonal.

        center: (x, y, z) that defines the center of the box
        size: (width, height, depth) that defines the size of the box, as
            measured from edge to edge
        yaw: yaw angle of box
        label_class: integer specifying the classification label. If an LUT is
            specified in create_lines() this will be used to determine the color
            of the box.
        confidence: confidence level of the box
        world_cam: world to camera transformation (shape = [4,4])
            x_cam = x_world @ world_cam
        cam_img: camera to image transformation (shape = [4,4])
            x_img = x_cam @ cam_img
        """

        self.yaw = yaw
        self.world_cam = world_cam
        self.cam_img = cam_img

        # x-axis
        left = [np.cos(self.yaw), -np.sin(self.yaw), 0]
        # y-axis
        front = [np.sin(self.yaw), np.cos(self.yaw), 0]
        # z-axis
        up = [0, 0, 1]

        super().__init__(center, front, up, left, size, label_class, confidence,
                         **kwargs)

        self.points_inside_box = np.array([])
        self.level = self.get_difficulty()
        if self.world_cam is not None:
            self.dis_to_cam = np.linalg.norm(
                self.center @ self.world_cam[:3, :3] + self.world_cam[3, :3])
        else:
            self.dis_to_cam = np.linalg.norm(self.center)

    def __repr__(self):
        s = str(self.identifier) + " (class=" + str(
            self.label_class) + ", conf=" + str(self.confidence)
        if self.meta is not None:
            s = s + ", meta=" + str(self.meta)
        s = s + ")" + f" yaw={np.rad2deg(self.yaw):0.2f} size=" + str(self.size)
        return s

    def generate_corners3d(self):
        """
        Generate corners3d representation for this object.

        Returns:
            corners_3d: (8, 3) corners of box3d in camera coord
        """
        w, h, l = self.size
        x_corners = [l / 2, l / 2, -l / 2, -l / 2, l / 2, l / 2, -l / 2, -l / 2]
        y_corners = [0, 0, 0, 0, -h, -h, -h, -h]
        z_corners = [w / 2, -w / 2, -w / 2, w / 2, w / 2, -w / 2, -w / 2, w / 2]

        R = np.array([[np.cos(self.yaw), 0,
                       np.sin(self.yaw)], [0, 1, 0],
                      [-np.sin(self.yaw), 0,
                       np.cos(self.yaw)]])
        corners3d = np.vstack([x_corners, y_corners, z_corners])  # (3, 8)
        corners3d = np.dot(R, corners3d).T
        corners3d = corners3d + self.to_camera()[:3]
        return corners3d

    def to_xyzwhlr(self):
        """
        Returns box in the common (KITTI) 7-sized vector representation.

        Returns:
            box: (7,)
        """
        bbox = np.zeros((7,))
        bbox[0:3] = self.center - [0, 0, self.size[1] / 2]
        bbox[3:6] = np.array(self.size)[[0, 2, 1]]
        bbox[6] = self.yaw
        return bbox

    def to_xyzwlhyc(self):
        """
        Convert box to [center (xyz), size (dx, dy, dz), yaw (rad),
        label_class] representation for caching in a npy file

        Returns: (numpy array (8,))
        """
        return np.hstack((self.center, self.size, self.yaw, self.label_class))

    def to_camera_bev(self):
        """
        Transforms box into camera space as a BEV box. This is an approximation
        since the exact box is no longer parallel to the new XY plane.
        new yaw = mean(angle between box X and camera X, angle between box Y and
                camera Y)

        Returns:
            transformed BEVBox3D
        """
        if self.world_cam is None or np.allclose(self.world_cam, np.eye(4)):
            return self
        bb_cam = copy(self)
        bb_cam.yaw = 0.5 * (np.arccos(self.left @ self.world_cam[:3, 0]) +
                            np.arccos(self.front @ self.world_cam[:3, 1]))
        bb_cam.transform(self.world_cam)
        bb_cam.world_cam = None
        return bb_cam

    def to_camera(self):
        """
        Transforms box into camera space.

        Returns:
            transformed box in KITTI representation: (7,)
        """
        if self.world_cam is None:
            return self.to_xyzwhlr()[[1, 2, 0, 4, 5, 3, 6]]

        bbox = np.zeros((7,))
        bbox[0:3] = self.center - [0, 0, self.size[1] / 2]
        bbox[0:3] = (np.array([*bbox[0:3], 1.0]) @ self.world_cam)[:3]
        bbox[3:6] = self.size[2::-1]
        bbox[6] = self.yaw
        return bbox

    def to_img(self):
        """
        Transforms box into 2d box.

        Returns:
            transformed box [center_x, center_y, size_x, size_y]: (4,)
        """
        if self.cam_img is None:
            return None

        corners = self.generate_corners3d()
        corners = np.concatenate(
            [corners, np.ones((corners.shape[0], 1))], axis=-1)

        bbox_img = np.matmul(corners, self.cam_img)
        bbox_img = bbox_img[:, :2] / bbox_img[:, 3:]

        minxy = np.min(bbox_img, axis=0)
        maxxy = np.max(bbox_img, axis=0)

        size = maxxy - minxy
        center = minxy + size / 2

        return np.concatenate([center, size])

    def is_visible(self, image_shape):
        """
        Test if bounding box center is visible in camera. It may still be
        occluded by another object.

        Args:
            image_shape: (width, height) of the image
        """
        bb_img = self.to_img()
        visible = (0 <= bb_img[0] < image_shape[0] and
                   0 <= bb_img[1] < image_shape[1])
        if self.world_cam is None:
            return visible
        z_cam = (np.array([*self.center, 1.0]) @ self.world_cam)[2]
        in_front = z_cam > 0.1  # at least 0.1m in front of the camera
        return visible and in_front

    def get_difficulty(self):
        """
        General method to compute difficulty, can be overloaded.
        Return difficulty depending on projected height of box.
        """

        if self.cam_img is None:
            return 0

        heights = [40, 25]
        height = self.to_img()[3]
        diff = -1
        for j in range(len(heights)):
            if height > heights[j]:
                diff = j
                break
        return diff

    def to_dict(self):
        """
        Convert data for evaluation:
        """
        return {
            'bbox': self.to_camera(),
            'label': self.label_class,
            'score': self.confidence,
            'difficulty': self.level
        }

    @staticmethod
    def to_dicts(bboxes):
        """
        Convert data for evaluation:

        Args:
            bboxes: List of BEVBox3D bboxes.
        """
        box_dicts = {
            'bbox': np.empty((len(bboxes), 7)),
            'label': np.empty((len(bboxes),), dtype='<U20'),
            'score': np.empty((len(bboxes),)),
            'difficulty': np.empty((len(bboxes),))
        }

        for i in range(len(bboxes)):
            box_dict = bboxes[i].to_dict()
            for k in box_dict:
                box_dicts[k][i] = box_dict[k]

        return box_dicts
