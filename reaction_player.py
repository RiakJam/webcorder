"""Video playback and web view inside the main Webcorder window."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse

import vlc


class ReactionPlayer:
    BG = '#0b0b0b'
    PANEL = '#171717'
    FIELD = '#242424'
    WHITE = '#f5f5f5'
    MUTED = '#a6a6a6'

    def __init__(self, app, parent):
        self.app = app
        self.frame = tk.Frame(parent, bg=self.PANEL)
        self.mode = None
        self.browser = None
        self.browser_window = None
        self.browser_before = set()
        self.browser_deadline = 0.0
        self.fullscreen = False
        self.seeking = False
        self.source = tk.StringVar()
        self.link = tk.StringVar()
        self.time_text = tk.StringVar(value='00:00 / 00:00')
        self.info = tk.StringVar(value='Open a local video or paste a web video link.')
        self.volume = tk.IntVar(value=100)
        self.speed = tk.StringVar(value='1.0×')

        title = tk.Frame(self.frame, bg=self.PANEL)
        title.pack(fill='x', pady=(0, 8))
        tk.Label(title, text='REACTION PLAYER', bg=self.PANEL, fg=self.WHITE,
                 font=('DejaVu Sans', 14, 'bold')).pack(side='left')
        tk.Label(title, text='VIDEO + CAMERA + SOUND', bg=self.PANEL, fg=self.MUTED,
                 font=('DejaVu Sans', 9)).pack(side='right')

        source_row = tk.Frame(self.frame, bg=self.PANEL)
        source_row.pack(fill='x', pady=(0, 8))
        tk.Entry(source_row, textvariable=self.source, bg=self.FIELD, fg=self.WHITE,
                 insertbackground=self.WHITE, relief='flat', font=('DejaVu Sans', 10)).pack(side='left', fill='x', expand=True, ipady=7)
        self.button(source_row, 'OPEN LOCAL', self.choose_local).pack(side='left', padx=(8, 0))

        link_row = tk.Frame(self.frame, bg=self.PANEL)
        link_row.pack(fill='x', pady=(0, 8))
        tk.Entry(link_row, textvariable=self.link, bg=self.FIELD, fg=self.WHITE,
                 insertbackground=self.WHITE, relief='flat', font=('DejaVu Sans', 10)).pack(side='left', fill='x', expand=True, ipady=7)
        self.button(link_row, 'OPEN LINK HERE', self.open_web).pack(side='left', padx=(8, 0))

        self.video_host = tk.Frame(self.frame, bg='#000000', highlightbackground='#363636', highlightthickness=1)
        self.video_host.pack(fill='both', expand=True)
        self.video_host.bind('<Configure>', self.resize_web)
        self.video_host.update_idletasks()
        self.instance = vlc.Instance('--quiet', '--no-video-title-show', '--avcodec-hw=none')
        self.player = self.instance.media_player_new()
        if app.root.tk.call('tk', 'windowingsystem') == 'x11':
            self.player.set_xwindow(self.video_host.winfo_id())
        elif app.root.tk.call('tk', 'windowingsystem') == 'win32':
            self.player.set_hwnd(self.video_host.winfo_id())
        self.player.audio_set_volume(100)

        seek_row = tk.Frame(self.frame, bg=self.PANEL)
        seek_row.pack(fill='x', pady=(7, 0))
        self.seek = ttk.Scale(seek_row, from_=0, to=1000)
        self.seek.pack(side='left', fill='x', expand=True)
        self.seek.bind('<ButtonPress-1>', self.seek_start)
        self.seek.bind('<ButtonRelease-1>', self.seek_end)
        tk.Label(seek_row, textvariable=self.time_text, bg=self.PANEL, fg=self.WHITE,
                 font=('DejaVu Sans', 9), width=15).pack(side='right', padx=(8, 0))

        controls = tk.Frame(self.frame, bg=self.PANEL)
        controls.pack(fill='x', pady=(8, 0))
        self.button(controls, '◀ 10s', lambda: self.jump(-10000)).pack(side='left', padx=(0, 5))
        self.button(controls, 'PLAY / PAUSE', self.toggle_play).pack(side='left', padx=(0, 5))
        self.button(controls, '10s ▶', lambda: self.jump(10000)).pack(side='left', padx=(0, 10))
        tk.Label(controls, text='VOL', bg=self.PANEL, fg=self.MUTED).pack(side='left')
        ttk.Scale(controls, from_=0, to=150, variable=self.volume,
                  command=lambda value: self.player.audio_set_volume(int(float(value))), length=95).pack(side='left', padx=6)
        ttk.Combobox(controls, textvariable=self.speed, values=['0.75×', '1.0×', '1.25×', '1.5×', '2.0×'],
                     state='readonly', width=6).pack(side='left', padx=(4, 0))
        self.speed.trace_add('write', self.change_speed)
        self.button(controls, 'FIT SCREEN', self.toggle_fullscreen).pack(side='right')

        bottom = tk.Frame(self.frame, bg=self.PANEL)
        bottom.pack(fill='x', pady=(10, 0))
        self.button(bottom, '●  RECORD REACTION', self.start_recording, primary=True).pack(side='left')
        self.button(bottom, '■  STOP', app.stop).pack(side='left', padx=7)
        tk.Label(bottom, textvariable=self.info, bg=self.PANEL, fg=self.MUTED,
                 font=('DejaVu Sans', 9), anchor='e').pack(side='right', fill='x', expand=True)
        self.frame.after(250, self.update_position)

    def button(self, parent, text, action, primary=False):
        return tk.Button(parent, text=text, command=action, relief='flat', cursor='hand2',
                         bg=self.WHITE if primary else self.FIELD,
                         fg='#111111' if primary else self.WHITE,
                         activebackground='#dddddd' if primary else '#3b3b3b',
                         activeforeground='#111111' if primary else self.WHITE,
                         font=('DejaVu Sans', 9, 'bold'), padx=10, pady=7)

    def choose_local(self):
        path = filedialog.askopenfilename(parent=self.app.root, filetypes=[
            ('Video files', '*.mp4 *.mkv *.mov *.webm *.avi *.m4v'), ('All files', '*.*')])
        if path:
            self.play_path(Path(path))

    def play_path(self, path: Path):
        if not path.is_file():
            messagebox.showerror('Video missing', str(path), parent=self.app.root)
            return
        self.close_browser()
        self.source.set(str(path))
        self.mode = 'local'
        self.player.set_media(self.instance.media_new_path(str(path)))
        self.player.play()
        self.info.set('Local video playing. Use the controls below.')

    def browser_ids(self) -> set[str]:
        found = set()
        for browser_class in ('google-chrome', 'brave-browser'):
            result = subprocess.run(['xdotool', 'search', '--class', browser_class],
                                    capture_output=True, text=True)
            found.update(token for token in result.stdout.split() if token.isdigit())
        return found

    def open_web(self):
        url = self.link.get().strip()
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            messagebox.showerror('Invalid link', 'Enter a full http:// or https:// video link.', parent=self.app.root)
            return
        if self.app.root.tk.call('tk', 'windowingsystem') != 'x11' or not shutil.which('xdotool'):
            messagebox.showerror('Web view unavailable', 'The embedded web view needs X11 and xdotool.', parent=self.app.root)
            return
        chrome = shutil.which('google-chrome') or shutil.which('brave-browser')
        if not chrome:
            messagebox.showerror('Chrome missing', 'Install Google Chrome or Brave to use the embedded web view.', parent=self.app.root)
            return
        self.player.stop()
        self.close_browser()
        self.mode = 'web'
        self.browser_before = self.browser_ids()
        profile = Path(os.environ.get('WEBCORDER_WEB_PROFILE',
                                      str(Path.home() / '.config' / 'webcorder' / 'chrome-profile')))
        profile.mkdir(parents=True, exist_ok=True)
        self.browser = subprocess.Popen([chrome, f'--user-data-dir={profile}', '--no-first-run',
                                         '--no-default-browser-check', f'--app={url}'],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.browser_deadline = time.monotonic() + 12
        self.info.set('Opening web player inside Webcorder…')
        self.frame.after(150, self.attach_browser)

    def attach_browser(self):
        if self.mode != 'web' or not self.browser or not self.frame.winfo_exists():
            return
        candidates = self.browser_ids() - self.browser_before
        # A dedicated Chrome profile creates one new visible application window.
        if candidates:
            window = max(candidates, key=self.window_area)
            host = str(self.video_host.winfo_id())
            result = subprocess.run(['xdotool', 'windowreparent', window, host], capture_output=True)
            if result.returncode == 0:
                self.browser_window = window
                self.resize_web()
                self.info.set('Web video is inside Webcorder. Use the page controls to sign in and play.')
                return
        if time.monotonic() < self.browser_deadline:
            self.frame.after(150, self.attach_browser)
        else:
            self.info.set('Could not embed the web page. Check that Chrome opens on this X11 desktop.')

    @staticmethod
    def window_area(window: str) -> int:
        result = subprocess.run(['xdotool', 'getwindowgeometry', '--shell', window], capture_output=True, text=True)
        values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        try:
            return int(values.get('WIDTH', 0)) * int(values.get('HEIGHT', 0))
        except ValueError:
            return 0

    def resize_web(self, _event=None):
        if not self.browser_window:
            return
        width = max(200, self.video_host.winfo_width())
        height = max(150, self.video_host.winfo_height())
        subprocess.run(['xdotool', 'windowmove', self.browser_window, '0', '0'], capture_output=True)
        subprocess.run(['xdotool', 'windowsize', self.browser_window, str(width), str(height)], capture_output=True)

    def web_key(self, key: str):
        if self.browser_window:
            subprocess.run(['xdotool', 'key', '--window', self.browser_window, key], capture_output=True)

    def toggle_play(self):
        if self.mode == 'web':
            self.web_key('space')
        elif not self.player.get_media():
            self.choose_local()
        elif self.player.is_playing():
            self.player.pause()
        else:
            self.player.play()

    def jump(self, milliseconds: int):
        if self.mode == 'web':
            self.web_key('Left' if milliseconds < 0 else 'Right')
        elif self.player.get_media():
            self.player.set_time(max(0, self.player.get_time() + milliseconds))

    def seek_start(self, _event):
        self.seeking = True

    def seek_end(self, _event):
        if self.mode == 'local':
            length = self.player.get_length()
            if length > 0:
                self.player.set_time(int(length * self.seek.get() / 1000))
        self.seeking = False

    def change_speed(self, *_args):
        if self.mode == 'local':
            try:
                self.player.set_rate(float(self.speed.get().rstrip('×')))
            except ValueError:
                pass

    @staticmethod
    def clock(milliseconds: int) -> str:
        seconds = max(0, milliseconds // 1000)
        return f'{seconds // 60:02d}:{seconds % 60:02d}'

    def update_position(self):
        if not self.frame.winfo_exists():
            return
        if self.mode == 'local':
            length, current = self.player.get_length(), self.player.get_time()
            self.time_text.set(f'{self.clock(current)} / {self.clock(length)}')
            if length > 0 and not self.seeking:
                self.seek.set(max(0, min(1000, current * 1000 / length)))
        else:
            self.time_text.set('WEB PLAYER' if self.mode == 'web' else '00:00 / 00:00')
        self.frame.after(250, self.update_position)

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        self.app.root.attributes('-fullscreen', self.fullscreen)

    def start_recording(self):
        self.app.desktop_audio.set(True)
        self.app.start()
        self.info.set('Recording starts in 3 seconds. Camera and desktop sound are included.')

    def close_browser(self):
        if self.browser_window:
            subprocess.run(['xdotool', 'windowclose', self.browser_window], capture_output=True)
        if self.browser:
            self.browser.terminate()
            try:
                self.browser.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.browser.kill()
        self.browser = None
        self.browser_window = None

    def close(self):
        self.close_browser()
        self.player.stop()
        self.player.release()
        self.instance.release()
        if self.fullscreen:
            self.app.root.attributes('-fullscreen', False)
        self.frame.destroy()
