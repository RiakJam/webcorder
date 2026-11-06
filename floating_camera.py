#!/usr/bin/env python3
"""Movable round camera window for X11 screen capture."""
from __future__ import annotations

import argparse
import ctypes
import json
import signal
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageTk

ROOT = Path(__file__).resolve().parent
POSITION = Path.home() / '.config' / 'webcorder' / 'floating-camera.json'
SIZE = 320
FRAME_BYTES = SIZE * SIZE * 4


def shape_circle(window: int) -> None:
    """Clip the X11 window to a circle, including its clickable area."""
    x11 = ctypes.CDLL('libX11.so.6')
    xext = ctypes.CDLL('libXext.so.6')
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XCreateBitmapFromData.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                         ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint]
    x11.XCreateBitmapFromData.restype = ctypes.c_ulong
    x11.XFreePixmap.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                              ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
                              ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)), ctypes.POINTER(ctypes.c_uint)]
    x11.XQueryTree.restype = ctypes.c_int
    x11.XFree.argtypes = [ctypes.c_void_p]
    xext.XShapeCombineMask.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_ulong, ctypes.c_int]
    display = x11.XOpenDisplay(None)
    if not display:
        raise RuntimeError('X11 display unavailable for floating camera')
    try:
        row_bytes = (SIZE + 7) // 8
        bits = bytearray(row_bytes * SIZE)
        radius2 = (SIZE / 2 - 2) ** 2
        for y in range(SIZE):
            for x in range(SIZE):
                if (x - (SIZE - 1) / 2) ** 2 + (y - (SIZE - 1) / 2) ** 2 <= radius2:
                    bits[y * row_bytes + x // 8] |= 1 << (x % 8)
        mask = x11.XCreateBitmapFromData(display, window, bytes(bits), SIZE, SIZE)
        if not mask:
            raise RuntimeError('Could not create round camera mask')
        try:
            targets = [window]
            root_id = ctypes.c_ulong()
            parent_id = ctypes.c_ulong()
            children = ctypes.POINTER(ctypes.c_ulong)()
            count = ctypes.c_uint()
            if x11.XQueryTree(display, window, ctypes.byref(root_id), ctypes.byref(parent_id),
                              ctypes.byref(children), ctypes.byref(count)):
                if parent_id.value and parent_id.value != root_id.value:
                    targets.append(parent_id.value)
                if children:
                    x11.XFree(children)
            for target in targets:
                xext.XShapeCombineMask(display, target, 0, 0, 0, mask, 0)
                xext.XShapeCombineMask(display, target, 2, 0, 0, mask, 0)
            x11.XFlush(display)
        finally:
            x11.XFreePixmap(display, mask)
    finally:
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay(display)


def saved_position(default_x: int, default_y: int) -> tuple[int, int]:
    try:
        data = json.loads(POSITION.read_text())
        return int(data['x']), int(data['y'])
    except (OSError, ValueError, KeyError, TypeError):
        return default_x, default_y


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--camera', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--image')
    parser.add_argument('--x', type=int, default=20)
    parser.add_argument('--y', type=int, default=20)
    args = parser.parse_args()

    root = tk.Tk(className='WebcorderCamera')
    root.overrideredirect(True)
    root.attributes('-topmost', True)
    root.configure(bg='#111111')
    x, y = saved_position(args.x, args.y)
    x = min(max(0, x), max(0, root.winfo_screenwidth() - SIZE))
    y = min(max(0, y), max(0, root.winfo_screenheight() - SIZE))
    root.geometry(f'{SIZE}x{SIZE}+{x}+{y}')
    label = tk.Label(root, bg='#111111', borderwidth=0, highlightthickness=0)
    label.pack(fill='both', expand=True)
    root.update_idletasks()
    shape_circle(root.winfo_id())

    command = [sys.executable, str(ROOT / 'camera_worker.py'), '--camera', args.camera,
               '--name', args.name, '--fps', '10', '--background',
               'Person background' if args.image else 'None']
    if args.image:
        command += ['--image', args.image]
    worker = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              stdin=subprocess.DEVNULL)
    latest: list[bytes | None] = [None]
    lock = threading.Lock()
    closed = False

    def read_frames() -> None:
        while True:
            frame = worker.stdout.read(FRAME_BYTES)
            if len(frame) != FRAME_BYTES:
                break
            with lock:
                latest[0] = frame

    def forward_status() -> None:
        for line in worker.stderr:
            print(line.decode('utf-8', errors='replace').rstrip(), file=sys.stderr, flush=True)

    threading.Thread(target=read_frames, daemon=True).start()
    threading.Thread(target=forward_status, daemon=True).start()

    def repaint() -> None:
        with lock:
            frame = latest[0]
            latest[0] = None
        if frame is not None:
            image = Image.frombytes('RGBA', (SIZE, SIZE), frame, 'raw', 'BGRA')
            photo = ImageTk.PhotoImage(image)
            label.configure(image=photo)
            label.image = photo
        if not closed:
            root.after(35, repaint)

    drag: list[int] = []

    def press(event) -> None:
        drag[:] = [event.x_root, event.y_root, root.winfo_x(), root.winfo_y()]

    def move(event) -> None:
        if not drag:
            return
        left = min(max(0, drag[2] + event.x_root - drag[0]), max(0, root.winfo_screenwidth() - SIZE))
        top = min(max(0, drag[3] + event.y_root - drag[1]), max(0, root.winfo_screenheight() - SIZE))
        root.geometry(f'+{left}+{top}')

    def release(_event) -> None:
        if drag:
            drag.clear()
            POSITION.parent.mkdir(parents=True, exist_ok=True)
            POSITION.write_text(json.dumps({'x': root.winfo_x(), 'y': root.winfo_y()}))

    for widget in (root, label):
        widget.bind('<ButtonPress-1>', press)
        widget.bind('<B1-Motion>', move)
        widget.bind('<ButtonRelease-1>', release)

    def keep_visible() -> None:
        if not closed:
            root.lift()
            root.after(500, keep_visible)

    def close() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        if worker.poll() is None:
            worker.terminate()
        root.destroy()

    signal.signal(signal.SIGTERM, lambda *_: root.after(0, close))
    root.after(0, repaint)
    root.after(500, keep_visible)
    try:
        root.mainloop()
    finally:
        if worker.poll() is None:
            worker.terminate()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
