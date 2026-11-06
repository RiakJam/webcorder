#!/usr/bin/env python3
"""Small FFmpeg driven screen recorder for X11 Linux and Windows."""
from __future__ import annotations

import os
import json
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    import zmq
except ImportError:
    zmq = None

IS_WINDOWS = platform.system() == "Windows"
ROOT = Path(__file__).resolve().parent
SETTINGS = Path.home() / '.config' / 'webcorder' / 'settings.json'


def cameras() -> list[tuple[str, str]]:
    if IS_WINDOWS:
        try:
            p = subprocess.run(["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"], capture_output=True, text=True, timeout=8)
            section = p.stderr.split("DirectShow video devices", 1)[-1].split("DirectShow audio devices", 1)[0]
            names = re.findall(r'"([^"]+)"', section)
            return [(n, n) for n in names if not n.startswith("@device_")]
        except (OSError, subprocess.TimeoutExpired):
            return []
    found = []
    for path in sorted(Path('/sys/class/video4linux').glob('video*')):
        dev = Path('/dev') / path.name
        try:
            name = (path / 'name').read_text().strip()
            if 'ir camera' in name.lower() or 'infrared' in name.lower():
                continue
            # V4L2 cameras often expose several metadata nodes; keep capture nodes.
            caps = subprocess.run(['v4l2-ctl', '-d', str(dev), '--all'], capture_output=True, text=True, timeout=2).stdout
            device_caps = caps.split('Device Caps', 1)[-1].split('Priority:', 1)[0]
            if caps and 'Video Capture' not in device_caps:
                continue
            found.append((name, str(dev)))
        except (OSError, subprocess.TimeoutExpired):
            if dev.exists():
                found.append((str(dev), str(dev)))
    return found


def is_phone_camera(name: str) -> bool:
    label = name.lower()
    return any(word in label for word in ('android webcam', 'iphone', 'phone', 'camo', 'iriun', 'droidcam'))


def enable_camera_speed(device: str | None) -> tuple[str, str] | None:
    """Temporarily prevent a V4L2 camera from dropping FPS in low light."""
    if IS_WINDOWS or not device or not shutil.which('v4l2-ctl'):
        return None
    try:
        result = subprocess.run(['v4l2-ctl', '-d', device, '--get-ctrl=exposure_dynamic_framerate'],
                                capture_output=True, text=True, timeout=2)
        match = re.search(r'exposure_dynamic_framerate:\s*(\d+)', result.stdout)
        if not match:
            return None
        previous = match.group(1)
        if previous == '0':
            return None
        change = subprocess.run(['v4l2-ctl', '-d', device, '--set-ctrl=exposure_dynamic_framerate=0'],
                                capture_output=True, timeout=2)
        return (device, previous) if change.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def restore_camera_speed(setting: tuple[str, str] | None) -> None:
    if setting:
        device, previous = setting
        try:
            subprocess.run(['v4l2-ctl', '-d', device, f'--set-ctrl=exposure_dynamic_framerate={previous}'],
                           capture_output=True, timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass


def supports_mjpeg(device: str | None) -> bool:
    if IS_WINDOWS or not device or not shutil.which('v4l2-ctl'):
        return False
    try:
        result = subprocess.run(['v4l2-ctl', '-d', device, '--list-formats-ext'],
                                capture_output=True, text=True, timeout=2)
        return result.returncode == 0 and "'MJPG'" in result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


def build_command(*, output: Path | None, width: int, height: int, fps: int, camera: str | None,
                  mic: bool, background: str, image: Path | None, preview: bool = False,
                  camera_pipe: int | None = None, position: tuple[int, int] | None = None,
                  zmq_port: int | None = None, camera_mjpeg: bool = False,
                  desktop_audio: bool = False,
                  screen_origin: tuple[int, int] = (0, 0),
                  camera_layout: str = 'floating', preview_height: int = 360) -> list[str]:
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-y']
    capture_fps = 15 if output is None else fps
    if IS_WINDOWS:
        if screen_origin != (0, 0):
            cmd += ['-offset_x', str(screen_origin[0]), '-offset_y', str(screen_origin[1]),
                    '-video_size', f'{width}x{height}']
        cmd += ['-f', 'gdigrab', '-framerate', str(capture_fps), '-i', 'desktop']
    else:
        display = os.environ.get('DISPLAY')
        if not display:
            raise ValueError('X11 display unavailable. Log in to an X11 session to record.')
        cmd += ['-f', 'x11grab', '-framerate', str(capture_fps), '-video_size', f'{width}x{height}',
                '-i', f'{display}+{screen_origin[0]},{screen_origin[1]}']
    cam_index = None
    if camera:
        cam_index = 1
        if camera_pipe is not None:
            cmd += ['-thread_queue_size', '2', '-f', 'rawvideo', '-pixel_format', 'bgra',
                    '-video_size', '480x270' if camera_layout in ('top', 'bottom') else '320x320',
                    '-framerate', '10', '-i', f'pipe:{camera_pipe}']
        elif IS_WINDOWS:
            cmd += ['-f', 'dshow', '-rtbufsize', '128M', '-i', f'video={camera}']
        else:
            cmd += ['-thread_queue_size', '2']
            if camera_mjpeg:
                cmd += ['-input_format', 'mjpeg']
            cmd += ['-f', 'v4l2', '-framerate', str(max(24, capture_fps)),
                    '-video_size', '640x360', '-i', camera]
    next_index = 2 if camera else 1
    audio_index = None
    if mic and output is not None:
        audio_index = next_index
        next_index += 1
        cmd += (['-f', 'dshow', '-i', 'audio=default'] if IS_WINDOWS else
                ['-thread_queue_size', '128', '-f', 'pulse', '-i', 'default'])
    desktop_audio_index = None
    if desktop_audio and output is not None:
        if IS_WINDOWS:
            raise ValueError('Desktop audio capture is available on Kali first.')
        desktop_audio_index = next_index
        next_index += 1
        cmd += ['-thread_queue_size', '128', '-f', 'pulse', '-i', '@DEFAULT_MONITOR@']
    mask_index = None
    if camera and camera_pipe is None and not IS_WINDOWS and background == 'None':
        mask_index = next_index
        next_index += 1
        cmd += ['-loop', '1', '-framerate', '30', '-i', str(ROOT / 'assets' / 'circle-mask.png')]
    image_index = None
    if camera and background != 'None' and camera_pipe is None:
        if not image or not image.is_file():
            raise ValueError('Choose a background image first.')
        image_index = next_index
        next_index += 1
        cmd += ['-loop', '1', '-i', str(image)]
    if camera and camera_layout in ('top', 'bottom'):
        screen = '[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1[screen]'.format(w=width,h=height)
        camera_filter = f'[{cam_index}:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,format=yuv420p[cam]'
        first, second = ('[cam]', '[screen]') if camera_layout == 'top' else ('[screen]', '[cam]')
        filters = [screen, camera_filter, f'{first}{second}vstack=inputs=2,format=yuv420p[composed]']
    elif camera:
        # Keep camera processing small for lower CPU use. Overlay at bottom right.
        if camera_pipe is not None:
            filters = [f'[{cam_index}:v]format=rgba[cam]']
        elif mask_index is not None:
            filters = [f'[{cam_index}:v]scale=320:320:force_original_aspect_ratio=increase,crop=320:320,format=rgba,setpts=PTS-STARTPTS+0.3/TB[face]',
                       f'[{mask_index}:v]format=gray[mask]', '[face][mask]alphamerge[cam]']
        else:
            filters = [f'[{cam_index}:v]scale=400:226:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,format=yuv420p[cam]']
        cam_label = '[cam]'
        if image_index is not None:
            filters = [f'[{cam_index}:v]scale=400:226:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,format=rgba[small]']
            if background == 'Green screen':
                filters += ['[small]chromakey=0x00ff00:0.14:0.08,format=rgba[keyed]']
            else:
                filters += ['[small]backgroundkey=threshold=0.08:similarity=0.12:blend=0.08,format=rgba[keyed]']
            filters += [f'[{image_index}:v]scale=400:226,format=rgba[bg]', '[bg][keyed]overlay=0:0:format=auto[cam]']
        x, y = position if position is not None else (max(0, width - 344), max(0, height - 344))
        filters += [f'[0:v]{"format=yuv420p," if IS_WINDOWS else ""}null[screen]',
                    f'[screen]{cam_label}overlay@camera=x={x}:y={y}:shortest=0:eof_action=repeat:repeatlast=1:format=auto[positioned]']
        if zmq_port is not None:
            filters += [f'[positioned]zmq=bind_address=tcp\\\\://127.0.0.1\\\\:{zmq_port},format=yuv420p[composed]']
        else:
            filters += ['[positioned]format=yuv420p[composed]']
    else:
        filters = ['[0:v]format=yuv420p[composed]']
    filters += ['[composed]crop=trunc(iw/2)*2:trunc(ih/2)*2[even]']
    if preview and output is not None:
        filters += ['[even]split=2[record][monitor]',
                    f'[monitor]fps=15,scale=640:{preview_height},format=rgb24[preview]']
    elif preview:
        filters += [f'[even]fps=15,scale=640:{preview_height},format=rgb24[preview]']
    else:
        filters += ['[even]null[record]']
    if audio_index is not None and desktop_audio_index is not None:
        filters += [f'[{audio_index}:a][{desktop_audio_index}:a]amix=inputs=2:duration=longest:dropout_transition=0[mixed_audio]']
    cmd += ['-filter_complex', ';'.join(filters)]
    if output is not None:
        cmd += ['-map', '[record]']
        if audio_index is not None or desktop_audio_index is not None:
            audio_label = '[mixed_audio]' if audio_index is not None and desktop_audio_index is not None else f'{audio_index if audio_index is not None else desktop_audio_index}:a'
            cmd += ['-map', audio_label, '-c:a', 'aac', '-b:a', '160k']
        cmd += ['-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency', '-crf', '25', '-pix_fmt', 'yuv420p',
                '-r', str(fps), '-movflags', '+faststart', str(output)]
    if preview:
        cmd += ['-map', '[preview]', '-an', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1']
    return cmd


class App:
    BG = '#0b0b0b'
    PANEL = '#171717'
    FIELD = '#242424'
    WHITE = '#f5f5f5'
    MUTED = '#a6a6a6'

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title('Webcorder')
        self.icon = tk.PhotoImage(file=str(ROOT / 'assets' / 'webcorder-icon.png'))
        self.root.iconphoto(True, self.icon)
        if IS_WINDOWS:
            self.root.iconbitmap(str(ROOT / 'assets' / 'webcorder-icon.ico'))
        self.root.geometry('1060x730')
        self.root.minsize(900, 650)
        self.root.configure(bg=self.BG)
        self.process: subprocess.Popen | None = None
        self.worker_process: subprocess.Popen | None = None
        self.camera_speed_setting = None
        self.worker_error = ''
        self.camera_connection = None
        self.zmq_port = None
        self.camera_x = None
        self.camera_y = None
        self.drag_origin = None
        self.mode = None
        self.started = 0.0
        self.last_frame = None
        self.finished_event = None
        self.displayed_frame = None
        self.photo = None
        self.gallery_photos = []
        self.reaction_player = None
        self.camera_map: dict[str, str] = {}
        self.output = tk.StringVar(value=str(Path.home() / 'Videos' / 'Webcorder'))
        try:
            saved_camera = json.loads(SETTINGS.read_text()).get('camera', 'No camera')
        except (OSError, ValueError):
            saved_camera = 'No camera'
        self.camera = tk.StringVar(value=saved_camera)
        self.background = tk.StringVar(value='None')
        self.image = tk.StringVar()
        self.mic = tk.BooleanVar(value=False)
        self.desktop_audio = tk.BooleanVar(value=not IS_WINDOWS)
        self.fps = tk.IntVar(value=24)
        self.status = tk.StringVar(value='READY  •  Choose your sources, then preview or record')
        self.setup_style()
        shell = tk.Frame(root, bg=self.BG, padx=28, pady=24)
        shell.pack(fill='both', expand=True)
        top = tk.Frame(shell, bg=self.BG)
        top.pack(fill='x', pady=(0, 20))
        tk.Label(top, text='WEBCORDER', bg=self.BG, fg=self.WHITE, font=('DejaVu Sans', 23, 'bold')).pack(side='left')
        tk.Label(top, text='SCREEN + CAMERA STUDIO', bg=self.BG, fg=self.MUTED, font=('DejaVu Sans', 10)).pack(side='right')
        self.reaction_button = tk.Button(top, text='OPEN REACTION PLAYER', command=self.open_reaction_player,
                                         bg=self.WHITE, fg='#111111', activebackground='#dddddd', relief='flat', cursor='hand2',
                                         font=('DejaVu Sans', 9, 'bold'), padx=12, pady=8)
        self.reaction_button.pack(side='right', padx=(0, 14))
        body = tk.Frame(shell, bg=self.BG)
        body.pack(fill='both', expand=True)
        left = tk.Frame(body, bg=self.PANEL, padx=20, pady=20, width=315)
        left.pack(side='left', fill='y', padx=(0, 18))
        left.pack_propagate(False)
        right = tk.Frame(body, bg=self.PANEL, padx=16, pady=16)
        right.pack(side='left', fill='both', expand=True)
        self.left_panel = left
        self.right_panel = right
        self.heading(left, '01  CAPTURE SOURCES')
        self.label(left, 'CAMERA')
        camera_line = tk.Frame(left, bg=self.PANEL)
        camera_line.pack(fill='x', pady=(0, 16))
        self.camera_box = ttk.Combobox(camera_line, textvariable=self.camera, state='readonly')
        self.camera_box.pack(side='left', fill='x', expand=True)
        ttk.Button(camera_line, text='↻', width=3, command=self.refresh_cameras).pack(side='left', padx=(5, 0))
        self.camera_box.bind('<<ComboboxSelected>>', self.camera_selected)
        self.label(left, 'CAMERA BACKGROUND')
        bg_box = ttk.Combobox(left, textvariable=self.background, state='readonly', values=['None', 'Person background'])
        bg_box.pack(fill='x', pady=(0, 15))
        bg_box.bind('<<ComboboxSelected>>', lambda e: self.restart_preview())
        self.label(left, 'BACKGROUND IMAGE')
        self.entry_row(left, self.image, self.choose_image, 'Choose')
        self.label(left, 'SAVE RECORDINGS TO')
        self.entry_row(left, self.output, self.choose_folder, 'Choose')
        tk.Frame(left, bg='#333333', height=1).pack(fill='x', pady=(4, 18))
        self.heading(left, '02  RECORDING OPTIONS')
        ttk.Checkbutton(left, text='Include microphone', variable=self.mic).pack(anchor='w', pady=(0, 15))
        ttk.Checkbutton(left, text='Record desktop / video sound', variable=self.desktop_audio).pack(anchor='w', pady=(0, 15))
        self.label(left, 'FRAME RATE')
        ttk.Combobox(left, textvariable=self.fps, values=[15, 24, 30], state='readonly', width=8).pack(anchor='w')
        tk.Label(left, text='Nature backgrounds use person detection. Good front lighting gives a cleaner edge.',
                 bg=self.PANEL, fg=self.MUTED, justify='left', wraplength=265, font=('DejaVu Sans', 9)).pack(anchor='w', pady=(25, 0))
        self.studio_panel = tk.Frame(right, bg=self.PANEL)
        self.studio_panel.pack(fill='both', expand=True)
        studio = self.studio_panel
        self.heading(studio, 'LIVE PREVIEW  •  DRAG THE CAMERA CIRCLE TO MOVE IT')
        self.preview_holder = tk.Frame(studio, bg='#000000', highlightbackground='#303030', highlightthickness=1)
        self.preview_holder.pack(fill='both', expand=True, pady=(12, 15))
        self.preview_label = tk.Label(self.preview_holder, text='NO PREVIEW YET\n\nClick Preview to see your screen and camera',
                                      bg='#000000', fg=self.MUTED, font=('DejaVu Sans', 12), justify='center')
        self.preview_label.pack(fill='both', expand=True)
        self.preview_label.bind('<ButtonPress-1>', self.drag_start)
        self.preview_label.bind('<B1-Motion>', self.drag_move)
        self.preview_label.bind('<ButtonRelease-1>', self.drag_end)
        controls = tk.Frame(studio, bg=self.PANEL)
        controls.pack(fill='x')
        self.preview_btn = tk.Button(controls, text='PREVIEW', command=self.toggle_preview,
                                     bg=self.FIELD, fg=self.WHITE, activebackground='#424242', activeforeground=self.WHITE,
                                     relief='flat', font=('DejaVu Sans', 10, 'bold'), padx=18, pady=12, cursor='hand2')
        self.preview_btn.pack(side='left')
        self.start_btn = tk.Button(controls, text='●  START RECORDING', command=self.start,
                                   bg=self.WHITE, fg='#111111', activebackground='#cccccc', activeforeground='#111111',
                                   relief='flat', font=('DejaVu Sans', 10, 'bold'), padx=18, pady=12, cursor='hand2')
        self.start_btn.pack(side='left', padx=8)
        self.stop_btn = tk.Button(controls, text='■  STOP', command=self.stop,
                                  bg=self.FIELD, fg=self.WHITE, activebackground='#424242', activeforeground=self.WHITE,
                                  relief='flat', font=('DejaVu Sans', 10, 'bold'), padx=18, pady=12, state='disabled')
        self.stop_btn.pack(side='left')
        self.label(studio, 'NATURE BACKGROUNDS')
        gallery = tk.Frame(studio, bg=self.PANEL)
        gallery.pack(fill='x', pady=(0, 2))
        for title, stem in [('Alpine lake', 'alpine-lake'), ('Meadow', 'sunlit-meadow'),
                            ('Waterfall', 'tropical-waterfall')]:
            thumb = tk.PhotoImage(file=str(ROOT / 'assets' / 'thumbs' / (stem + '.png')))
            self.gallery_photos.append(thumb)
            card = tk.Button(gallery, image=thumb, text=title, compound='top',
                             command=lambda name=stem: self.choose_preset(name),
                             bg=self.FIELD, fg=self.WHITE, activebackground='#444444',
                             activeforeground=self.WHITE, relief='flat', padx=4, pady=4,
                             font=('DejaVu Sans', 9), cursor='hand2')
            card.pack(side='left', padx=(0, 8))
        tk.Label(shell, textvariable=self.status, bg=self.BG, fg=self.MUTED,
                 font=('DejaVu Sans', 10), anchor='w').pack(fill='x', pady=(16, 0))
        self.refresh_cameras()
        self.root.after(80, self.show_frame)
        self.root.after(100, self.poll_finished)
        self.root.protocol('WM_DELETE_WINDOW', self.close)

    def setup_style(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TCombobox', fieldbackground=self.FIELD, background=self.FIELD,
                        foreground=self.WHITE, arrowcolor=self.WHITE, bordercolor='#444444', padding=7)
        style.map('TCombobox', fieldbackground=[('readonly', self.FIELD)], foreground=[('readonly', self.WHITE)])
        style.configure('TButton', background=self.FIELD, foreground=self.WHITE, borderwidth=0, padding=7)
        style.map('TButton', background=[('active', '#444444')])
        style.configure('TCheckbutton', background=self.PANEL, foreground=self.WHITE, font=('DejaVu Sans', 10))
        style.map('TCheckbutton', background=[('active', self.PANEL)], foreground=[('active', self.WHITE)])

    def heading(self, parent, value):
        tk.Label(parent, text=value, bg=parent['bg'], fg=self.WHITE,
                 font=('DejaVu Sans', 11, 'bold')).pack(anchor='w', pady=(0, 18))

    def label(self, parent, value):
        tk.Label(parent, text=value, bg=parent['bg'], fg=self.MUTED,
                 font=('DejaVu Sans', 9, 'bold')).pack(anchor='w', pady=(0, 7))

    def entry_row(self, parent, variable, action, button):
        row = tk.Frame(parent, bg=self.PANEL)
        row.pack(fill='x', pady=(0, 16))
        tk.Entry(row, textvariable=variable, bg=self.FIELD, fg=self.WHITE, insertbackground=self.WHITE,
                 relief='flat', highlightthickness=0, font=('DejaVu Sans', 9)).pack(side='left', fill='x', expand=True, ipady=8)
        ttk.Button(row, text=button, command=action).pack(side='left', padx=(5, 0))

    def choose_folder(self):
        selected = filedialog.askdirectory(initialdir=self.output.get())
        if selected: self.output.set(selected)

    def open_reaction_player(self):
        if self.reaction_player:
            self.show_studio()
            return
        try:
            from reaction_player import ReactionPlayer
            if self.mode == 'preview':
                self.end_process()
            player = ReactionPlayer(self, self.right_panel)
            self.left_panel.pack_forget()
            self.studio_panel.pack_forget()
            self.reaction_player = player
            player.frame.pack(fill='both', expand=True)
            self.reaction_button.configure(text='BACK TO STUDIO')
        except (ImportError, OSError, AttributeError) as exc:
            messagebox.showerror('Reaction player unavailable', f'Install VLC and python-vlc.\n{exc}')

    def show_studio(self):
        if not self.reaction_player:
            return
        self.reaction_player.close()
        self.reaction_player = None
        self.left_panel.pack(side='left', fill='y', padx=(0, 18), before=self.right_panel)
        self.studio_panel.pack(fill='both', expand=True)
        self.reaction_button.configure(text='OPEN REACTION PLAYER')

    def choose_image(self):
        selected = filedialog.askopenfilename(filetypes=[('Images', '*.png *.jpg *.jpeg *.webp')])
        if selected:
            self.image.set(selected)
            self.restart_preview()

    def choose_preset(self, stem):
        self.image.set(str(ROOT / 'assets' / 'backgrounds' / (stem + '.jpg')))
        self.background.set('Person background')
        self.status.set(f'BACKGROUND  •  {stem.replace("-", " ").title()} selected')
        self.restart_preview()

    def refresh_cameras(self):
        selected = self.camera.get()
        previous_path = self.camera_map.get(selected)
        items = cameras()
        if selected == 'No camera' and not SETTINGS.exists():
            phone = next((name for name, _ in items if is_phone_camera(name)), None)
            if phone:
                selected = phone
                self.camera.set(phone)
                self.camera_selected()
        self.camera_map = dict(items)
        values = ['No camera'] + [name for name, _ in items]
        if selected not in values and is_phone_camera(selected):
            self.camera_map[selected] = previous_path or '__waiting_phone__'
            values.append(selected)
        self.camera_box['values'] = values
        if selected not in values:
            self.camera.set('No camera')

    def camera_selected(self, _event=None):
        try:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS.write_text(json.dumps({'camera': self.camera.get()}))
        except OSError:
            pass
        self.restart_preview()

    def settings_command(self, output, camera_pipe=None, zmq_port=None):
        if not shutil.which('ffmpeg'):
            raise ValueError('FFmpeg is missing. Install FFmpeg and restart Webcorder.')
        if not IS_WINDOWS and os.environ.get('XDG_SESSION_TYPE', 'x11').lower() == 'wayland':
            raise ValueError('This version needs an X11 login session.')
        camera = self.camera_map.get(self.camera.get())
        background = self.background.get() if camera else 'None'
        image = Path(self.image.get()) if self.image.get() else None
        if IS_WINDOWS and background != 'None':
            raise ValueError('Person background is available on Kali first. Windows support is still in progress.')
        if background != 'None' and (not image or not image.is_file()):
            raise ValueError('Select a background image first.')
        width, height = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        if self.camera_x is None:
            self.camera_x = max(0, width - 344)
            self.camera_y = max(0, height - 344)
        return build_command(output=output, width=width, height=height, fps=self.fps.get(), camera=camera,
                             mic=self.mic.get(), background=background, image=image, preview=True,
                             camera_pipe=camera_pipe, position=(self.camera_x, self.camera_y), zmq_port=zmq_port,
                             camera_mjpeg=supports_mjpeg(camera) if camera_pipe is None else False,
                             desktop_audio=self.desktop_audio.get())

    def toggle_preview(self):
        if self.mode == 'preview':
            self.end_process()
            return
        if self.process: return
        self.launch('preview', None)

    def restart_preview(self):
        if self.mode == 'preview':
            self.end_process()
            self.root.after(500, self.toggle_preview)

    def start(self):
        if self.mode == 'record': return
        if self.mode == 'preview': self.end_process()
        try:
            folder = Path(self.output.get()).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            output = folder / f'Webcorder-{time.strftime("%Y%m%d-%H%M%S")}.mp4'
        except OSError as exc:
            messagebox.showerror('Cannot record', str(exc)); return
        self.pending = output
        self.start_btn.configure(state='disabled')
        self.status.set('STARTING  •  Recording begins in 3 seconds')
        self.root.after(3000, self.launch_pending)

    def launch_pending(self):
        if not hasattr(self, 'pending'): return
        output = self.pending
        del self.pending
        if self.process and self.process.poll() is None:
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
        self.launch('record', output)

    def launch(self, mode, output):
        self.refresh_cameras()
        read_fd = write_fd = None
        worker = None
        speed_setting = None
        camera = self.camera_map.get(self.camera.get())
        phone = is_phone_camera(self.camera.get())
        use_worker = bool(camera and not IS_WINDOWS and (self.background.get() != 'None' or phone))
        try:
            if camera and not IS_WINDOWS:
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', 0))
                    port = sock.getsockname()[1]
            else:
                port = None
            speed_setting = enable_camera_speed(camera)
            if use_worker:
                worker_python = ROOT / '.venv' / 'bin' / 'python'
                if not worker_python.is_file():
                    raise ValueError('Camera processor is missing. Run ./run.sh to set it up.')
                read_fd, write_fd = os.pipe()
                command = self.settings_command(output, camera_pipe=read_fd, zmq_port=port)
                worker_cmd = [str(worker_python), str(ROOT / 'camera_worker.py'), '--camera', camera,
                              '--name', self.camera.get(), '--background', self.background.get(), '--fps', '10']
                if self.image.get(): worker_cmd += ['--image', self.image.get()]
                worker = subprocess.Popen(worker_cmd, stdin=subprocess.DEVNULL, stdout=write_fd,
                                          stderr=subprocess.PIPE)
                os.close(write_fd); write_fd = None
            else:
                command = self.settings_command(output, zmq_port=port)
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,
                                       pass_fds=(read_fd,) if read_fd is not None else (),
                                       creationflags=subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0)
            if read_fd is not None:
                os.close(read_fd); read_fd = None
        except (OSError, ValueError) as exc:
            if worker: worker.terminate()
            restore_camera_speed(speed_setting)
            if read_fd is not None: os.close(read_fd)
            if write_fd is not None: os.close(write_fd)
            self.start_btn.configure(state='normal')
            messagebox.showerror('Cannot start', str(exc)); return
        self.worker_process = worker
        self.worker_error = ''
        self.camera_connection = 'disconnected' if phone and not Path(camera).exists() else None
        if worker:
            threading.Thread(target=self.read_worker_messages, args=(worker,), daemon=True).start()
        self.camera_speed_setting = speed_setting
        self.zmq_port = port
        self.process = process
        self.stop_requested = False
        self.mode = mode
        self.current_output = output
        self.started = time.monotonic()
        self.start_btn.configure(state='disabled' if mode == 'record' else 'normal')
        self.stop_btn.configure(state='normal' if mode == 'record' else 'disabled')
        self.preview_btn.configure(text='STOP PREVIEW' if mode == 'preview' else 'PREVIEW',
                                   state='normal' if mode == 'preview' else 'disabled')
        self.set_live_status()
        threading.Thread(target=self.read_frames, args=(process,), daemon=True).start()
        threading.Thread(target=self.watch, args=(process, mode, output), daemon=True).start()
        self.tick()

    def drag_start(self, event):
        if not self.process or not self.camera_map.get(self.camera.get()): return
        width, height = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        left = (self.preview_label.winfo_width() - 640) / 2
        top = (self.preview_label.winfo_height() - 360) / 2
        center_x = left + (self.camera_x + 160) * 640 / width
        center_y = top + (self.camera_y + 160) * 360 / height
        radius = 160 * min(640 / width, 360 / height)
        if (event.x - center_x) ** 2 + (event.y - center_y) ** 2 <= radius ** 2:
            self.drag_origin = (event.x, event.y, self.camera_x, self.camera_y)

    def drag_move(self, event):
        if not self.drag_origin: return
        start_x, start_y, old_x, old_y = self.drag_origin
        width, height = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.camera_x = max(0, min(width - 320, int(old_x + (event.x - start_x) * width / 640)))
        self.camera_y = max(0, min(height - 320, int(old_y + (event.y - start_y) * height / 360)))
        self.send_position()

    def drag_end(self, event):
        if self.drag_origin:
            self.drag_move(event)
            self.drag_origin = None

    def send_position(self):
        if not self.zmq_port or zmq is None: return
        self.pending_position = (self.camera_x, self.camera_y, self.zmq_port)
        if getattr(self, 'position_thread', None) and self.position_thread.is_alive(): return
        self.position_thread = threading.Thread(target=self.position_sender, daemon=True)
        self.position_thread.start()

    def position_sender(self):
        context = zmq.Context.instance()
        while getattr(self, 'pending_position', None):
            x, y, port = self.pending_position
            self.pending_position = None
            sock = context.socket(zmq.REQ)
            sock.setsockopt(zmq.LINGER, 0)
            sock.setsockopt(zmq.RCVTIMEO, 600)
            sock.connect(f'tcp://127.0.0.1:{port}')
            try:
                for axis, value in [('x', x), ('y', y)]:
                    sock.send_string(f'overlay@camera {axis} {value}')
                    sock.recv_string()
            except zmq.ZMQError:
                pass
            finally:
                sock.close()

    def read_frames(self, process):
        frame_size = 640 * 360 * 3
        while True:
            frame = process.stdout.read(frame_size)
            if len(frame) != frame_size: break
            self.last_frame = (process, frame)

    def show_frame(self):
        current = self.last_frame
        if current and current is not self.displayed_frame and current[0] is self.process:
            ppm = b'P6\n640 360\n255\n' + current[1]
            self.photo = tk.PhotoImage(data=ppm, format='PPM')
            self.preview_label.configure(image=self.photo, text='')
            self.displayed_frame = current
        self.root.after(80, self.show_frame)

    def watch(self, process, mode, output):
        error = process.stderr.read().decode('utf-8', errors='replace')[-2500:]
        code = process.wait()
        self.finished_event = (process, mode, output, code, error)

    def read_worker_messages(self, worker):
        for raw in worker.stderr:
            line = raw.decode('utf-8', errors='replace').strip()
            if line.startswith('WEBCORDER_CAMERA:'):
                if self.worker_process is worker:
                    self.camera_connection = line.split(':', 1)[1]
            else:
                self.worker_error = (self.worker_error + '\n' + line)[-2500:]

    def set_live_status(self):
        if not self.process or self.stop_requested:
            return
        if self.mode == 'record':
            seconds = int(time.monotonic() - self.started)
            prefix = f'RECORDING  •  {seconds // 60:02d}:{seconds % 60:02d}'
        elif self.mode == 'preview':
            prefix = 'PREVIEW  •  Drag the camera circle to move it'
        else:
            return
        if is_phone_camera(self.camera.get()) and self.camera_connection == 'disconnected':
            prefix += '  •  Phone camera disconnected; waiting for USB webcam mode'
        self.status.set(prefix)

    def poll_finished(self):
        event = self.finished_event
        if event:
            self.finished_event = None
            self.finished(*event)
        elif self.process and is_phone_camera(self.camera.get()):
            self.set_live_status()
        self.root.after(100, self.poll_finished)

    def tick(self):
        if self.mode == 'record' and self.process:
            self.set_live_status()
            self.root.after(1000, self.tick_time)

    def tick_time(self):
        if self.mode == 'record' and self.process:
            self.set_live_status()
            self.root.after(1000, self.tick_time)

    def end_process(self):
        if self.process and self.process.poll() is None:
            self.stop_requested = True
            try:
                if self.worker_process and not IS_WINDOWS:
                    self.process.send_signal(signal.SIGINT)
                else:
                    self.process.stdin.write(b'q\n')
                    self.process.stdin.flush()
            except OSError: pass

    def stop(self):
        if self.mode == 'record':
            self.status.set('SAVING  •  Finalizing MP4')
            self.stop_btn.configure(state='disabled')
            self.end_process()

    def finished(self, process, mode, output, code, error):
        if self.process is not process: return
        worker = self.worker_process
        self.worker_process = None
        self.zmq_port = None
        if worker:
            if worker.poll() is None: worker.terminate()
            try: worker.wait(timeout=1)
            except subprocess.TimeoutExpired: worker.kill()
            worker_error = self.worker_error[-700:]
            if worker.returncode not in (0, -15) and worker_error:
                error += '\nCamera: ' + worker_error
        restore_camera_speed(self.camera_speed_setting)
        self.camera_speed_setting = None
        self.process = None
        self.mode = None
        self.start_btn.configure(state='normal')
        self.stop_btn.configure(state='disabled')
        self.preview_btn.configure(text='PREVIEW', state='normal')
        stopped_ok = code == 0 or (code == 255 and self.stop_requested)
        if mode == 'record' and stopped_ok and output and output.exists() and output.stat().st_size > 1000:
            self.status.set(f'SAVED  •  {output}')
        elif mode == 'preview' and stopped_ok:
            self.status.set('READY  •  Preview stopped')
        else:
            self.status.set('ERROR  •  Capture stopped')
            messagebox.showerror('FFmpeg error', error or f'FFmpeg exited with code {code}')

    def close(self):
        if self.process and self.process.poll() is None:
            self.end_process()
            self.root.after(300, self.close)
        else:
            if self.worker_process and self.worker_process.poll() is None:
                self.worker_process.terminate()
            restore_camera_speed(self.camera_speed_setting)
            if self.reaction_player:
                self.reaction_player.close()
            self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    App(root)
    root.mainloop()
