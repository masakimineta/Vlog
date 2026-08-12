"""Whisper による文字起こしと、喋りあり／風景クリップの判定。"""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .media import Clip


class TranscriptionUnavailable(RuntimeError):
    """faster-whisper が入っていない。"""


_MODEL_CACHE: dict[tuple[str, str], object] = {}


def _load_model(model_name: str, compute_type: str = "int8"):
    key = (model_name, compute_type)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise TranscriptionUnavailable(
            "faster-whisper が入っていません。\n"
            "  pip install faster-whisper\n"
            "文字起こしなしで進めたい場合は scan に --no-transcribe を付けてください"
            "（すべて風景クリップとして扱われます）。"
        ) from None

    model = WhisperModel(model_name, device="auto", compute_type=compute_type)
    _MODEL_CACHE[key] = model
    return model


def transcribe_clip(clip: Clip, config: Config, *, progress=print) -> None:
    """クリップを文字起こしして segments / words / kind を埋める。"""
    if not clip.info.has_audio:
        clip.kind = "broll"
        return

    model = _load_model(config.whisper_model)
    progress(f"  文字起こし中: {clip.name}")

    segments, _info = model.transcribe(
        str(clip.path),
        language=config.language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )

    clip.segments = []
    clip.words = []
    for segment in segments:
        text = (segment.text or "").strip()
        if not text:
            continue
        clip.segments.append({
            "start": round(segment.start, 3),
            "end": round(segment.end, 3),
            "text": text,
        })
        for word in segment.words or []:
            clip.words.append({
                "start": round(word.start, 3),
                "end": round(word.end, 3),
                "text": (word.word or "").strip(),
            })

    clip.kind = classify(clip, config)


def classify(clip: Clip, config: Config) -> str:
    """喋りあり（speech）か風景（broll）かを判定する。"""
    speech = clip.speech_seconds
    if speech < config.speech_min_seconds:
        return "broll"
    if clip.info.duration > 0 and speech / clip.info.duration < config.speech_min_ratio:
        return "broll"
    return "speech"


def transcribe_all(clips: list[Clip], config: Config, *, enabled: bool = True,
                   progress=print) -> None:
    """全クリップを文字起こしする。enabled=False なら全部風景クリップ扱い。"""
    if not enabled:
        for clip in clips:
            clip.kind = "broll"
        progress("文字起こしをスキップしました（全クリップを風景扱いにします）")
        return

    for index, clip in enumerate(clips, start=1):
        progress(f"[{index}/{len(clips)}] {clip.name}")
        transcribe_clip(clip, config, progress=progress)
        label = "喋りあり" if clip.kind == "speech" else "風景"
        progress(f"  → {label}（喋り {clip.speech_seconds:.1f} 秒 / "
                 f"全体 {clip.info.duration:.1f} 秒）")


def write_srt(items: list[dict], path: Path) -> None:
    """タイムライン上の字幕を SRT として書き出す。"""
    lines = []
    for index, item in enumerate(items, start=1):
        lines.append(str(index))
        lines.append(f"{_timestamp(item['start'])} --> {_timestamp(item['end'])}")
        lines.append(item["text"])
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:  # 繰り上がり
        millis = 0
        secs += 1
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
