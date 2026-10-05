"""Turns the recorded take (out/kioku-demo.webm) into the mp4 for upload (out/kioku-demo.mp4).

    python demo/video/make_mp4.py

The take starts a moment before the opening card is up and ends with the browser closing, so the mp4 is cut
to start on the opening card and end on the closing card, with a short fade at each end. It needs ffmpeg with
libx264: the copy that comes with the imageio-ffmpeg package if it is installed, otherwise ffmpeg on the PATH.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / 'out'
SRC, DST = OUT / 'kioku-demo.webm', OUT / 'kioku-demo.mp4'
FPS = 25
CARD = (18, 55, 46)  # #12372e, the background of the title cards


def ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return shutil.which('ffmpeg') or sys.exit('ffmpeg not found')


def card_frames(ff: str) -> list[bool]:
    """For each frame, whether a title card fills the screen (a small patch left of centre: a light panel in the app)."""
    raw = subprocess.run([ff, '-v', 'error', '-i', str(SRC), '-vf', f'fps={FPS},crop=8:8:96:446,scale=1:1',
                          '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True, check=True).stdout
    return [max(abs(a - b) for a, b in zip(raw[i:i + 3], CARD)) <= 8 for i in range(0, len(raw) - 2, 3)]


def main() -> None:
    ff = ffmpeg()
    cards = card_frames(ff)
    if True not in cards:
        sys.exit('no title card found in the take')
    first = cards.index(True)
    last = len(cards) - 1 - cards[::-1].index(True)
    start, length = first / FPS, (last + 1 - first) / FPS
    subprocess.run([ff, '-v', 'error', '-y', '-ss', f'{start:.3f}', '-i', str(SRC), '-t', f'{length:.3f}',
                    '-vf', f'fps={FPS},fade=t=in:st=0:d=0.4,fade=t=out:st={length - 0.8:.3f}:d=0.8',
                    '-c:v', 'libx264', '-preset', 'slow', '-crf', '18', '-pix_fmt', 'yuv420p',
                    '-movflags', '+faststart', '-an', str(DST)], check=True)
    print(f'{DST}  {length:.1f} s (cut {start:.2f} s from the start, {(len(cards) - last - 1) / FPS:.2f} s from the end)')


if __name__ == '__main__':
    main()
