"""素材フォルダのスキャンとファイル名の解釈。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .ffmpeg import MediaInfo, probe

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".m2ts", ".webm"}
AUDIO_SUFFIXES = {".wav", ".aif", ".aiff", ".mp3", ".m4a", ".flac"}

#: docs/命名規則.md の「A001」「B012」形式のカット番号
CUT_ID_RE = re.compile(r"(?<![A-Za-z0-9])([AB])(\d{2,4})(?![0-9])")


@dataclass
class Clip:
    path: Path
    cut_id: str | None
    info: MediaInfo
    #: "speech"（喋りあり） / "broll"（風景・Bロール） / "unknown"（未解析）
    kind: str = "unknown"
    segments: list[dict] = field(default_factory=list)
    words: list[dict] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def label(self) -> str:
        return self.cut_id or self.path.stem

    @property
    def speech_seconds(self) -> float:
        return sum(s["end"] - s["start"] for s in self.segments)

    @property
    def transcript(self) -> str:
        return " ".join(s["text"] for s in self.segments).strip()

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "name": self.name,
            "cut_id": self.cut_id,
            "kind": self.kind,
            "duration": round(self.info.duration, 3),
            "width": self.info.width,
            "height": self.info.height,
            "fps": self.info.fps,
            "has_video": self.info.has_video,
            "has_audio": self.info.has_audio,
            "speech_seconds": round(self.speech_seconds, 2),
            "segments": self.segments,
            "words": self.words,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Clip":
        info = MediaInfo(
            duration=data["duration"],
            width=data.get("width"),
            height=data.get("height"),
            fps=data.get("fps"),
            has_video=data.get("has_video", True),
            has_audio=data.get("has_audio", True),
        )
        return cls(
            path=Path(data["path"]),
            cut_id=data.get("cut_id"),
            info=info,
            kind=data.get("kind", "unknown"),
            segments=data.get("segments", []),
            words=data.get("words", []),
        )


def parse_cut_id(filename: str) -> str | None:
    """ファイル名からカット番号を取り出す。

    >>> parse_cut_id("20260412-EP001-A001-清水寺.mp4")
    'A001'
    >>> parse_cut_id("IMG_0421.mov") is None
    True
    """
    stem = Path(filename).stem
    # 「EP001」の 001 を拾わないよう、EP+数字は先に取り除く
    stem = re.sub(r"EP\d+", "", stem, flags=re.IGNORECASE)
    match = CUT_ID_RE.search(stem)
    if not match:
        return None
    return f"{match[1].upper()}{match[2]}"


def scan_clips(source: Path, *, include_audio: bool = False) -> list[Clip]:
    """素材フォルダを再帰的に走査して Clip の一覧を返す。"""
    if not source.exists():
        raise FileNotFoundError(f"素材フォルダが見つかりません: {source}")

    suffixes = set(VIDEO_SUFFIXES)
    if include_audio:
        suffixes |= AUDIO_SUFFIXES

    paths = sorted(
        p for p in source.rglob("*")
        if p.is_file() and p.suffix.lower() in suffixes and not p.name.startswith(".")
    )
    if not paths:
        raise FileNotFoundError(
            f"{source} に動画ファイルが見つかりません（対応拡張子: "
            f"{', '.join(sorted(suffixes))}）"
        )

    clips = []
    for path in paths:
        clips.append(Clip(path=path, cut_id=parse_cut_id(path.name), info=probe(path)))
    return clips


def sort_key(clip: Clip) -> tuple:
    """カット番号があればその順、なければファイル名順に並べるためのキー。"""
    if clip.cut_id:
        return (0, clip.cut_id[0], int(clip.cut_id[1:]), clip.name)
    return (1, "", 0, clip.name)
