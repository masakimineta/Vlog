"""フレームを ffmpeg に流し込んで動画にする。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def ffmpeg_path() -> str:
    """ffmpeg を探す。PATH に無ければ imageio-ffmpeg の同梱版を使う。"""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise RuntimeError(
            "ffmpeg が見つかりません。ffmpeg を入れるか、"
            "pip install imageio-ffmpeg を実行してください。"
        ) from exc


def codec_args(out: Path, transparent: bool) -> list[str]:
    """拡張子と透過の有無から、書き出し設定を決める。"""
    ext = out.suffix.lower()

    if ext == ".mov":
        # ProRes 4444（透過つき）。Premiere / Final Cut / Resolve が素直に読む。
        if transparent:
            return ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le"]
        return ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"]

    if ext == ".webm":
        # VP9。透過つきでもファイルが軽い。
        pix = "yuva420p" if transparent else "yuv420p"
        return ["-c:v", "libvpx-vp9", "-pix_fmt", pix, "-b:v", "0", "-crf", "24"]

    if transparent:
        raise ValueError(
            "MP4 は透過を保存できません。透過つきで書き出すなら "
            "出力ファイル名を .mov か .webm にしてください。"
        )
    return ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-preset", "slow"]


def encode(frames, out: Path, width: int, height: int, fps: int, transparent: bool) -> None:
    """フレーム（PIL の Image を順に返すもの）を動画にする。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    mode = "rgba" if transparent else "rgb24"

    cmd = [
        ffmpeg_path(),
        "-y",
        "-loglevel", "error",
        "-f", "rawvideo",
        "-pix_fmt", mode,
        "-s", f"{width}x{height}",
        "-r", str(fps),
        "-i", "-",
        *codec_args(out, transparent),
        "-r", str(fps),
        str(out),
    ]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for img in frames:
            if not transparent:
                img = img.convert("RGB")
            proc.stdin.write(img.tobytes())
    finally:
        proc.stdin.close()
        code = proc.wait()
    if code != 0:
        raise RuntimeError(f"ffmpeg が失敗しました（終了コード {code}）")


def write_png_sequence(frames, out_dir: Path) -> int:
    """連番 PNG で書き出す。編集ソフトに連番で読ませたいときに使う。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for i, img in enumerate(frames):
        img.save(out_dir / f"routemap_{i:05d}.png")
        count = i + 1
    return count
