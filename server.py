#!/usr/bin/env python3
"""Local Webcorder service. Binds only to localhost; the browser is the UI."""
from __future__ import annotations

import io
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image

from webcorder import (ROOT, build_command, cameras, enable_camera_speed,
                       is_phone_camera, restore_camera_speed)

WEB = ROOT / 'web'
RECORDINGS = Path.home() / 'Videos' / 'Webcorder'
CUSTOM_BACKGROUNDS = Path.home() / '.config' / 'webcorder' / 'backgrounds'
HOST, PORT = '127.0.0.1', 8765


def display_size() -> tuple[int, int]:
    if sys.platform == 'win32':
        import ctypes
        user = ctypes.windll.user32
        return user.GetSystemMetrics(0), user.GetSystemMetrics(1)
    try:
        result = subprocess.run(['xdpyinfo'], capture_output=True, text=True, timeout=3)
        match = re.search(r'dimensions:\s*(\d+)x(\d+)', result.stdout)
        if match:
            return int(match.group(1)), int(match.group(2))
    except (OSError, subprocess.TimeoutExpired):
        pass
    return 1280, 720


class CaptureManager:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.frame_ready = threading.Condition()
        self.frame_version = 0
        self.jpeg = b''
        self.camera_ready = threading.Condition()
        self.camera_version = 0
        self.camera_jpeg = b''
        self.process = None
        self.worker = None
        self.camera_setting = None
        self.camera_state = 'none'
        self.mode = 'idle'
        self.started = 0.0
        self.output = None
        self.latest_file = None
        self.error = ''
        self.stopping = False
        self.port = None
        self.width, self.height = display_size()
        self.x, self.y = max(0, self.width - 344), max(0, self.height - 344)

    def status(self) -> dict:
        with self.lock:
            return {
                'mode': self.mode,
                'cameraState': self.camera_state,
                'elapsed': int(time.monotonic() - self.started) if self.mode in ('preview', 'record') else 0,
                'error': self.error,
                'latestFile': self.latest_file.name if self.latest_file else None,
                'output': str(self.latest_file) if self.latest_file else None,
                'width': self.width,
                'height': self.height,
                'cameraX': self.x,
                'cameraY': self.y,
            }

    def start(self, settings: dict) -> dict:
        with self.lock:
            if self.mode == 'record':
                raise ValueError('A recording is already in progress. Stop it before starting another capture.')
        self.stop()
        mode = settings.get('mode')
        if mode not in ('preview', 'record'):
            raise ValueError('Choose preview or record.')
        source = settings.get('source', 'screen')
        if source not in ('screen', 'local', 'web'):
            raise ValueError('Invalid capture source.')
        camera_name = str(settings.get('camera') or '')
        available = dict(cameras())
        camera = available.get(camera_name)
        phone = bool(camera_name and is_phone_camera(camera_name))
        if camera_name and not camera and not phone:
            raise ValueError('Selected camera is unavailable. Refresh the camera list.')
        if phone and not camera:
            camera = '__waiting_phone__'
        background = str(settings.get('background') or '')
        image = None
        if background:
            if background.startswith('custom:'):
                name = background.removeprefix('custom:')
                if Path(name).name != name:
                    raise ValueError('Invalid background name.')
                image = CUSTOM_BACKGROUNDS / name
            else:
                name = Path(background).name
                image = WEB / 'assets' / 'backgrounds' / name
            if not image.is_file():
                raise ValueError('Background image is missing.')
            if not camera:
                raise ValueError('Select a camera before applying a camera background.')
        fps = int(settings.get('fps') or 24)
        if fps not in (15, 24, 30):
            raise ValueError('Frame rate must be 15, 24, or 30.')
        capture_width, capture_height = self.width, self.height
        capture_origin = (0, 0)
        if mode == 'record' and source in ('local', 'web'):
            rectangle = settings.get('captureRect')
            if not isinstance(rectangle, dict):
                raise ValueError('Open the updated Webcorder page to record the reaction player.')
            x, y, width, height = (int(rectangle[key]) for key in ('x', 'y', 'width', 'height'))
            if (x < 0 or y < 0 or width < 160 or height < 160 or
                    x + width > self.width or y + height > self.height):
                raise ValueError('The reaction player must be fully visible on the captured display.')
            capture_origin = (x, y)
            capture_width, capture_height = width, height
        output = None
        if mode == 'record':
            folder = Path(str(settings.get('folder') or RECORDINGS)).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            output = folder / f'Webcorder-{time.strftime("%Y%m%d-%H%M%S")}.mp4'
        player_camera = bool(camera and source in ('local', 'web'))
        screen_float = bool(camera and source == 'screen' and sys.platform != 'win32')
        use_worker = bool(camera and sys.platform != 'win32' and not screen_float and (phone or image or player_camera))
        if player_camera and sys.platform == 'win32':
            raise ValueError('Reaction camera preview is available on Kali first.')
        read_fd = write_fd = None
        worker = process = None
        speed_setting = None
        port = None
        try:
            if camera and sys.platform != 'win32':
                with socket.socket() as sock:
                    sock.bind((HOST, 0))
                    port = sock.getsockname()[1]
            if camera and camera != '__waiting_phone__':
                speed_setting = enable_camera_speed(camera)
            if screen_float:
                worker_python = ROOT / '.venv' / 'bin' / 'python'
                if not worker_python.is_file():
                    raise ValueError('Camera processor is missing. Run the Webcorder installer.')
                worker_args = [str(worker_python), str(ROOT / 'floating_camera.py'),
                               '--camera', camera, '--name', camera_name,
                               '--x', str(self.x), '--y', str(self.y)]
                if image:
                    worker_args += ['--image', str(image)]
                worker = subprocess.Popen(worker_args, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
            elif use_worker:
                if not player_camera:
                    read_fd, write_fd = os.pipe()
                worker_python = ROOT / '.venv' / 'bin' / 'python'
                if not worker_python.is_file():
                    raise ValueError('Camera processor is missing. Run the Webcorder installer.')
                worker_args = [str(worker_python), str(ROOT / 'camera_worker.py'),
                               '--camera', camera, '--name', camera_name, '--fps', '10',
                               '--background', 'Person background' if image else 'None']
                if player_camera:
                    worker_args += ['--wide']
                if image:
                    worker_args += ['--image', str(image)]
                worker = subprocess.Popen(worker_args, stdout=subprocess.PIPE if player_camera else write_fd, stderr=subprocess.PIPE,
                                          stdin=subprocess.DEVNULL)
                if write_fd is not None:
                    os.close(write_fd)
                    write_fd = None
            if not (mode == 'preview' and source in ('local', 'web')):
                capture_preview = source == 'screen'
                command = build_command(
                    output=output, width=capture_width, height=capture_height, fps=fps,
                    camera=None if (player_camera or screen_float) else camera, mic=bool(settings.get('mic')), desktop_audio=bool(settings.get('desktopAudio', True)),
                    background='Person background' if image else 'None', image=image,
                    preview=capture_preview, camera_pipe=read_fd, position=(self.x, self.y), zmq_port=port,
                    screen_origin=capture_origin,
                    camera_mjpeg=False)
                # The low-latency FFmpeg path for a built-in camera can use MJPEG.
                if camera and not use_worker and not screen_float and sys.platform != 'win32':
                    from webcorder import supports_mjpeg
                    if supports_mjpeg(camera):
                        command = build_command(
                            output=output, width=self.width, height=self.height, fps=fps,
                            camera=camera, mic=bool(settings.get('mic')),
                            desktop_audio=bool(settings.get('desktopAudio', True)),
                            background='None', image=None, preview=capture_preview,
                            screen_origin=capture_origin,
                            position=(self.x, self.y), zmq_port=port, camera_mjpeg=True)
                process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                           stdout=subprocess.PIPE if capture_preview else subprocess.DEVNULL,
                                           stderr=subprocess.PIPE, pass_fds=(read_fd,) if read_fd is not None else (),
                                           creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            if read_fd is not None:
                os.close(read_fd)
                read_fd = None
        except Exception:
            if worker:
                worker.terminate()
            if read_fd is not None:
                os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)
            restore_camera_speed(speed_setting)
            raise
        with self.lock:
            self.process = process
            self.worker = worker
            self.camera_setting = speed_setting
            self.camera_state = 'waiting' if phone else ('connecting' if camera else 'none')
            self.mode = mode
            self.started = time.monotonic()
            self.output = output
            self.error = ''
            self.stopping = False
            self.port = port
            if player_camera:
                with self.camera_ready:
                    self.camera_jpeg = b''
                    self.camera_version += 1
                    self.camera_ready.notify_all()
        if process:
            if source == 'screen':
                threading.Thread(target=self.read_preview, args=(process,), daemon=True).start()
            threading.Thread(target=self.watch, args=(process,), daemon=True).start()
        if worker:
            threading.Thread(target=self.read_worker, args=(worker,), daemon=True).start()
            if player_camera:
                threading.Thread(target=self.read_camera, args=(worker,), daemon=True).start()
        return self.status()

    def read_camera(self, worker) -> None:
        width, height = 480, 270
        size = width * height * 4
        while True:
            frame = worker.stdout.read(size)
            if len(frame) != size:
                break
            image = Image.frombytes('RGBA', (width, height), frame, 'raw', 'BGRA').convert('RGB')
            buffer = io.BytesIO()
            image.save(buffer, format='JPEG', quality=78)
            with self.camera_ready:
                self.camera_jpeg = buffer.getvalue()
                self.camera_version += 1
                self.camera_ready.notify_all()

    def read_preview(self, process) -> None:
        size = 640 * 360 * 3
        while True:
            frame = process.stdout.read(size)
            if len(frame) != size:
                break
            image = Image.frombytes('RGB', (640, 360), frame)
            buffer = io.BytesIO()
            image.save(buffer, format='JPEG', quality=74, optimize=False)
            with self.frame_ready:
                self.jpeg = buffer.getvalue()
                self.frame_version += 1
                self.frame_ready.notify_all()

    def read_worker(self, worker) -> None:
        for raw in worker.stderr:
            line = raw.decode('utf-8', errors='replace').strip()
            if line.startswith('WEBCORDER_CAMERA:'):
                with self.lock:
                    if self.worker is worker:
                        self.camera_state = line.split(':', 1)[1]
            elif 'error' in line.lower():
                with self.lock:
                    if self.worker is worker:
                        self.error = line[-400:]
        with self.lock:
            if self.worker is worker and self.mode in ('preview', 'record'):
                self.camera_state = 'disconnected'
                self.error = self.error or 'The camera processor stopped.'

    def watch(self, process) -> None:
        error = process.stderr.read().decode('utf-8', errors='replace')[-2400:]
        code = process.wait()
        with self.lock:
            if self.process is not process:
                return
            worker = self.worker
            setting = self.camera_setting
            output = self.output
            stopping = self.stopping
            self.process = None
            self.worker = None
            self.camera_setting = None
            self.port = None
            self.mode = 'idle'
            self.camera_state = 'none'
            if output and output.exists() and output.stat().st_size > 1000:
                self.latest_file = output
            elif code not in (0, 255) or not stopping:
                useful = [line for line in error.splitlines() if 'not enough frames to estimate rate' not in line]
                self.error = '\n'.join(useful[-4:]) or f'Capture stopped (code {code}).'
        if worker and worker.poll() is None:
            worker.terminate()
        restore_camera_speed(setting)
        with self.frame_ready:
            self.frame_ready.notify_all()

    def stop(self) -> dict:
        with self.lock:
            process = self.process
            worker = self.worker
            if not process:
                if worker and worker.poll() is None:
                    worker.terminate()
                    try:
                        worker.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        worker.kill()
                restore_camera_speed(self.camera_setting)
                self.worker = None
                self.camera_setting = None
                self.mode = 'idle'
                self.camera_state = 'none'
                self.port = None
                with self.camera_ready:
                    self.camera_jpeg = b''
                    self.camera_version += 1
                    self.camera_ready.notify_all()
                return self.status()
            self.stopping = True
        if process.poll() is None:
            try:
                if worker and sys.platform != 'win32':
                    process.send_signal(signal.SIGINT)
                else:
                    process.stdin.write(b'q\n')
                    process.stdin.flush()
            except OSError:
                pass
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        deadline = time.monotonic() + 2
        while self.process is process and time.monotonic() < deadline:
            time.sleep(.05)
        return self.status()

    def set_position(self, x: int, y: int) -> dict:
        self.x = min(max(0, x), max(0, self.width - 320))
        self.y = min(max(0, y), max(0, self.height - 320))
        if self.port:
            try:
                import zmq
                context = zmq.Context.instance()
                sock = context.socket(zmq.REQ)
                sock.setsockopt(zmq.LINGER, 0)
                sock.setsockopt(zmq.RCVTIMEO, 500)
                sock.connect(f'tcp://{HOST}:{self.port}')
                for axis, value in (('x', self.x), ('y', self.y)):
                    sock.send_string(f'overlay@camera {axis} {value}')
                    sock.recv_string()
                sock.close()
            except Exception:
                pass
        return self.status()


CAPTURE = CaptureManager()


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      '.mjs': 'text/javascript', '.wasm': 'application/wasm',
                      '.webmanifest': 'application/manifest+json'}
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def log_message(self, *_args):
        pass

    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()

    def json_response(self, code: int, payload: dict):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/health':
            return self.json_response(200, {'app': 'webcorder', 'version': 2})
        if path == '/api/cameras':
            return self.json_response(200, {'cameras': [{'name': name, 'device': device} for name, device in cameras()]})
        if path == '/api/status':
            return self.json_response(200, CAPTURE.status())
        if path == '/api/preview':
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            seen = -1
            try:
                while True:
                    with CAPTURE.frame_ready:
                        CAPTURE.frame_ready.wait_for(lambda: CAPTURE.frame_version != seen, timeout=10)
                        seen = CAPTURE.frame_version
                        frame = CAPTURE.jpeg
                    if frame:
                        self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(frame)).encode() + b'\r\n\r\n')
                        self.wfile.write(frame + b'\r\n')
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
        if path == '/api/camera-preview':
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            seen = -1
            try:
                while True:
                    with CAPTURE.camera_ready:
                        CAPTURE.camera_ready.wait_for(lambda: CAPTURE.camera_version != seen, timeout=10)
                        seen = CAPTURE.camera_version
                        frame = CAPTURE.camera_jpeg
                    if frame:
                        self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(frame)).encode() + b'\r\n\r\n')
                        self.wfile.write(frame + b'\r\n')
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
        if path.startswith('/recordings/'):
            name = unquote(path.removeprefix('/recordings/'))
            if Path(name).name != name or not name.endswith('.mp4'):
                return self.send_error(404)
            file = RECORDINGS / name
            if not file.is_file():
                return self.send_error(404)
            self.send_response(200)
            self.send_header('Content-Type', 'video/mp4')
            self.send_header('Content-Disposition', f'attachment; filename="{name}"')
            self.send_header('Content-Length', str(file.stat().st_size))
            self.end_headers()
            with file.open('rb') as source:
                while chunk := source.read(1024 * 1024):
                    self.wfile.write(chunk)
            return
        return super().do_GET()

    def do_POST(self):
        origin = self.headers.get('Origin')
        if origin and origin not in (f'http://{HOST}:{PORT}', f'http://localhost:{PORT}'):
            return self.json_response(403, {'error': 'This local service only accepts its own web app.'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size <= 0 or size > 12_000_000:
                raise ValueError('Request size is invalid or too large.')
            raw = self.rfile.read(size)
            path = urlparse(self.path).path
            if path == '/api/background':
                image = Image.open(io.BytesIO(raw)).convert('RGB')
                image.thumbnail((1920, 1080))
                CUSTOM_BACKGROUNDS.mkdir(parents=True, exist_ok=True)
                name = f'background-{int(time.time() * 1000)}.jpg'
                image.save(CUSTOM_BACKGROUNDS / name, quality=88)
                return self.json_response(200, {'id': 'custom:' + name})
            data = json.loads(raw)
            if path == '/api/start':
                return self.json_response(200, CAPTURE.start(data))
            if path == '/api/stop':
                return self.json_response(200, CAPTURE.stop())
            if path == '/api/position':
                return self.json_response(200, CAPTURE.set_position(int(data['x']), int(data['y'])))
            self.send_error(404)
        except (ValueError, OSError, KeyError) as exc:
            self.json_response(400, {'error': str(exc)})
        except Exception as exc:
            self.json_response(500, {'error': str(exc)})


def main():
    url = f'http://{HOST}:{PORT}/'
    try:
        with urllib.request.urlopen(f'{url}api/health', timeout=.5) as response:
            if json.load(response).get('app') == 'webcorder':
                webbrowser.open(url)
                print(url, flush=True)
                return
    except Exception:
        pass
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f'Webcorder: {url}', flush=True)
    webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        CAPTURE.stop()
        server.server_close()


if __name__ == '__main__':
    main()
