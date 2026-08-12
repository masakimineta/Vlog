"""編集プランを実際の動画ファイルに書き出す。

  1. プランの各コマを、同じ解像度・フレームレート・音声形式に揃えて切り出す
  2. それらを連結する
  3. BGM を重ね（喋りの下では自動で音量を下げる）、ラウドネスを揃え、字幕を焼き込む
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import Config, default_subtitle_font
from .ffmpeg import run
from .transcribe import write_srt


def render(plan: dict, output: Path, config: Config, *, workdir: Path | None = None,
           keep_workdir: bool = False, progress=print) -> Path:
    items = plan["items"]
    if not items:
        raise ValueError("プランにコマがありません")

    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    temporary = workdir is None
    workdir = Path(workdir) if workdir else output.parent / f".{output.stem}-work"
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        progress(f"[1/3] {len(items)} コマを切り出しています…")
        segments = [
            _cut_item(item, workdir / f"seg_{index:04d}.mp4", config, index, len(items),
                      progress)
            for index, item in enumerate(items)
        ]

        progress("[2/3] つなげています…")
        body = _concat(segments, workdir, config)

        progress("[3/3] 音声を整えて書き出しています…")
        _finalize(body, plan, output, config, workdir, progress)
    finally:
        if temporary and not keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)

    return output


# --------------------------------------------------------------------------

def _cut_item(item: dict, destination: Path, config: Config, index: int, total: int,
              progress) -> Path:
    if index % 10 == 0 or index == total - 1:
        progress(f"      {index + 1}/{total}")

    video_path = item["video_path"]
    audio_path = item.get("audio_path")
    duration = float(item["duration"])
    video_start = float(item["video_start"])
    audio_start = float(item.get("audio_start", 0.0))
    gain_db = float(item.get("audio_gain_db", 0.0))

    args: list[str] = ["-y"]
    same_source = (
        audio_path == video_path and abs(audio_start - video_start) < 1e-3
    )

    args += ["-ss", f"{video_start:.3f}", "-i", video_path]
    if audio_path and not same_source:
        args += ["-ss", f"{audio_start:.3f}", "-i", audio_path]
        audio_label = "1:a"
    elif audio_path:
        audio_label = "0:a"
    else:
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        audio_label = "1:a"

    video_filter = (
        f"scale={config.width}:{config.height}:force_original_aspect_ratio=decrease,"
        f"pad={config.width}:{config.height}:(ow-iw)/2:(oh-ih)/2,"
        f"setsar=1,fps={config.fps},format=yuv420p"
    )
    audio_filter = "aresample=48000,aformat=channel_layouts=stereo"
    if abs(gain_db) > 0.01:
        audio_filter += f",volume={gain_db}dB"

    args += [
        "-t", f"{duration:.3f}",
        "-map", "0:v:0", "-map", audio_label,
        "-vf", video_filter,
        "-af", audio_filter,
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2",
        "-video_track_timescale", "90000",
        str(destination),
    ]
    run(args)
    return destination


def _concat(segments: list[Path], workdir: Path, config: Config) -> Path:
    list_file = workdir / "segments.txt"
    list_file.write_text(
        "\n".join(f"file '{p.name}'" for p in segments) + "\n", encoding="utf-8"
    )
    body = workdir / "body.mp4"
    run([
        "-y", "-f", "concat", "-safe", "0", "-i", list_file.name,
        "-c", "copy", body.name,
    ], cwd=workdir)
    return body


def _finalize(body: Path, plan: dict, output: Path, config: Config, workdir: Path,
              progress) -> None:
    duration = float(plan.get("duration") or 0.0)
    bgm = plan.get("bgm")
    subtitles = plan.get("subtitles") or []

    args: list[str] = ["-y", "-i", body.name]
    filters: list[str] = []
    audio_out = "0:a"

    if bgm:
        bgm_path = Path(bgm)
        if not bgm_path.exists():
            raise FileNotFoundError(f"BGM が見つかりません: {bgm_path}")
        args += ["-stream_loop", "-1", "-i", str(bgm_path.resolve())]

        fade_out_start = max(duration - config.bgm_fade_out, 0.0)
        # bgm_duck_db を sidechaincompress の圧縮比に対応させる
        ratio = max(1.5, min(20.0, abs(config.bgm_duck_db)))
        filters += [
            f"[1:a]aresample=48000,aformat=channel_layouts=stereo,"
            f"atrim=0:{duration:.3f},asetpts=N/SR/TB,"
            f"volume={config.bgm_gain_db}dB,"
            f"afade=t=in:st=0:d={config.bgm_fade_in},"
            f"afade=t=out:st={fade_out_start:.3f}:d={config.bgm_fade_out}[bgm]",
            "[0:a]asplit=2[voice][sidechain]",
            f"[bgm][sidechain]sidechaincompress=threshold=0.02:ratio={ratio:.1f}:"
            f"attack=20:release=600[ducked]",
            "[voice][ducked]amix=inputs=2:duration=first:normalize=0[mixed]",
            f"[mixed]loudnorm=I={config.target_lufs}:TP=-1.5:LRA=11[aout]",
        ]
        audio_out = "[aout]"
    else:
        filters.append(f"[0:a]loudnorm=I={config.target_lufs}:TP=-1.5:LRA=11[aout]")
        audio_out = "[aout]"

    video_out = "0:v"
    if subtitles and config.burn_subtitles:
        srt = workdir / "subtitles.srt"
        write_srt(subtitles, srt)
        font = config.subtitle_font or default_subtitle_font()
        style = (
            f"FontName={font},FontSize={config.subtitle_font_size},"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,"
            "Outline=2,Shadow=0,MarginV=40,Alignment=2"
        )
        # cwd を workdir にしてファイル名だけ渡す（Windows のパス問題を避ける）
        filters.append(f"[0:v]subtitles={srt.name}:force_style='{style}'[vout]")
        video_out = "[vout]"
        progress(f"      字幕 {len(subtitles)} 件を焼き込みます（フォント: {font}）")

    args += [
        "-filter_complex", ";".join(filters),
        "-map", video_out, "-map", audio_out,
        "-c:v", "libx264", "-crf", str(config.crf), "-preset", config.preset,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", config.audio_bitrate, "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(output),
    ]
    run(args, cwd=workdir)
