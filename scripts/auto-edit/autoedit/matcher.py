"""台本の文章と、文字起こしの照合。

台本の 1 パートに対して「どのクリップの何秒から何秒までが対応するか」を返す。
同じ内容を撮り直している場合は、一致率が高いほうが選ばれる。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .media import Clip

try:  # 高速な実装があれば使う
    from rapidfuzz import fuzz as _fuzz
except ImportError:  # pragma: no cover - 環境依存
    _fuzz = None

import difflib

_STRIP_RE = re.compile(r"[\s、。，．,.!?！？「」『』（）()\[\]…‥・~〜ー\-—:;：；\"'`]+")


@dataclass
class Match:
    clip: Clip
    start: float
    end: float
    score: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def normalize(text: str) -> str:
    """比較用に文字列をならす（全角半角・記号・空白を吸収）。"""
    text = unicodedata.normalize("NFKC", text)
    text = _STRIP_RE.sub("", text)
    return text.lower()


def _char_timeline(clip: Clip) -> tuple[str, list[tuple[float, float]]]:
    """正規化した文字列と、各文字に対応する (開始秒, 終了秒) を返す。"""
    chars: list[str] = []
    times: list[tuple[float, float]] = []

    source = clip.words or [
        {"start": s["start"], "end": s["end"], "text": s["text"]} for s in clip.segments
    ]
    for entry in source:
        normalized = normalize(entry["text"])
        if not normalized:
            continue
        start, end = float(entry["start"]), float(entry["end"])
        span = max(end - start, 1e-3) / len(normalized)
        for index, char in enumerate(normalized):
            chars.append(char)
            times.append((start + span * index, start + span * (index + 1)))
    return "".join(chars), times


def _score(target: str, candidate: str) -> float:
    if _fuzz is not None:
        return _fuzz.ratio(target, candidate) / 100.0
    return difflib.SequenceMatcher(None, target, candidate).ratio()


def _best_span(target: str, haystack: str) -> tuple[int, int, float]:
    """haystack の中で target に最も近い部分文字列の範囲と一致率を返す。"""
    if not target or not haystack:
        return 0, 0, 0.0

    if _fuzz is not None:
        alignment = _fuzz.partial_ratio_alignment(target, haystack)
        if alignment is None:
            return 0, 0, 0.0
        return alignment.dest_start, alignment.dest_end, alignment.score / 100.0

    # rapidfuzz がない場合は粗いスライディングウィンドウで探す
    best = (0, 0, 0.0)
    length = len(target)
    step = max(1, length // 10)
    for scale in (0.85, 1.0, 1.25):
        window = max(4, int(length * scale))
        for start in range(0, max(1, len(haystack) - window + 1), step):
            candidate = haystack[start:start + window]
            score = _score(target, candidate)
            if score > best[2]:
                best = (start, start + window, score)
    return best


def find_best_match(text: str, clips: list[Clip], *, threshold: float = 0.55,
                    used: set[tuple[str, float]] | None = None) -> Match | None:
    """台本の一文に最も近い区間を、喋りクリップ全体から探す。"""
    target = normalize(text)
    if len(target) < 4:
        return None

    best: Match | None = None
    for clip in clips:
        haystack, times = _char_timeline(clip)
        if not haystack:
            continue
        start_index, end_index, score = _best_span(target, haystack)
        if end_index <= start_index or score < threshold:
            continue
        if best is not None and score <= best.score:
            continue
        start = times[start_index][0]
        end = times[min(end_index, len(times)) - 1][1]
        best = Match(clip=clip, start=start, end=end, score=score)

    return best


def split_on_gaps(clip: Clip, start: float, end: float, max_gap: float
                  ) -> list[tuple[float, float]]:
    """区間内の長い無音を取り除き、[(開始, 終了), ...] に分割する。"""
    words = [w for w in clip.words if w["end"] > start and w["start"] < end]
    if not words:
        return [(start, end)]

    spans: list[list[float]] = []
    for word in words:
        word_start = max(float(word["start"]), start)
        word_end = min(float(word["end"]), end)
        if word_end <= word_start:
            continue
        if spans and word_start - spans[-1][1] <= max_gap:
            spans[-1][1] = word_end
        else:
            spans.append([word_start, word_end])

    return [(s, e) for s, e in spans if e - s > 0.05] or [(start, end)]


def trim_fillers(clip: Clip, start: float, end: float, fillers: list[str]
                 ) -> tuple[float, float]:
    """区間の先頭・末尾にあるフィラー語（「えー」など）を落とす。"""
    if not clip.words:
        return start, end

    normalized_fillers = {normalize(f) for f in fillers if normalize(f)}
    words = [w for w in clip.words if w["end"] > start + 1e-3 and w["start"] < end - 1e-3]

    while words and normalize(words[0]["text"]) in normalized_fillers:
        start = float(words[0]["end"])
        words = words[1:]
    while words and normalize(words[-1]["text"]) in normalized_fillers:
        end = float(words[-1]["start"])
        words = words[:-1]

    return (start, end) if end > start else (start, start)
