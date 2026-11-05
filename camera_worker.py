#!/usr/bin/env python3
"""Capture and segment a camera into a small BGRA stream for FFmpeg."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np

SIZE = (320, 320)
MODEL = Path(__file__).resolve().parent / 'assets/models/selfie_segmenter.tflite'


def circle_alpha() -> np.ndarray:
    y, x = np.ogrid[:SIZE[1], :SIZE[0]]
    distance = np.sqrt((x - (SIZE[0] - 1) / 2) ** 2 + (y - (SIZE[1] - 1) / 2) ** 2)
    # Feather the outside edge for a circular camera tile.
    radius = min(SIZE) / 2 - 2
    return (np.clip(radius + 1.5 - distance, 0, 1) * 255).astype(np.uint8)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--camera', required=True)
    parser.add_argument('--background', default='None')
    parser.add_argument('--image')
    parser.add_argument('--fps', type=int, default=12)
    args = parser.parse_args()
    camera = int(args.camera) if args.camera.isdigit() else args.camera
    capture = cv2.VideoCapture(camera, cv2.CAP_DSHOW if sys.platform == 'win32' else cv2.CAP_V4L2)
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
    capture.set(cv2.CAP_PROP_FPS, args.fps)
    if not capture.isOpened():
        print(f'Cannot open camera: {args.camera}', file=sys.stderr)
        return 1
    backdrop = None
    segmenter = None
    if args.background != 'None':
        if not args.image:
            print('Background image missing.', file=sys.stderr)
            return 1
        backdrop = cv2.imread(args.image)
        if backdrop is None:
            print(f'Cannot read background image: {args.image}', file=sys.stderr)
            return 1
        backdrop = cv2.resize(backdrop, SIZE, interpolation=cv2.INTER_AREA)
        opts = mp.tasks.vision.ImageSegmenterOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(MODEL)),
            output_confidence_masks=True)
        segmenter = mp.tasks.vision.ImageSegmenter.create_from_options(opts)
    alpha_circle = circle_alpha()
    frame_period = 1 / max(1, args.fps)
    try:
        while True:
            began = time.monotonic()
            ok, frame = capture.read()
            if not ok:
                print('Camera stopped sending frames.', file=sys.stderr)
                return 1
            source_h, source_w = frame.shape[:2]
            square = min(source_h, source_w)
            left = (source_w - square) // 2
            top = (source_h - square) // 2
            frame = cv2.resize(frame[top:top + square, left:left + square], SIZE, interpolation=cv2.INTER_AREA)
            if segmenter is not None:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                result = segmenter.segment(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
                mask = result.confidence_masks[0].numpy_view()
                if mask.shape != frame.shape[:2]:
                    mask = cv2.resize(mask, SIZE, interpolation=cv2.INTER_LINEAR)
                mask = cv2.GaussianBlur(mask, (5, 5), 0)
                mask = np.clip((mask - 0.20) / 0.65, 0, 1)[..., None]
                frame = (frame.astype(np.float32) * mask + backdrop.astype(np.float32) * (1 - mask)).astype(np.uint8)
            bgra = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
            bgra[:, :, 3] = alpha_circle
            sys.stdout.buffer.write(bgra.tobytes())
            sys.stdout.buffer.flush()
            remaining = frame_period - (time.monotonic() - began)
            if remaining > 0: time.sleep(remaining)
    except (BrokenPipeError, KeyboardInterrupt):
        return 0
    finally:
        capture.release()
        if segmenter is not None: segmenter.close()


if __name__ == '__main__':
    raise SystemExit(main())
