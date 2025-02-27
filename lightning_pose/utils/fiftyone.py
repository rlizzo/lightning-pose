import fiftyone as fo
from tqdm import tqdm
from typing import Dict, List, Optional, Union, Callable
import pandas as pd
import numpy as np
from omegaconf import DictConfig, OmegaConf, ListConfig
from lightning_pose.utils.io import return_absolute_path, return_absolute_data_paths
import os
from typeguard import typechecked

from lightning_pose.utils.plotting_utils import get_videos_in_dir


@typechecked
def check_lists_equal(list_1: list, list_2: list) -> bool:
    return len(list_1) == len(list_2) and sorted(list_1) == sorted(list_2)


@typechecked
def check_unique_tags(data_pt_tags: List[str]) -> bool:
    uniques = list(np.unique(data_pt_tags))
    cond_list = ["test", "train", "validation"]
    cond_list_with_unused_images = ["test", "train", "validation", "unused"]
    flag = check_lists_equal(uniques, cond_list) or check_lists_equal(
        uniques, cond_list_with_unused_images
    )
    return flag


@typechecked
def check_dataset(dataset: fo.Dataset) -> None:
    try:
        dataset.compute_metadata(skip_failures=False)
    except ValueError:
        print("Encountered error in metadata computation. See print:")
        print(dataset.exists("metadata", False))
        print(
            "The above print should indicate bad image samples, e.g., with bad paths."
        )


@typechecked
def get_image_tags(pred_df: pd.DataFrame) -> pd.Series:
    # last column indicates if the image was used for training, testing, validation or unused at all
    # zero -> unused, so explicitly replace
    data_pt_tags = pred_df.iloc[:, -1].replace("0.0", "unused")
    assert check_unique_tags(data_pt_tags=list(data_pt_tags))
    return data_pt_tags


# @typechecked # force typechecking over the entire class. right now fails due to some list/listconfig issue
class FiftyOneKeypointBase:
    def __init__(
        self, cfg: DictConfig, keypoints_to_plot: Optional[List[str]] = None
    ) -> None:
        self.cfg = cfg
        self.keypoints_to_plot = keypoints_to_plot
        self.data_dir, self.video_dir = return_absolute_data_paths(cfg.data)
        self.df_header_rows: List[int] = OmegaConf.to_object(cfg.data.header_rows)
        # TODO: [0, 1] in toy dataset, [1,2] in actual ones, standardize
        # ground_truth_df is not necessary but useful for keypoint names
        self.ground_truth_df: pd.DataFrame = pd.read_csv(
            os.path.join(self.data_dir, self.cfg.data.csv_file),
            header=self.df_header_rows,
        )
        if self.keypoints_to_plot is None:
            # plot all keypoints that appear in the ground-truth dataframe
            self.keypoints_to_plot: List[str] = list(
                self.ground_truth_df.columns.levels[0][1:]
            )

    @property
    def img_width(self) -> int:
        return self.cfg.data.image_orig_dims.width

    @property
    def img_height(self) -> int:
        return self.cfg.data.image_orig_dims.height

    @property
    def num_keypoints(self) -> int:
        return self.cfg.data.num_keypoints

    @property
    def model_names(self) -> List[str]:
        return self.cfg.eval.model_display_names

    @property
    def dataset_name(self) -> str:
        return self.cfg.eval.fifty_one_dataset_name

    def get_model_abs_paths(self) -> List[str]:
        model_maybe_relative_paths = self.cfg.eval.hydra_paths
        model_abs_paths = [
            return_absolute_path(m, n_dirs_back=2) for m in model_maybe_relative_paths
        ]
        # assert that the model folders exist
        for mod_path in model_abs_paths:
            assert os.path.isdir(mod_path)
        return model_abs_paths

    def load_model_predictions(self) -> None:
        # TODO: we have to specify the paths differently in the init method?
        # take the abs paths, and load the models into a dictionary
        model_abs_paths = self.get_model_abs_paths()
        self.model_preds_dict = {}
        for model_idx, model_dir in enumerate(model_abs_paths):
            # assuming that each path of saved logs has a predictions.csv file in it
            self.model_preds_dict[self.model_names[model_idx]] = pd.read_csv(
                os.path.join(model_dir, "predictions.csv"), header=self.df_header_rows
            )

    @typechecked
    def build_single_frame_keypoint_list(
        self,
        df: pd.DataFrame,
        frame_idx: int,
    ) -> List[fo.Keypoint]:
        # the output of this, is a the positions of all keypoints in a single frame for a single model.
        keypoints_list = []
        for kp_name in self.keypoints_to_plot:  # loop over names
            if "likelihood" in df[kp_name]:
                confidence = df[kp_name]["likelihood"][frame_idx]
            else:  # gt data has no confidence, but we call it 1.0 for simplicity
                confidence = 1.0  # also works if we make it None
            # "bodyparts" it appears in the csv as we read it right now, but should be ignored
            if kp_name == "bodyparts":
                continue
            # write a single keypoint's position, confidence, and name
            keypoints_list.append(
                fo.Keypoint(
                    points=[
                        [
                            df[kp_name]["x"][frame_idx] / self.img_width,
                            df[kp_name]["y"][frame_idx] / self.img_height,
                        ]
                    ],
                    confidence=confidence,
                    label=kp_name,  # sometimes plotted aggresively
                )
            )
        return keypoints_list

    @typechecked
    def get_keypoints_per_image(self, df: pd.DataFrame) -> List[fo.Keypoints]:
        """iterates over the rows of the dataframe and gathers keypoints in fiftyone format"""
        keypoints_list = []
        for img_idx in tqdm(range(df.shape[0])):
            single_frame_keypoints_list = self.build_single_frame_keypoint_list(
                df=df, frame_idx=img_idx
            )
            keypoints_list.append(fo.Keypoints(keypoints=single_frame_keypoints_list))
        return keypoints_list

    @typechecked
    def get_pred_keypoints_dict(self) -> Dict[str, List[fo.Keypoints]]:
        pred_keypoints_dict = {}
        # loop over the dictionary with predictions per model
        for model_name, model_df in self.model_preds_dict.items():
            print("Collecting predicted keypoints for model: %s..." % model_name)
            pred_keypoints_dict[model_name] = self.get_keypoints_per_image(model_df)

        return pred_keypoints_dict

    def create_dataset(self):
        # subclasses build their own
        raise NotImplementedError


class FiftyOneImagePlotter(FiftyOneKeypointBase):
    def __init__(
        self, cfg: DictConfig, keypoints_to_plot: Optional[List[str]] = None
    ) -> None:
        super().__init__(cfg=cfg, keypoints_to_plot=keypoints_to_plot)

    @property
    def image_paths(self) -> List[str]:
        """extract absolute paths for all the images in the ground truth csv file

        Returns:
            List[str]: absolute paths per image, checked before returning.
        """
        relative_list = list(self.ground_truth_df.iloc[:, 0])
        absolute_list = [
            os.path.join(self.data_dir, im_path) for im_path in relative_list
        ]
        # assert that the images are indeed files
        for im in absolute_list:
            assert os.path.isfile(im)

        return absolute_list

    @typechecked
    def get_gt_keypoints_list(self) -> List[fo.Keypoints]:
        # for each frame, extract ground-truth keypoint information
        print("Collecting ground-truth keypoints...")
        return self.get_keypoints_per_image(self.ground_truth_df)

    @typechecked
    def create_dataset(self) -> fo.Dataset:
        samples = []
        # read each model's csv into a pandas dataframe
        self.load_model_predictions()
        # assumes that train,test,val split is identical for all the different models. may be different with ensembling.
        self.data_tags = get_image_tags(self.model_preds_dict[self.model_names[0]])
        # build the ground-truth keypoints per image
        gt_keypoints_list = self.get_gt_keypoints_list()
        # do the same for each model's predictions (lists are stored in a dict)
        pred_keypoints_dict = self.get_pred_keypoints_dict()
        for img_idx, img_path in enumerate(tqdm(self.image_paths)):
            # create a "sample" with an image and a tag (should be appended to self.samples)
            sample = fo.Sample(filepath=img_path, tags=[self.data_tags[img_idx]])
            # add ground truth keypoints to the sample (won't happen for video)
            sample["ground_truth"] = gt_keypoints_list[img_idx]  # previously created
            # add model-predicted keypoints to the sample
            for model_field_name, model_preds in pred_keypoints_dict.items():
                sample[model_field_name + "_preds"] = model_preds[img_idx]

            samples.append(sample)

        fiftyone_dataset = fo.Dataset(self.dataset_name)
        fiftyone_dataset.add_samples(samples)
        return fiftyone_dataset


""" 
what's shared between the two?
certain properties of the image; keypoint names; obtaining of csvs
creation of dataset.

different: 
in video each sample is a video. there is basically one sample if we analyze one video.
should also use get_pred_keypoints_dict (assuming that the preds for a new vid look the same as the ones in train hydra)

what do I need?
a folder with csv predictions for a given video. I have multiple videos and potentially multiple models' predictions for each.
maybe just point to path to preds, and automatically find the path that has the same basename as the video name? or is it too specific?
or for each video in the folder, I should assume there exists a directory with the same name inside saved_preds folder? that seems easier to grasp.
it will require changing the path handler a bit, but it'll be easy.

for now -- assume the basic inputs exist, i.e., we have all the paths to individual predictions.
assume one video for now. keep as simple as possible.
"""


class FiftyOneKeypointVideoPlotter(FiftyOneKeypointBase):
    def __init__(
        self,
        cfg: DictConfig,
        keypoints_to_plot: Optional[List[str]] = None,
    ) -> None:
        super().__init__(cfg=cfg, keypoints_to_plot=keypoints_to_plot)
        self.video: str = cfg.eval.video_file_to_plot
        self.pred_csv_files: List[str] = self.cfg.eval.pred_csv_files_to_plot
        self.check_inputs()

    def check_inputs(self) -> None:
        for f in self.pred_csv_files:
            assert os.path.isfile(f)
        assert os.path.isfile(self.video)

    @property
    def model_names(self) -> List[str]:
        model_display_names = self.cfg.eval.model_display_names
        if model_display_names is None:  # model_0, model_1, ...
            model_display_names = [
                "model_%i" % i for i in range(len(self.pred_csv_files))
            ]
        assert len(model_display_names) == len(self.pred_csv_files)
        return model_display_names

    def load_model_predictions(self) -> None:
        self.model_preds_dict = {}
        for model_name, pred_csv_file in zip(self.model_names, self.pred_csv_files):
            self.model_preds_dict[model_name] = pd.read_csv(
                pred_csv_file, header=self.df_header_rows
            )

    def create_dataset(self) -> fo.Dataset:
        # read each model's csv into a pandas dataframe, save in self.model_preds_dict
        self.load_model_predictions()
        # modify the predictions into fiftyone format
        pred_keypoints_dict = self.get_pred_keypoints_dict()
        # inherited from FiftyOneKeypointBase
        dataset = fo.Dataset(self.dataset_name + "_videos")
        # adding _videos so as to not overwrite existing datasets with images.
        # NOTE: for now, one sample only in the dataset (one video)
        # TODO: in the future, dataset could include multiple video samples
        video_sample = fo.Sample(filepath=self.video)
        first_model_name = list(pred_keypoints_dict.keys())[0]
        for frame_idx in tqdm(range(len(pred_keypoints_dict[first_model_name]))):
            for model_field_name, model_preds in pred_keypoints_dict.items():
                video_sample.frames[frame_idx + 1][
                    model_field_name + "_preds"
                ] = model_preds[frame_idx]

        dataset.add_sample(video_sample)
        return dataset


# import fiftyone as fo
# import fiftyone.core.metadata as fom
# import h5py
# import hydra
# from imgaug.augmentables.kps import Keypoint, KeypointsOnImage
# import imgaug.augmenters as iaa
# import numpy as np
# from omegaconf import DictConfig, OmegaConf
# import os
# import pandas as pd
# import torch
# from tqdm import tqdm
# from typing import List, Union, Callable
# from typeguard import typechecked

# from lightning_pose.models.heatmap_tracker import HeatmapTracker
# from lightning_pose.utils.io import return_absolute_path, return_absolute_data_paths


# @typechecked
# def check_lists_equal(list_1: list, list_2: list) -> bool:
#     return len(list_1) == len(list_2) and sorted(list_1) == sorted(list_2)


# @typechecked
# def check_unique_tags(data_pt_tags: List[str]) -> bool:
#     uniques = list(np.unique(data_pt_tags))
#     cond_list = ["test", "train", "validation"]
#     cond_list_with_unused_images = ["test", "train", "validation", "unused"]
#     flag = check_lists_equal(uniques, cond_list) or check_lists_equal(
#         uniques, cond_list_with_unused_images
#     )
#     return flag


# @typechecked
# def get_image_tags(pred_df: pd.DataFrame) -> pd.Series:
#     # last column indicates if the image was used for training, testing, validation or unused at all
#     data_pt_tags = pred_df.iloc[:, -1].replace("0.0", "unused")
#     assert check_unique_tags(data_pt_tags=data_pt_tags)
#     return data_pt_tags


# def tensor_to_keypoint_list(keypoint_tensor, height, width):
#     # TODO: standardize across video & image plotting. Dan's video util is more updated.
#     img_kpts_list = []
#     for i in range(len(keypoint_tensor)):
#         img_kpts_list.append(
#             tuple(
#                 (
#                     float(keypoint_tensor[i][0] / width),
#                     float(keypoint_tensor[i][1] / height),
#                 )
#             )
#         )
#         # keypoints are normalized to the original image dims, either add these to data
#         # config, or automatically detect by loading a sample image in dataset.py or
#         # something
#     return img_kpts_list


# @typechecked
# def make_keypoint_list(
#     csv_with_preds: pd.DataFrame,
#     keypoint_names: List[str],
#     frame_idx: int,
#     width: int,
#     height: int,
# ) -> List[fo.Keypoint]:
#     keypoints_list = []
#     for kp_name in keypoint_names:  # loop over names
#         print(kp_name)
#         # "bodyparts" it appears in the csv as we read it right now, but should be ignored
#         if kp_name == "bodyparts":
#             continue
#         # write a single keypoint's position, confidence, and name
#         keypoints_list.append(
#             fo.Keypoint(
#                 points=[
#                     [
#                         csv_with_preds[kp_name]["x"][frame_idx] / width,
#                         csv_with_preds[kp_name]["y"][frame_idx] / height,
#                     ]
#                 ],
#                 confidence=csv_with_preds[kp_name]["likelihood"][frame_idx],
#                 label=kp_name,  # sometimes plotted aggresively; can comment out if needed.
#             )
#         )
#     return keypoints_list


# def make_dataset_and_viz_from_csvs(cfg: DictConfig):

#     # basic error checking
#     assert len(cfg.eval.model_display_names) == len(cfg.eval.hydra_paths)

#     df_header_rows = OmegaConf.to_object(cfg.data.header_rows)  # default is [1,2]
#     data_dir, video_dir = return_absolute_data_paths(cfg.data)

#     # load ground truth csv file from which we take image paths
#     gt_csv_data = pd.read_csv(
#         os.path.join(data_dir, cfg.data.csv_file), header=df_header_rows
#     )
#     image_paths = list(gt_csv_data.iloc[:, 0])
#     num_kpts = cfg.data.num_keypoints

#     # below doesn't seem needed, work with dataframe
#     gt_keypoints = gt_csv_data.iloc[:, 1:].to_numpy()
#     gt_keypoints = gt_keypoints.reshape(-1, num_kpts, 2)

#     # load info from a single predictions csv file
#     model_maybe_relative_paths = cfg.eval.hydra_paths
#     model_abs_paths = [
#         return_absolute_path(m, n_dirs_back=2) for m in model_maybe_relative_paths
#     ]

#     # could go to iteration zero of the loop below
#     prediction_csv_file = os.path.join(model_abs_paths[0], "predictions.csv")
#     pred_df = pd.read_csv(prediction_csv_file, header=df_header_rows)
#     # data_pt_tags = list(pred_df.iloc[:, -1])
#     # for images we ignore in training, replace a zero entry by the string "unused"
#     data_pt_tags = pred_df.iloc[:, -1].replace("0.0", "unused")
#     assert check_unique_tags(data_pt_tags=data_pt_tags)

#     # store predictions from different models
#     model_preds_np = np.empty(
#         shape=(len(model_maybe_relative_paths), len(image_paths), num_kpts, 3)
#     )
#     heatmap_height = cfg.data.image_resize_dims.height // (
#         2 ** cfg.data.downsample_factor
#     )
#     heatmap_width = cfg.data.image_resize_dims.width // (
#         2 ** cfg.data.downsample_factor
#     )
#     model_heatmaps_np = np.empty(
#         shape=(
#             len(model_maybe_relative_paths),
#             len(image_paths),
#             num_kpts,
#             heatmap_height,
#             heatmap_width,
#         )
#     )

#     # assuming these are absolute paths for now, might change this later
#     for model_idx, model_dir in enumerate(model_abs_paths):
#         pred_csv_path = os.path.join(model_dir, "predictions.csv")
#         pred_heatmap_path = os.path.join(model_dir, "heatmaps_and_images/heatmaps.h5")
#         model_pred_csv = pd.read_csv(
#             pred_csv_path, header=df_header_rows
#         )  # load ground-truth data csv
#         keypoints_np = model_pred_csv.iloc[:, 1:-1].to_numpy()
#         keypoints_np = keypoints_np.reshape(-1, num_kpts, 3)  # x, y, confidence
#         model_h5 = h5py.File(pred_heatmap_path, "r")
#         heatmaps = model_h5.get("heatmaps")
#         heatmaps_np = np.array(heatmaps)
#         model_preds_np[model_idx] = keypoints_np
#         model_heatmaps_np[model_idx] = heatmaps_np

#     samples = []
#     keypoint_idx = 0  # index of keypoint to visualize heatmap for

#     for img_idx, img_name in enumerate(tqdm(image_paths)):
#         gt_kpts_list = tensor_to_keypoint_list(
#             gt_keypoints[img_idx],
#             cfg.data.image_orig_dims.height,
#             cfg.data.image_orig_dims.width,
#         )
#         img_path = os.path.join(data_dir, img_name)
#         assert os.path.isfile(img_path)
#         tag = data_pt_tags[img_idx]
#         if tag == 0.0:
#             tag = "train-not_used"
#         sample = fo.Sample(filepath=img_path, tags=[tag])
#         sample["ground_truth"] = fo.Keypoints(
#             keypoints=[fo.Keypoint(points=gt_kpts_list)]
#         )
#         for model_idx, model_name in enumerate(cfg.eval.model_display_names):
#             model_kpts_list = tensor_to_keypoint_list(
#                 model_preds_np[model_idx][img_idx],
#                 cfg.data.image_orig_dims.height,
#                 cfg.data.image_orig_dims.width,
#             )
#             sample[model_name + "_prediction"] = fo.Keypoints(
#                 keypoints=[fo.Keypoint(points=model_kpts_list)]
#             )
#             # TODO: fo.Heatmap does not exist?
#             # model_heatmap = model_heatmaps_np[model_idx][img_idx][keypoint_idx]
#             # sample[model_name + "_heatmap_"] = fo.Heatmap(map=model_heatmap)

#         samples.append(sample)
#         keypoint_idx += 1
#         if keypoint_idx == num_kpts:
#             keypoint_idx = 0

#     # create a dataset and add all samples to it
#     full_dataset = fo.Dataset(cfg.eval.fifty_one_dataset_name)
#     full_dataset.add_samples(samples)

#     try:
#         full_dataset.compute_metadata(skip_failures=False)
#     except ValueError:
#         print(full_dataset.exists("metadata", False))
#         print(
#             "the above print should indicate bad image samples, e.g., with bad paths."
#         )

#     session = fo.launch_app(full_dataset, remote=True)
#     session.wait()

#     return
