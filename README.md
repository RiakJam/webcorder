# Webcorder

Webcorder is an installed local web app for recording your desktop with an optional camera overlay and commentary audio. Its black and white interface opens at **http://127.0.0.1:8765/** in your browser. Recordings stay on your computer.

## Install and run on Kali Linux

Use an X11 desktop session. Install the system tools and Python environment manager:

```bash
sudo apt install ffmpeg x11-utils v4l-utils python3-tk
# Install uv if it is not already available: https://docs.astral.sh/uv/getting-started/installation/
./run.sh
```

`run.sh` installs the Python packages on first use, starts the local service in the background, and opens the browser interface. If the app is offline, run `./run.sh` again. The service log is at `~/.local/state/webcorder/server.log`. To add Webcorder to the Kali application menu, run `./install-desktop.sh`. The browser may also offer **Install app** for an app-style window; launch Webcorder again after logging back into Kali.

## Recording

The **Screen** source captures the full primary desktop. Select a camera if wanted, choose a nature background, then click **Preview** or **Start recording**. On Kali X11, the round camera appears as an always-on-top desktop window. Drag that window directly while any app is active to move it out of the way; its visible position is captured in the recording and remembered for next time. Use **Stop & save** to finalize an MP4 under `~/Videos/Webcorder`.

For reactions, choose **Local video** and select a file. The player measures the video's dimensions and available screen height, then gives the video and camera matching widths and equal-height panels while keeping the controls visible. Small and portrait videos use a narrower paired player. Select a camera to fill its square-edged half. Use **Camera on top / Camera below video** to switch their order. The camera preview starts automatically and does not save a file. **Start recording** expands the paired panels and records only that player region; **Stop & save** is available inside it. Leaving fullscreen also stops the recording. Enable **Desktop audio** to record the video's sound and **Microphone** for commentary.

For **Web video**, paste a YouTube, Vimeo, direct MP4/WebM/Ogg, or embeddable website link and click **Load in reaction player**. It uses the same 50/50 layout, camera order control, and player-only recording. The embedded site's own player provides playback controls; use Webcorder's **Full screen** button to keep the camera visible in fullscreen. Some sites, including Netflix, prevent their pages from being embedded; Webcorder shows a message and offers an explicit browser fallback for those links. It does not bypass playback or capture restrictions.

Screen Preview monitors the full desktop without saving a file. If the Webcorder page is visible on the captured display, the monitor shows itself recursively; that is the display feedback, not a recording.

## USB phone camera

A connected phone must appear to Kali as a webcam. On supported Android phones, enable **USB webcam** mode and refresh the camera list. Other Android phones and iPhones need a webcam bridge that exposes a V4L2 camera device. Webcorder remembers a selected phone camera and keeps the desktop recording active if the device disconnects; it waits for the webcam to return. The app cannot force the phone to remain in webcam mode.

## Camera backgrounds

The included nature images and uploaded backgrounds use person segmentation to place an image behind the camera subject. This processing is limited to about 10 camera frames per second to keep desktop recording responsive. Good lighting improves edges.

## Platform status

Kali X11 is the supported platform. The underlying FFmpeg command has an early Windows capture path, but the browser service and Windows camera/background workflows have not been validated. Full desktop capture is used; individual window or browser tab selection is not yet available.
