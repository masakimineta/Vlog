"""台本と素材から編集プランを組み立てる。

出来上がるプランは JSON で保存され、そのまま render に渡せる。
気に入らない箇所は JSON を手で直してから書き出してもよい。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import Config
from .matcher import Match, find_best_match, split_on_gaps, trim_fillers
from .media import Clip, sort_key
from .script_parser import Section, Visual


@dataclass
class Item:
    """タイムライン上の 1 コマ。映像と音声の取り出し元を持つ。"""
    type: str                    # "speech" | "broll"
    section: str
    video_path: str
    video_start: float
    duration: float
    audio_path: str | None = None
    audio_start: float = 0.0
    audio_gain_db: float = 0.0
    note: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ("video_start", "duration", "audio_start"):
            data[key] = round(data[key], 3)
        return data


@dataclass
class Plan:
    items: list[Item] = field(default_factory=list)
    subtitles: list[dict] = field(default_factory=list)
    chapters: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    bgm: str | None = None

    @property
    def duration(self) -> float:
        return sum(item.duration for item in self.items)

    def to_dict(self, config: Config) -> dict:
        return {
            "duration": round(self.duration, 3),
            "bgm": self.bgm,
            "config": config.to_dict(),
            "chapters": self.chapters,
            "items": [item.to_dict() for item in self.items],
            "subtitles": self.subtitles,
            "warnings": self.warnings,
        }


def build_plan(clips: list[Clip], sections: list[Section] | None, config: Config,
               *, bgm: Path | None = None) -> Plan:
    speech_clips = [c for c in clips if c.kind == "speech"]
    broll_clips = [c for c in clips if c.kind != "speech"]
    plan = Plan(bgm=str(bgm) if bgm else None)

    if not clips:
        raise ValueError("素材が 1 つもありません")

    if sections:
        _build_from_script(plan, sections, speech_clips, broll_clips, config)
    else:
        _build_without_script(plan, speech_clips, broll_clips, config)

    if not plan.items:
        detail = "\n".join(f"  - {w}" for w in plan.warnings)
        raise ValueError(
            "使えるカットが 1 つも見つかりませんでした。\n"
            "台本と素材が対応しているか、--match-threshold を下げるか確認してください。"
            + (f"\n\n内訳:\n{detail}" if detail else "")
        )
    return plan


# --------------------------------------------------------------------------
# 台本あり
# --------------------------------------------------------------------------

def _build_from_script(plan: Plan, sections: list[Section], speech_clips: list[Clip],
                       broll_clips: list[Clip], config: Config) -> None:
    broll_by_id = {c.cut_id: c for c in broll_clips if c.cut_id}
    # Bロール指定が A001 のように喋りクリップを指している場合も引けるようにする
    speech_by_id = {c.cut_id: c for c in speech_clips if c.cut_id}
    unused_brolls = [c for c in sorted(broll_clips, key=sort_key)]
    cursor = 0.0

    for section in sections:
        section_start = cursor
        spine = _match_section(section, speech_clips, config, plan)

        if spine:
            cursor = _emit_narrated_section(
                plan, section, spine, cursor, config,
                broll_by_id, speech_by_id, unused_brolls,
            )
        elif section.visuals:
            cursor = _emit_visual_section(
                plan, section, cursor, config, broll_by_id, speech_by_id, unused_brolls,
            )
        else:
            continue

        if cursor > section_start:
            plan.chapters.append({
                "start": round(section_start, 3),
                "title": section.title,
            })


def _match_section(section: Section, speech_clips: list[Clip], config: Config,
                   plan: Plan) -> list[dict]:
    """セクションの喋りを素材から探し、使う区間の一覧を返す。"""
    spine: list[dict] = []
    if not speech_clips:
        return spine

    for narration in section.narrations:
        threshold = config.match_threshold
        if not narration.verbatim:
            # 要点メモは台詞どおりではないので基準をゆるめる
            threshold *= 0.8

        match = find_best_match(narration.text, speech_clips, threshold=threshold)
        if match is None:
            plan.warnings.append(
                f"[{section.title}] 対応する音声が見つかりませんでした: "
                f"{narration.text[:40]}…"
            )
            continue
        spine.extend(_expand_match(match, config))

    return _merge_overlaps(spine)


def _expand_match(match: Match, config: Config) -> list[dict]:
    """一致した区間から、無音を詰めた使用区間を作る。"""
    clip = match.clip
    start, end = trim_fillers(clip, match.start, match.end, config.fillers)
    if end <= start:
        return []

    pieces = split_on_gaps(clip, start, end, config.max_gap_seconds)
    result = []
    for index, (piece_start, piece_end) in enumerate(pieces):
        if index == 0:
            piece_start = max(0.0, piece_start - config.pad_before)
        if index == len(pieces) - 1:
            piece_end = min(clip.info.duration, piece_end + config.pad_after)
        if piece_end - piece_start < 0.08:
            continue
        result.append({
            "clip": clip,
            "start": piece_start,
            "end": piece_end,
            "score": match.score,
        })
    return result


def _merge_overlaps(spine: list[dict]) -> list[dict]:
    """同じクリップの重なった区間をつなげる。"""
    merged: list[dict] = []
    for piece in spine:
        if merged:
            last = merged[-1]
            if last["clip"] is piece["clip"] and piece["start"] <= last["end"] + 0.05:
                last["end"] = max(last["end"], piece["end"])
                continue
        merged.append(dict(piece))
    return merged


def _emit_narrated_section(plan: Plan, section: Section, spine: list[dict],
                           cursor: float, config: Config,
                           broll_by_id: dict, speech_by_id: dict,
                           unused_brolls: list[Clip]) -> float:
    total = sum(p["end"] - p["start"] for p in spine)

    # 字幕は文字起こしの区切りをそのまま使う（台本より実際の発話に近い）
    offset = cursor
    for piece in spine:
        plan.subtitles.extend(_subtitles_for(piece, offset))
        offset += piece["end"] - piece["start"]

    covers = _cover_windows(section.visuals, total, config,
                            broll_by_id, speech_by_id, unused_brolls)

    local = 0.0
    for piece in spine:
        clip: Clip = piece["clip"]
        piece_length = piece["end"] - piece["start"]
        position = 0.0
        while position < piece_length - 1e-4:
            remaining = piece_length - position
            cover = _cover_at(covers, local + position)
            if cover is None:
                length = min(remaining, _next_cover_start(covers, local + position)
                             - (local + position))
                length = max(min(length, remaining), 0.05)
                plan.items.append(Item(
                    type="speech",
                    section=section.title,
                    video_path=str(clip.path),
                    video_start=piece["start"] + position,
                    duration=length,
                    audio_path=str(clip.path),
                    audio_start=piece["start"] + position,
                    note=f"{clip.label} 話者",
                ))
            else:
                cover_clip, cover_end, cover_offset = cover
                length = max(min(remaining, cover_end - (local + position)), 0.05)
                inner = (local + position) - cover_offset
                plan.items.append(Item(
                    type="speech",
                    section=section.title,
                    video_path=str(cover_clip.path),
                    video_start=min(inner, max(cover_clip.info.duration - length, 0.0)),
                    duration=length,
                    audio_path=str(clip.path),
                    audio_start=piece["start"] + position,
                    note=f"{clip.label} の音声に {cover_clip.label} を被せ",
                ))
            position += length
        local += piece_length

    return cursor + total


def _cover_windows(visuals: list[Visual], total: float, config: Config,
                   broll_by_id: dict, speech_by_id: dict,
                   unused_brolls: list[Clip]) -> list[tuple[float, float, Clip]]:
    """Bロールを被せる区間を [(開始, 終了, クリップ), ...] で返す。"""
    resolved = []
    for visual in visuals:
        clip = _resolve_visual(visual, broll_by_id, speech_by_id, unused_brolls)
        if clip is not None:
            resolved.append((visual, clip))

    head = min(config.speaker_head_seconds, total)
    remaining = total - head
    if not resolved or remaining <= 0.5:
        return []

    slot = remaining / len(resolved)
    windows: list[tuple[float, float, Clip]] = []
    for index, (visual, clip) in enumerate(resolved):
        slot_start = head + slot * index
        wanted = visual.seconds or config.broll_seconds
        length = min(wanted, clip.info.duration, slot, total - slot_start)
        if length < 0.4:
            continue
        windows.append((slot_start, slot_start + length, clip))
    return windows


def _cover_at(covers: list[tuple[float, float, Clip]], position: float):
    for start, end, clip in covers:
        if start - 1e-6 <= position < end:
            return clip, end, start
    return None


def _next_cover_start(covers: list[tuple[float, float, Clip]], position: float) -> float:
    upcoming = [start for start, _end, _clip in covers if start > position + 1e-6]
    return min(upcoming) if upcoming else float("inf")


def _emit_visual_section(plan: Plan, section: Section, cursor: float, config: Config,
                         broll_by_id: dict, speech_by_id: dict,
                         unused_brolls: list[Clip]) -> float:
    """喋りのない、風景だけのセクション。"""
    for visual in section.visuals:
        clip = _resolve_visual(visual, broll_by_id, speech_by_id, unused_brolls)
        if clip is None:
            plan.warnings.append(
                f"[{section.title}] 映像が見つかりませんでした: {visual.description}"
            )
            continue
        length = min(visual.seconds or config.broll_seconds, clip.info.duration)
        plan.items.append(Item(
            type="broll",
            section=section.title,
            video_path=str(clip.path),
            video_start=0.0,
            duration=length,
            audio_path=str(clip.path) if clip.info.has_audio else None,
            audio_start=0.0,
            audio_gain_db=config.ambient_gain_db,
            note=_describe(clip, visual.description),
        ))
        cursor += length
    return cursor


def _describe(clip: Clip, description: str) -> str:
    """「B003 B003 空」のようにカット番号が重複しないようにする。"""
    label = clip.label
    if description.startswith(label):
        return description
    return f"{label} {description}".strip()


def _resolve_visual(visual: Visual, broll_by_id: dict, speech_by_id: dict,
                    unused_brolls: list[Clip]) -> Clip | None:
    """[映像] の指定から実際のクリップを決める。"""
    for cut_id in visual.cut_ids:
        if clip := broll_by_id.get(cut_id):
            if clip in unused_brolls:
                unused_brolls.remove(clip)
            return clip
        if clip := speech_by_id.get(cut_id):
            return clip
    # 指定のカットが無ければ、まだ使っていない風景クリップを順に充てる
    if unused_brolls:
        return unused_brolls.pop(0)
    return None


# --------------------------------------------------------------------------
# 台本なし
# --------------------------------------------------------------------------

def _build_without_script(plan: Plan, speech_clips: list[Clip],
                          broll_clips: list[Clip], config: Config) -> None:
    """台本がない場合は、喋りをファイル名順につなぎ、間に風景を挟む。"""
    plan.warnings.append("台本が指定されていないため、ファイル名順に並べました")

    speech_clips = sorted(speech_clips, key=sort_key)
    broll_clips = sorted(broll_clips, key=sort_key)
    cursor = 0.0

    if not speech_clips:
        for clip in broll_clips:
            length = min(clip.info.duration, config.broll_max_seconds)
            plan.items.append(Item(
                type="broll", section="本編",
                video_path=str(clip.path), video_start=0.0, duration=length,
                audio_path=str(clip.path) if clip.info.has_audio else None,
                audio_gain_db=config.ambient_gain_db,
                note=f"{clip.label}",
            ))
        return

    broll_queue = list(broll_clips)
    for index, clip in enumerate(speech_clips):
        spans = _speech_spans(clip, config)
        for start, end in spans:
            length = end - start
            plan.subtitles.extend(_subtitles_for(
                {"clip": clip, "start": start, "end": end}, cursor))
            plan.items.append(Item(
                type="speech", section="本編",
                video_path=str(clip.path), video_start=start, duration=length,
                audio_path=str(clip.path), audio_start=start,
                note=f"{clip.label} 話者",
            ))
            cursor += length

        # 喋りクリップの合間に風景を 1 つ挟む
        if broll_queue and index < len(speech_clips) - 1:
            broll = broll_queue.pop(0)
            length = min(broll.info.duration, config.broll_seconds)
            plan.items.append(Item(
                type="broll", section="本編",
                video_path=str(broll.path), video_start=0.0, duration=length,
                audio_path=str(broll.path) if broll.info.has_audio else None,
                audio_gain_db=config.ambient_gain_db,
                note=f"{broll.label}",
            ))
            cursor += length


def _speech_spans(clip: Clip, config: Config) -> list[tuple[float, float]]:
    if not clip.segments:
        return []
    start = max(0.0, clip.segments[0]["start"] - config.pad_before)
    end = min(clip.info.duration, clip.segments[-1]["end"] + config.pad_after)
    return split_on_gaps(clip, start, end, config.max_gap_seconds)


# --------------------------------------------------------------------------

def _subtitles_for(piece: dict, offset: float) -> list[dict]:
    """使用区間に重なる文字起こしを、タイムライン上の字幕に変換する。"""
    clip: Clip = piece["clip"]
    start, end = piece["start"], piece["end"]
    subtitles = []
    for segment in clip.segments:
        overlap_start = max(segment["start"], start)
        overlap_end = min(segment["end"], end)
        if overlap_end - overlap_start < 0.2:
            continue
        text = segment["text"].strip()
        if not text:
            continue
        subtitles.append({
            "start": round(offset + overlap_start - start, 3),
            "end": round(offset + overlap_end - start, 3),
            "text": text,
        })
    return subtitles
