#!/usr/bin/env python3
"""
ETL script: Convert nuScenes dataset to Xtreme1-compatible format.

Usage:
    conda activate xtreme1-etl
    python nuscenes_to_xtreme1.py \
        --dataroot /media/danc1nc0de/Dataset/nuScenes/v1.0-mini \
        --output /media/danc1nc0de/Dataset/nuScenes/xtreme1_upload \
        --version v1.0-mini
"""

import argparse
import json
import os
import struct
import sys
import zipfile
from pathlib import Path

import numpy as np
from nuscenes.nuscenes import NuScenes
from pyquaternion import Quaternion


# nuScenes camera channel names -> Xtreme1 camera_image_N index
CAMERA_CHANNELS = [
    "CAM_FRONT",
    "CAM_FRONT_RIGHT",
    "CAM_BACK_RIGHT",
    "CAM_BACK",
    "CAM_BACK_LEFT",
    "CAM_FRONT_LEFT",
]


def write_pcd(filepath, points):
    """
    Write a point cloud to PCD file format (binary).
    points: numpy array of shape (N, 4) with columns [x, y, z, intensity].
    """
    points_f32 = points.astype(np.float32)
    with open(filepath, "wb") as f:
        header = (
            f"# .PCD v0.7 - Point Cloud Data file format\n"
            f"VERSION 0.7\n"
            f"FIELDS x y z intensity\n"
            f"SIZE 4 4 4 4\n"
            f"TYPE F F F F\n"
            f"COUNT 1 1 1 1\n"
            f"WIDTH {len(points_f32)}\n"
            f"HEIGHT 1\n"
            f"VIEWPOINT 0 0 0 1 0 0 0\n"
            f"POINTS {len(points_f32)}\n"
            f"DATA binary\n"
        )
        f.write(header.encode("ascii"))
        f.write(points_f32.tobytes())


def get_extrinsic_matrix(calib_sensor):
    """
    Build 4x4 extrinsic matrix from nuScenes calibrated_sensor.
    nuScenes calibrated_sensor gives: sensor -> ego transformation.
    Returns T_sensor_to_ego as 4x4 numpy array.
    """
    translation = calib_sensor["translation"]
    rotation = calib_sensor["rotation"]
    quat = Quaternion(rotation)
    rot_matrix = quat.rotation_matrix
    extrinsic = np.eye(4)
    extrinsic[:3, :3] = rot_matrix
    extrinsic[:3, 3] = translation
    return extrinsic


def build_camera_config(nusc, sample):
    """
    Build Xtreme1 camera_config JSON for all 6 cameras at a given sample.
    The camera_external matrix represents T_lidar_to_camera.
    T_lidar_to_camera = inv(T_camera_to_ego) * T_lidar_to_ego
    """
    cameras_config = []

    # Get LiDAR -> ego transformation
    lidar_sd_token = sample["data"]["LIDAR_TOP"]
    lidar_sd = nusc.get("sample_data", lidar_sd_token)
    lidar_calib = nusc.get("calibrated_sensor", lidar_sd["calibrated_sensor_token"])
    T_lidar_to_ego = get_extrinsic_matrix(lidar_calib)

    for cam_channel in CAMERA_CHANNELS:
        cam_sd_token = sample["data"].get(cam_channel)
        if not cam_sd_token:
            continue

        cam_sd = nusc.get("sample_data", cam_sd_token)
        if cam_sd is None:
            continue

        # Get calibrated sensor
        calib_sensor = nusc.get("calibrated_sensor", cam_sd["calibrated_sensor_token"])

        # Camera intrinsics
        intrinsic = calib_sensor["camera_intrinsic"]
        camera_internal = {
            "fx": intrinsic[0][0],
            "fy": intrinsic[1][1],
            "cx": intrinsic[0][2],
            "cy": intrinsic[1][2],
        }

        # T_camera_to_ego from nuScenes calibration
        T_camera_to_ego = get_extrinsic_matrix(calib_sensor)
        # T_ego_to_camera = inverse of T_camera_to_ego
        T_ego_to_camera = np.linalg.inv(T_camera_to_ego)
        # T_lidar_to_camera = T_ego_to_camera * T_lidar_to_ego
        T_lidar_to_camera = T_ego_to_camera @ T_lidar_to_ego

        # Xtreme1 stores in column-major order (16-element flat list)
        camera_external = T_lidar_to_camera.flatten("F").tolist()

        cameras_config.append(
            {
                "camera_internal": camera_internal,
                "width": cam_sd["width"],
                "height": cam_sd["height"],
                "camera_external": camera_external,
                "rowMajor": False,
            }
        )

    return cameras_config


def convert_nuscenes_to_xtreme1(dataroot, output_dir, version="v1.0-mini"):
    """
    Main conversion function.
    """
    nusc = NuScenes(version=version, dataroot=dataroot, verbose=True)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print(f"Found {len(nusc.scene)} scenes")

    total_frames = 0
    global_frame_idx = 0

    for scene in nusc.scene:
        scene_name = scene["name"]
        print(f"\nProcessing scene: {scene_name} ({scene['nbr_samples']} samples)")

        scene_dir = output_path / scene_name
        lidar_dir = scene_dir / "lidar_point_cloud_0"
        lidar_dir.mkdir(parents=True, exist_ok=True)

        camera_dirs = []
        for i in range(len(CAMERA_CHANNELS)):
            cam_dir = scene_dir / f"camera_image_{i}"
            cam_dir.mkdir(parents=True, exist_ok=True)
            camera_dirs.append(cam_dir)

        config_dir = scene_dir / "camera_config"
        config_dir.mkdir(parents=True, exist_ok=True)

        # Iterate through samples in the scene
        sample_token = scene["first_sample_token"]
        frame_idx = 0

        while sample_token:
            sample = nusc.get("sample", sample_token)
            frame_name = f"{global_frame_idx:05d}"

            # --- Point cloud conversion ---
            lidar_sd_token = sample["data"]["LIDAR_TOP"]
            lidar_sd = nusc.get("sample_data", lidar_sd_token)
            lidar_path = os.path.join(dataroot, lidar_sd["filename"])

            # Read .bin file (float32 array of Nx5: x, y, z, intensity, ring_index)
            bin_data = np.fromfile(lidar_path, dtype=np.float32).reshape(-1, 5)
            # Keep only x, y, z, intensity (drop ring_index)
            pcd_points = bin_data[:, :4]
            # Scale intensity to 0-255 range if it's in 0-1 range
            if pcd_points[:, 3].max() <= 1.0:
                pcd_points[:, 3] = pcd_points[:, 3] * 255.0

            pcd_file = lidar_dir / f"{frame_name}.pcd"
            write_pcd(str(pcd_file), pcd_points)

            # --- Camera images ---
            for i, cam_channel in enumerate(CAMERA_CHANNELS):
                try:
                    cam_sd_token = sample["data"].get(cam_channel)
                    if cam_sd_token:
                        cam_sd = nusc.get("sample_data", cam_sd_token)
                        cam_path = os.path.join(dataroot, cam_sd["filename"])
                        ext = os.path.splitext(cam_path)[1]
                        dest = camera_dirs[i] / f"{frame_name}{ext}"
                        if os.path.exists(cam_path):
                            import shutil
                            shutil.copy2(cam_path, str(dest))
                        else:
                            print(f"  WARNING: Camera image not found: {cam_path}")
                except Exception as e:
                    print(f"  WARNING: Failed to process camera {cam_channel}: {e}")

            # --- Camera config (using the first camera's sample_data for reference) ---
            config_file = config_dir / f"{frame_name}.json"
            # Get pose info from LIDAR_TOP sample_data
            cameras_config = build_camera_config(nusc, sample)
            with open(config_file, "w") as f:
                json.dump(cameras_config, f, indent=2)

            # --- Result annotations (nuScenes ground truth as pre-annotations) ---
            # Annotations are per-sample, not per-sample_data
            # We'll create result files during the ZIP creation phase
            # For now, just increment frame counter

            frame_idx += 1
            global_frame_idx += 1

            # Move to next sample
            sample_token = sample.get("next", "")

        print(f"  Converted {frame_idx} frames")
        total_frames += frame_idx

    print(f"\nTotal frames converted: {total_frames}")

    # Create ZIP file
    zip_file = output_path.parent / f"{output_path.name}.zip"
    print(f"\nCreating ZIP: {zip_file}")
    with zipfile.ZipFile(str(zip_file), "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(str(output_path)):
            for file in files:
                filepath = os.path.join(root, file)
                arcname = os.path.relpath(filepath, str(output_path.parent))
                zf.write(filepath, arcname)

    print(f"ZIP created: {zip_file} ({os.path.getsize(zip_file) / 1024 / 1024:.1f} MB)")
    print("\nDone! The ZIP file can be uploaded to Xtreme1 via:")
    print("  1. Upload to MinIO using presigned URL")
    print("  2. POST /data/upload with fileUrl and datasetId")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert nuScenes dataset to Xtreme1 format"
    )
    parser.add_argument(
        "--dataroot",
        default="/media/danc1nc0de/Dataset/nuScenes/v1.0-mini",
        help="Path to nuScenes dataset root",
    )
    parser.add_argument(
        "--output",
        default="/media/danc1nc0de/Dataset/nuScenes/xtreme1_upload",
        help="Output directory for Xtreme1-formatted data",
    )
    parser.add_argument(
        "--version",
        default="v1.0-mini",
        help="nuScenes dataset version",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.dataroot):
        print(f"ERROR: dataroot not found: {args.dataroot}")
        sys.exit(1)

    convert_nuscenes_to_xtreme1(args.dataroot, args.output, args.version)
