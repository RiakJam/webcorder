# Webcorder

A lightweight screen recorder for Kali Linux X11 with a black and white interface, a live preview, a draggable circular camera overlay, and person-aware background replacement. Windows screen recording is an early path.

## Run on Kali

```bash
sudo apt install ffmpeg python3-tk v4l-utils
./run.sh
```

`run.sh` creates a Python 3.13 virtual environment with MediaPipe and pyzmq through `uv` when needed. The person segmentation model is bundled in `assets/models`; recording works offline after installation.

Choose a camera, click **Preview**, and drag the camera circle to place it. The position also applies to recording and can be moved while recording. Press **Start recording** to begin after three seconds, then **Stop** to finalize the MP4. The app previews the screen at 12 fps and records at the selected rate (24 fps by default). The camera feed is processed at up to 10 fps and repeats between screen frames to reduce CPU load.

## Phone cameras over USB

The phone must appear as a system webcam. Connect the cable, enable **Webcam / USB webcam** mode on supported Android phones, then click **Refresh**. Select the resulting `/dev/video*` device. Other Android phones and iPhones need a camera app or bridge that creates a V4L2 webcam on Kali. A cable alone cannot make every phone appear as a webcam.

## Person background and wallpapers

Select a camera, choose **Person background**, and choose a custom image or one of the built-in nature wallpapers: Alpine Lake, Meadow, or Waterfall. MediaPipe identifies the person frame by frame, blends the edge, and places the wallpaper behind them. Even front lighting and a clear view of the person improve the result. The circular camera mask applies whether or not a wallpaper is selected.

## Windows status

Windows desktop and DirectShow camera capture are implemented but have not been tested. The Kali person-background and circular camera processor is not yet ported to Windows. The Windows microphone checkbox uses a DirectShow device named `default`, which may not exist on every machine.

## Limits

Kali capture requires X11 and records the full primary display. There is no system audio or window selection yet. MP4 files use H.264 video and optional AAC microphone audio.
