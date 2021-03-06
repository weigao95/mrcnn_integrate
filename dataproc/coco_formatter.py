import os
import cv2
import datetime
import attr
from pycocotools import mask
from skimage import measure
import numpy as np
import json
from typing import List, Dict
import config.parameter as parameter
from dataproc.abstract_db import AbstractMaskDatabase


@attr.s
class COCODatasetFormatterConfig(object):
    # The various path
    db_name: str = ''
    base_folder: str = ''

    # The path derived from base folder
    @property
    def image_folder_path(self) -> str:
        return os.path.join(self.base_folder, 'images')

    @property
    def json_file_path(self) -> str:  # Just the base folder with db name
        assert len(self.db_name) > 0
        assert os.path.exists(self.base_folder)
        return os.path.join(self.base_folder, self.db_name)

    # The object category, might be update
    object_category = parameter.default_obj_category

    # The default parameter
    BBOX_MIN_WIDTH: float = 5.0
    BBOX_MIN_HEIGHT: float = 5.0
    AREA_THRESHOLD: float = 25.0

    # Misc info
    INFO = {
        "description": "Dense Correspondence COCO Dataset",
        "url": "https://github.com/RobotLocomotion/pytorch-dense-correspondence",
        "version": "",
        "year": 2018,
        "contributor": "",
        "date_created": datetime.datetime.utcnow().isoformat(' ')
    }

    LICENSES = [
        {
            "id": 1,
            "name": "",
            "url": ""
        }
    ]


class COCODatasetFormatter(object):
    """
    This formatter borrows a lot form Ethan Weber's code in pytorch-dense-correspondence
    """
    def __init__(self, config: COCODatasetFormatterConfig):
        # The config file
        self._config = config

        # Build the path if not exist
        assert len(self._config.db_name) > 0
        assert len(self._config.base_folder) > 0
        if not os.path.exists(self._config.base_folder):
            os.mkdir(self._config.base_folder)
        if not os.path.exists(self._config.image_folder_path):
            os.mkdir(self._config.image_folder_path)

        # Build name2id
        self._category_name2id: Dict[str, int] = {}
        for category in config.object_category:
            assert 'name' in category
            assert 'id' in category
            self._category_name2id[category['name']] = category['id']

    def process_db_list(self, database_list: List[AbstractMaskDatabase]):
        # Predefined variables
        image_id: int = 0
        annotation_id: int = 0
        all_annotation_list = []
        all_image_info_list = []

        # The total size of the db
        for database in database_list:
            for entry_idx in range(len(database)):
                img_entry = database[entry_idx]
                # Iterate over all annotation in the entry
                img_annotation_list = []
                for annotation_entry in img_entry.annotation_list:
                    # Retrieve information
                    binary_mask = annotation_entry.binary_mask
                    category_name = annotation_entry.category_name
                    if category_name not in self._category_name2id:
                        # This is not a valid annotation
                        continue
                    # The category id as int
                    category_id = self._category_name2id[category_name]

                    # Get annotation info
                    is_validity, annotation = self._get_annotation_info(
                        annotation_id,
                        image_id,
                        category_id,
                        binary_mask)

                    # If this annotation is OK
                    if is_validity:
                        annotation_id += 1
                        img_annotation_list.append(annotation)

                # If there is no annotation on this image
                if len(img_annotation_list) == 0:
                    continue
                else:  # Insert the annotation to global list
                    for elem in img_annotation_list:
                        all_annotation_list.append(elem)

                # Process of this image
                rgb_img = img_entry.rgb_image
                height, width, _ = rgb_img.shape  # Must be three channel image

                # Write the image to output path
                rgb_output_path = os.path.join(self._config.image_folder_path, "{:05}.png".format(image_id))
                rgb_relative_path = os.path.basename(rgb_output_path)
                cv2.imwrite(rgb_output_path, rgb_img)

                # Build the image info
                image_info = COCODatasetFormatter._get_image_info(image_id, width, height, rgb_relative_path)
                all_image_info_list.append(image_info)
                image_id += 1

                # Debug output
                if image_id % 20 == 0:
                    print('Processing image %d' % image_id)

        # Global output
        coco_output = self._get_coco_output_from_images_and_annotations(
            all_image_info_list, all_annotation_list)
        self._write_coco_to_json(coco_output)

    def _get_annotation_info(
            self,
            annotation_id: int,
            image_id: int,
            category_id: int,
            this_mask: np.ndarray):
        # Perform mask encoding
        encoded_mask = COCODatasetFormatter._get_encoded_mask(this_mask)
        area = COCODatasetFormatter._get_area_of_encoded_mask(encoded_mask)
        x, y, width, height = COCODatasetFormatter.get_bounding_box(encoded_mask)

        # using polygon (iscrowd = 0)
        segmentation = self.get_polygons(this_mask)

        # Build the annotation
        annotation = {
            "id": annotation_id,
            "image_id": image_id,
            "category_id": category_id,
            "segmentation": segmentation,
            "area": float(area),
            "bbox": [int(x), int(y), int(width), int(height)],
            "iscrowd": 0,  # assume polygon for now
        }

        # Check the result
        is_validity = self._check_annotation_valid(annotation)
        return is_validity, annotation

    @staticmethod
    def _get_encoded_mask(image_mask: np.ndarray):
        return mask.encode(np.asfortranarray(image_mask))

    @staticmethod
    def _get_area_of_encoded_mask(encoded_mask):
        # return the area of the mask (by counting the nonzero pixels)
        return mask.area(encoded_mask)

    @staticmethod
    def get_bounding_box(encoded_mask):
        # returns x, y (top left), width, height
        bounding_box = mask.toBbox(encoded_mask)
        return bounding_box.astype(int)

    @staticmethod
    def get_polygons(image_mask: np.ndarray, tolerance=0):
        """
        code from https://github.com/waspinator/pycococreator/blob/master/pycococreatortools/pycococreatortools.py
        Args:
            image_mask: a 2D binary numpy array where '1's represent the object
            tolerance: Maximum distance from original points of polygon to approximated
                polygonal chain. If tolerance is 0, the original coordinate array is returned.
        """
        polygons = []
        # pad mask to close contours of shapes which start and end at an edge
        padded_binary_mask = np.pad(image_mask, pad_width=1, mode='constant', constant_values=0)
        contours = measure.find_contours(padded_binary_mask, 0.5)
        contours = np.subtract(contours, 1)
        for contour in contours:
            if not np.array_equal(contour[0], contour[-1]):
                contour = np.vstack((contour, contour[0]))
            contour = measure.approximate_polygon(contour, tolerance)
            if len(contour) < 3:
                continue
            contour = np.flip(contour, axis=1)
            segmentation = contour.ravel().tolist()
            # after padding and subtracting 1 we may get -0.5 points in our segmentation
            segmentation = [0 if i < 0 else i for i in segmentation]
            polygons.append(segmentation)

        # OK
        return polygons

    def _check_annotation_valid(self, ann) -> bool:
        if len(ann['segmentation']) == 0:
            return False

        if ann['area'] < self._config.AREA_THRESHOLD:
            return False

        [x, y, width, height] = ann['bbox']
        if (width < self._config.BBOX_MIN_WIDTH) or (height < self._config.BBOX_MIN_HEIGHT):
            return False

        # Everything is OK here
        return True

    @staticmethod
    def _get_image_info(
            image_id: int,
            width: int,
            height: int,
            file_name: str,
            license_id=1,
            flickr_url="",
            coco_url="",
            date_captured=datetime.datetime.utcnow().isoformat(' ')):
        """
        Returns image data in the correct format for COCO
        """
        image_info = {
            "id": image_id,
            "width": width,
            "height": height,
            "file_name": file_name,
            "license": license_id,
            "flickr_url": flickr_url,
            "coco_url": coco_url,
            "date_captured": date_captured,
        }

        return image_info

    def _get_coco_output_from_images_and_annotations(self, images, annotations):
        # construct the dictionary that will be used to create the coco json data
        coco_output = {
            "info": self._config.INFO,
            "licenses": self._config.LICENSES,
            "categories": self._config.object_category,
            "images": images,
            "annotations": annotations
        }
        return coco_output

    def _write_coco_to_json(self, coco_output):
        # write the coco data to a json file which was specified at the class instantiation
        # self.name_of_set should be the string name to write to (which shouldn't include
        # .json)
        full_json_filename = self._config.json_file_path
        with open('{}.json'.format(full_json_filename), 'w') as output_json_file:
            json.dump(coco_output, output_json_file)


# The debugger methods
def test_formatter():
    import dataproc.spartan_singleobj_database as spartan_db
    config = spartan_db.SpartanSingleObjMaskDatabaseConfig()
    config.pdc_data_root = '/home/wei/data/pdc'
    config.scene_list_filepath = '/home/wei/Coding/fill_it/config/boot_logs.txt'
    config.category_name_key = 'shoe'
    database = spartan_db.SpartanSingleObjMaskDatabase(config)

    # The formatter
    formatter_config = COCODatasetFormatterConfig()
    formatter_config.db_name = 'boot_db'
    formatter_config.base_folder = 'tmp'
    formatter = COCODatasetFormatter(formatter_config)
    formatter.process_db_list([database])


if __name__ == '__main__':
    test_formatter()
