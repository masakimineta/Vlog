"""ffmpeg / ffprobe の呼び出しをまとめたモジュール。

ffmpeg は次の順で探す:
  1. 環境変数 AUTOEDIT_FFMPEG
  2. PATH 上の ffmpeg
  3. imageio-ffmpeg が同梱しているバイナリ

ffprobe が見つからない環境（imageio-ffmpeg は ffmpeg しか同梱しない）でも
動くように、ffmpeg の標準エラー出力から情報を読み取るフォールバックを用意している。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


class FFmpegError(RuntimeError):
    """ffmpeg / ffprobe の実行に失敗した。"""


@lru_cache(maxsize=1)
def ffmpeg_path() -> str:
    override = os.environ.get("AUTOEDIT_FFMPEG")
    if override:
        return override

    found = shutil.which("ffmpeg")
    if found:
        return found

    try:
        import imageio_ffmpeg
    except ImportError:
        raise FFmpegError(
            "ffmpeg が見つかりません。\n"
            "  macOS:   brew install ffmpeg\n"
            "  Windows: winget install Gyan.FFmpeg\n"
            "  もしくは: pip install imageio-ffmpeg"
        ) from None
    return imageio_ffmpeg.get_ffmpeg_exe()


@lru_cache(maxsize=1)
def ffprobe_path() -> str | None:
    override = os.environ.get("AUTOEDIT_FFPROBE")
    if override:
        return override
    return shutil.which("ffprobe")


def run(args: list[str], *, cwd: Path | None = None, quiet: bool = True) -> str:
    """ffmpeg を実行して標準エラー出力を返す。"""
    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", *args]
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-25:])
        raise FFmpegError(f"ffmpeg の実行に失敗しました:\n{' '.join(cmd)}\n\n{tail}")
    if not quiet and proc.stderr:
        print(proc.stderr)
    return proc.stderr


@dataclass
class MediaInfo:
    duration: float
    width: int | None
    height: int | None
    fps: float | None
    has_video: bool
    has_audio: bool

    @property
    def resolution(self) -> str:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return "-"


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_RE = re.compile(r"Stream #\d+:\d+.*: Video: .*?(\d{2,5})x(\d{2,5})")
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s+fps")
_AUDIO_RE = re.compile(r"Stream #\d+:\d+.*: Audio: ")


def probe(path: Path) -> MediaInfo:
    """動画・音声ファイルの基本情報を取得する。"""
    probe_bin = ffprobe_path()
    if probe_bin:
        return _probe_with_ffprobe(probe_bin, path)
    return _probe_with_ffmpeg(path)


def _probe_with_ffprobe(probe_bin: str, path: Path) -> MediaInfo:
    proc = subprocess.run(
        [
            probe_bin, "-v", "error",
            "-show_entries", "format=duration",
            "-show_entries", "stream=codec_type,width,height,avg_frame_rate",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe に失敗しました: {path}\n{proc.stderr.strip()}")

    data = json.loads(proc.stdout or "{}")
    duration = float(data.get("format", {}).get("duration") or 0.0)
    width = height = fps = None
    has_video = has_audio = False

    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and not has_video:
            has_video = True
            width = stream.get("width")
            height = stream.get("height")
            fps = _parse_fraction(stream.get("avg_frame_rate"))
        elif stream.get("codec_type") == "audio":
            has_audio = True

    return MediaInfo(duration, width, height, fps, has_video, has_audio)


def _probe_with_ffmpeg(path: Path) -> MediaInfo:
    """ffprobe がない環境向けに ffmpeg の出力から情報を拾う。"""
    proc = subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-i", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    text = proc.stderr
    if "Invalid data found" in text or "No such file" in text:
        raise FFmpegError(f"ファイルを読み取れません: {path}")

    duration = 0.0
    if m := _DURATION_RE.search(text):
        duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])

    width = height = fps = None
    has_video = False
    if m := _VIDEO_RE.search(text):
        has_video = True
        width, height = int(m[1]), int(m[2])
        line_end = text.find("\n", m.end())
        line = text[m.end(): line_end if line_end != -1 else None]
        if fm := _FPS_RE.search(line):
            fps = float(fm[1])

    has_audio = bool(_AUDIO_RE.search(text))
    return MediaInfo(duration, width, height, fps, has_video, has_audio)


def _parse_fraction(value: str | None) -> float | None:
    if not value or "/" not in value:
        return None
    num, den = value.split("/", 1)
    try:
        num_f, den_f = float(num), float(den)
    except ValueError:
        return None
    if den_f == 0:
        return None
    return round(num_f / den_f, 3)


def detect_silence(path: Path, *, noise_db: float = -32.0, min_duration: float = 0.5
                   ) -> list[tuple[float, float]]:
    """無音区間を [(開始, 終了), ...] で返す。"""
    stderr = run([
        "-i", str(path),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null", "-",
    ])

    silences: list[tuple[float, float]] = []
    start: float | None = None
    for line in stderr.splitlines():
        if "silence_start:" in line:
            start = float(line.split("silence_start:")[1].split()[0])
        elif "silence_end:" in line and start is not None:
            end = float(line.split("silence_end:")[1].split()[0])
            silences.append((start, end))
            start = None
    return silences
