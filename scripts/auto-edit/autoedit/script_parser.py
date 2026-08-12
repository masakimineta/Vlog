"""台本 Markdown の解釈。

templates/台本.md の記法をそのまま読み取る。

    ## 導入（0:00-0:15）

    [映像] B001 カーテンを開ける / B002 コーヒーの手元

    > 去年の今ごろ、僕は**昼まで何も手につかない**在宅ワーカーでした。

    [テロップ] 朝を変えた5つの習慣

- `>` で始まる行         … 喋る内容（台詞どおり）。文字起こしとの照合に使う
- `-` で始まる行         … 話す要点のメモ。ゆるく照合する
- `[映像] ...`           … そのパートで使うカット。A001 / B012 の番号を拾う
- `[テロップ] ...`       … 画面に出す文字
- `[BGM]` `[SE]`         … 音の指示（記録するだけ）

`[映像]` だけで喋りがないセクションは、風景パートとして扱われる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .media import CUT_ID_RE

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_DIRECTIVE_RE = re.compile(r"^\[(映像|テロップ|BGM|SE|音|字幕)\]\s*(.*)$")
_QUOTE_RE = re.compile(r"^>\s?(.*)$")
_BULLET_RE = re.compile(r"^[-*+]\s+(.*)$")
_SECONDS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:秒|s|sec)")
_TIMECODE_RE = re.compile(r"\d+:\d{2}")


@dataclass
class Visual:
    """[映像] の指示 1 つ分。"""
    cut_ids: list[str]
    description: str
    seconds: float | None = None


@dataclass
class Narration:
    """喋る内容のかたまり。"""
    text: str
    #: True なら台詞どおり（`>` 行）、False なら要点メモ（`-` 行）
    verbatim: bool = True


@dataclass
class Section:
    title: str
    visuals: list[Visual] = field(default_factory=list)
    narrations: list[Narration] = field(default_factory=list)
    captions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def has_narration(self) -> bool:
        return bool(self.narrations)

    @property
    def narration_text(self) -> str:
        return " ".join(n.text for n in self.narrations)


def parse_script(path: Path) -> list[Section]:
    """台本 Markdown をセクションの一覧に変換する。"""
    text = Path(path).read_text(encoding="utf-8")
    sections: list[Section] = []
    current: Section | None = None
    pending_quote: list[str] = []
    pending_bullets: list[str] = []
    in_code_block = False
    skipping = False  # 「表記ルール」など照合に使わない見出しの中にいる

    def flush() -> None:
        nonlocal pending_quote, pending_bullets
        if current is None:
            pending_quote, pending_bullets = [], []
            return
        if pending_quote:
            joined = " ".join(pending_quote).strip()
            if joined:
                current.narrations.append(Narration(clean_text(joined), verbatim=True))
        if pending_bullets:
            joined = " ".join(pending_bullets).strip()
            if joined:
                current.narrations.append(Narration(clean_text(joined), verbatim=False))
        pending_quote, pending_bullets = [], []

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if line.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        if heading := _HEADING_RE.match(line):
            flush()
            title = clean_text(heading[2])
            # 1 番上の見出し（# 台本: ...）と補助セクションは本編に含めない
            if len(heading[1]) == 1 or _is_skippable(title):
                skipping = True
                current = None
                continue
            skipping = False
            current = Section(title=title)
            sections.append(current)
            continue

        if skipping or not line or line.startswith("---"):
            if not line:
                flush()
            continue

        if directive := _DIRECTIVE_RE.match(line):
            flush()
            if current is None:
                continue
            kind, body = directive[1], directive[2].strip()
            if kind == "映像":
                current.visuals.extend(_parse_visuals(body))
            elif kind in ("テロップ", "字幕"):
                if body:
                    current.captions.append(clean_text(body))
            else:
                current.notes.append(f"[{kind}] {body}")
            continue

        if quote := _QUOTE_RE.match(line):
            if pending_bullets:
                flush()
            body = quote[1].strip()
            if body:
                pending_quote.append(body)
            continue

        if bullet := _BULLET_RE.match(line):
            if pending_quote:
                flush()
            body = bullet[1].strip()
            if body:
                pending_bullets.append(body)
            continue

        # それ以外の地の文は要点メモ扱い
        if current is not None and not line.startswith("|"):
            pending_bullets.append(line)

    flush()
    return [s for s in sections if s.visuals or s.narrations or s.captions]


def _is_skippable(title: str) -> bool:
    skip_words = ("表記ルール", "使わなかった", "メモ", "備考", "参考")
    return any(word in title for word in skip_words)


def _parse_visuals(body: str) -> list[Visual]:
    """「B001 カーテン / B002 手元」を Visual 2 つに分解する。"""
    visuals = []
    for part in re.split(r"\s*/\s*|\s*、\s*", body):
        part = part.strip()
        if not part:
            continue
        cut_ids = [f"{m[1].upper()}{m[2]}" for m in CUT_ID_RE.finditer(part)]
        seconds = float(m[1]) if (m := _SECONDS_RE.search(part)) else None
        visuals.append(Visual(cut_ids=cut_ids, description=clean_text(part),
                              seconds=seconds))
    return visuals


def clean_text(text: str) -> str:
    """Markdown の装飾を落として素の文字列にする。"""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
    text = re.sub(r"[*_]{1,2}(.+?)[*_]{1,2}", r"\1", text)
    # 見出しの「導入（0:00-0:15）」から時間指定を落とす
    text = re.sub(r"[（(]\s*" + _TIMECODE_RE.pattern + r"[^）)]*[）)]", "", text)
    return text.strip()
