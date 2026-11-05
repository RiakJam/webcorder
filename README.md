# Webcorder

Small screen recorder for Kali Linux (X11), with an early Windows capture path. Python's Tkinter is the only UI dependency; FFmpeg does the recording and camera compositing.

## Kali Linux

```bash
sudo apt install ffmpeg python3-tk v4l-utils
python3 webcorder.py
```

Choose a save folder, optionally select a camera and microphone, then press **Start recording**. Recording begins after three seconds. Press **Stop** to finalize the MP4.

### Phone cameras over USB

The phone must appear as a system webcam. Connect the cable, enable the phone's **Webcam / USB webcam** mode when available, then click **Refresh**. Select the resulting `/dev/video*` device. Some Android phones support this directly; for others, use a phone webcam app that exposes a V4L2 camera on Linux. A `v4l2loopback` camera is also selectable.

An iPhone does not generally expose a standard V4L2 camera directly to Kali over USB. Use a phone camera app with a Linux V4L2 bridge, then select that virtual camera. On Windows, use a phone camera app that creates a DirectShow webcam; click **Refresh** after it connects. USB access depends on the phone OS and companion app, not on Webcorder alone.

### Backgrounds

Choose a camera, select **Green screen** or **Still scene**, and select an image. Green screen works best with an evenly lit green backdrop. Still scene learns the first camera frame as the background: step out of view during the first second, keep the camera fixed, and use even light. Both modes soften the key edge; results vary with lighting and movement. There is no guarantee that replacement is imperceptible. Background processing uses FFmpeg filters, with no neural model download.

## Windows

Install Python 3 with Tkinter and FFmpeg, add `ffmpeg` to PATH, and run `python webcorder.py`. The Windows capture and DirectShow camera path is implemented but has not been tested on Windows yet. The microphone checkbox uses the DirectShow device called `default`; if the device has another name, leave the checkbox off until audio device selection is added.

## Limits

Kali capture currently requires an X11 session. The recorder captures the full primary display. No system audio or window selection yet. MP4 files use H.264 video and optional AAC microphone audio.
# webcorder
