#!/usr/bin/env python3
"""Persistent camera stream. Reconnects V4L2 devices after USB re-enumeration."""
from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

SIZE = (320, 320)
WIDE_SIZE = (480, 270)
MODEL = Path(__file__).resolve().parent / 'assets/models/selfie_segmenter.tflite'


def circle_alpha(size: tuple[int, int]) -> np.ndarray:
    y, x = np.ogrid[:size[1], :size[0]]
    center = (size[0] - 1) / 2
    distance = np.sqrt((x - center) ** 2 + (y - center) ** 2)
    return (np.clip(center - 1 - distance, 0, 1) * 255).astype(np.uint8)


def capture_device(name: str | None, fallback: str | None) -> str | None:
    if name and sys.platform != 'win32':
        for entry in sorted(Path('/sys/class/video4linux').glob('video*')):
            try:
                if (entry / 'name').read_text().strip() != name:
                    continue
                device = str(Path('/dev') / entry.name)
                result = subprocess.run(['v4l2-ctl', '-d', device, '--all'],
                                        capture_output=True, text=True, timeout=2)
                caps = result.stdout.split('Device Caps', 1)[-1].split('Priority:', 1)[0]
                if result.returncode == 0 and 'Video Capture' in caps:
                    return device
            except (OSError, subprocess.TimeoutExpired):
                continue
        return None
    return fallback if fallback and Path(fallback).exists() else None


def offline_frame(alpha: np.ndarray) -> bytes:
    height, width = alpha.shape
    tile = np.full((height, width, 4), (28, 28, 28, 255), dtype=np.uint8)
    for line, baseline, scale in (('CAMERA', height // 2 - 10, .85),
                                  ('DISCONNECTED', height // 2 + 28, .57)):
        text_width = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)[0][0]
        cv2.putText(tile, line, ((width - text_width) // 2, baseline),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, (245, 245, 245, 255), 2, cv2.LINE_AA)
    tile[:, :, 3] = alpha
    return tile.tobytes()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--camera')
    parser.add_argument('--name')
    parser.add_argument('--background', default='None')
    parser.add_argument('--image')
    parser.add_argument('--fps', type=int, default=10)
    parser.add_argument('--wide', action='store_true')
    args = parser.parse_args()
    if not args.camera and not args.name:
        parser.error('provide --camera or --name')
    size = WIDE_SIZE if args.wide else SIZE
    alpha = np.full((size[1], size[0]), 255, dtype=np.uint8) if args.wide else circle_alpha(size)
    fallback = offline_frame(alpha)
    backdrop = None
    segmenter = None
    if args.background != 'None':
        import mediapipe as mp
        if not args.image:
            print('Background image missing.', file=sys.stderr)
            return 1
        backdrop = cv2.imread(args.image)
        if backdrop is None:
            print(f'Cannot read background image: {args.image}', file=sys.stderr)
            return 1
        backdrop = cv2.resize(backdrop, size, interpolation=cv2.INTER_AREA)
        opts = mp.tasks.vision.ImageSegmenterOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(MODEL)),
            output_confidence_masks=True)
        segmenter = mp.tasks.vision.ImageSegmenter.create_from_options(opts)
    latest = [None]
    connected = [False]
    lock = threading.Lock()
    done = threading.Event()

    def grab_latest():
        while not done.is_set():
            device = capture_device(args.name, args.camera)
            if not device:
                with lock:
                    latest[0] = None
                    if connected[0]:
                        connected[0] = False
                        print('WEBCORDER_CAMERA:disconnected', file=sys.stderr, flush=True)
                done.wait(.5)
                continue
            cap = cv2.VideoCapture(device, cv2.CAP_DSHOW if sys.platform == 'win32' else cv2.CAP_V4L2)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
            cap.set(cv2.CAP_PROP_FPS, max(24, args.fps))
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                cap.release()
                done.wait(.5)
                continue
            while not done.is_set():
                if not Path(device).exists():
                    break
                ok, frame = cap.read()
                if not ok:
                    break
                with lock:
                    latest[0] = frame
                    if not connected[0]:
                        connected[0] = True
                        print('WEBCORDER_CAMERA:connected', file=sys.stderr, flush=True)
            cap.release()
            with lock:
                latest[0] = None
                if connected[0]:
                    connected[0] = False
                    print('WEBCORDER_CAMERA:disconnected', file=sys.stderr, flush=True)
            done.wait(.3)

    reader = threading.Thread(target=grab_latest, daemon=True)
    reader.start()
    last_packet = fallback
    period = 1 / max(1, args.fps)
    try:
        while True:
            began = time.monotonic()
            with lock:
                frame = latest[0]
                latest[0] = None
                online = connected[0]
            if not online:
                last_packet = fallback
            elif frame is not None:
                source_h, source_w = frame.shape[:2]
                ratio = size[0] / size[1]
                crop_w = min(source_w, round(source_h * ratio))
                crop_h = min(source_h, round(source_w / ratio))
                left = (source_w - crop_w) // 2
                top = (source_h - crop_h) // 2
                frame = cv2.resize(frame[top:top + crop_h, left:left + crop_w], size, interpolation=cv2.INTER_AREA)
                if segmenter is not None:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    result = segmenter.segment(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
                    mask = result.confidence_masks[0].numpy_view()
                    if mask.shape != frame.shape[:2]:
                        mask = cv2.resize(mask, size, interpolation=cv2.INTER_LINEAR)
                    mask = cv2.GaussianBlur(mask, (5, 5), 0)
                    mask = np.clip((mask - .20) / .65, 0, 1)[..., None]
                    frame = (frame.astype(np.float32) * mask + backdrop.astype(np.float32) * (1 - mask)).astype(np.uint8)
                bgra = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
                bgra[:, :, 3] = alpha
                last_packet = bgra.tobytes()
            sys.stdout.buffer.write(last_packet)
            sys.stdout.buffer.flush()
            remaining = period - (time.monotonic() - began)
            if remaining > 0:
                time.sleep(remaining)
    except (BrokenPipeError, KeyboardInterrupt):
        return 0
    finally:
        done.set()
        if segmenter is not None:
            segmenter.close()


if __name__ == '__main__':
    raise SystemExit(main())
