"""撮影した素材を、行程どおりのフォルダへ振り分ける。"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from .schedule import DAY_DATES, EPISODE_ID, Slot, build_slots, slot_for

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".insv", ".lrf"}
AUDIO_EXT = {".wav", ".m4a", ".mp3"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".dng", ".heic"}


def shot_at(path: Path, use_metadata: bool) -> datetime:
    """撮影時刻を求める。

    既定はファイルの更新時刻。カメラが書き込んだ時刻がそのまま残るので、
    ほとんどの場合はこれで足りる。`--use-metadata` を付けると ffprobe で
    メタデータの撮影時刻を読む（ffprobe が無い場合は自動で更新時刻に戻る）。
    """
    if use_metadata and shutil.which("ffprobe"):
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_entries", "format_tags=creation_time", str(path)],
                capture_output=True, text=True, timeout=20, check=True,
            ).stdout
            raw = json.loads(out)["format"]["tags"]["creation_time"]
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime)


def kind_of(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in VIDEO_EXT:
        return "01_映像"
    if ext in AUDIO_EXT:
        return "02_音声"
    if ext in PHOTO_EXT:
        return "03_写真"
    return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m organizefootage",
        description="撮影素材を行程どおりのフォルダへ振り分け、素材一覧を作ります。",
    )
    p.add_argument("source", type=Path, help="カメラから吸い出した素材のフォルダ")
    p.add_argument("-o", "--output", type=Path, required=True,
                   help="振り分け先（例: ~/Movies/Vlog素材/EP002-Fukuoka-JGC-Shugyo_Aug2026）")
    p.add_argument("--move", action="store_true",
                   help="コピーではなく移動する（既定はコピー。元を残すほうが安全）")
    p.add_argument("--use-metadata", action="store_true",
                   help="ffprobe で撮影時刻のメタデータを読む（既定はファイルの更新時刻）")
    p.add_argument("--shift-hours", type=float, default=0.0,
                   help="時刻を補正する。カメラの時計が現地時刻でないときに使う（例: -1）")
    p.add_argument("--dry-run", action="store_true",
                   help="実際には動かさず、振り分け結果だけ表示する")
    p.add_argument("--target", type=int, default=150,
                   help="目標素材本数。合格ラインの判定に使う（既定: 150）")
    args = p.parse_args(argv)

    if not args.source.is_dir():
        print(f"素材フォルダが見つかりません: {args.source}")
        return 1

    slots = build_slots()
    files = sorted(f for f in args.source.rglob("*") if f.is_file() and kind_of(f))
    if not files:
        print(f"対象の素材がありません: {args.source}")
        return 1

    assigned: dict[str, list[tuple[datetime, Path, Slot]]] = {}
    unmatched: list[tuple[datetime, Path]] = []
    shift = timedelta(hours=args.shift_hours)

    for f in files:
        moment = shot_at(f, args.use_metadata) + shift
        slot = slot_for(moment, slots)
        if slot is None:
            unmatched.append((moment, f))
            continue
        assigned.setdefault(slot.folder, []).append((moment, f, slot))

    # --- 振り分け ---
    action = "移動" if args.move else "コピー"
    total = 0
    for folder in sorted(assigned, key=lambda k: min(m for m, _, _ in assigned[k])):
        items = sorted(assigned[folder])
        print(f"\n{folder}  ({len(items)} 本)")
        for moment, src, _slot in items:
            dest_dir = args.output / kind_of(src) / folder
            dest = dest_dir / src.name
            print(f"    {moment:%m/%d %H:%M}  {src.name}")
            if not args.dry_run:
                dest_dir.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    print(f"      すでにあるので飛ばしました: {dest}")
                    continue
                (shutil.move if args.move else shutil.copy2)(str(src), str(dest))
            total += 1

    if unmatched:
        print(f"\n■ 行程の日付に当てはまらない素材 ({len(unmatched)} 本)")
        print("  カメラの時計がずれている可能性があります。--shift-hours で補正してください。")
        for moment, src in sorted(unmatched)[:10]:
            print(f"    {moment:%Y/%m/%d %H:%M}  {src.name}")
        if len(unmatched) > 10:
            print(f"    ほか {len(unmatched) - 10} 本")
        if not args.dry_run:
            other = args.output / "00_未分類"
            other.mkdir(parents=True, exist_ok=True)
            for _moment, src in unmatched:
                dest = other / src.name
                if not dest.exists():
                    (shutil.move if args.move else shutil.copy2)(str(src), str(dest))

    # --- 素材一覧を書く ---
    videos = sum(1 for f in files if kind_of(f) == "01_映像")
    if not args.dry_run:
        index = args.output / "素材一覧.md"
        index.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# 素材一覧: {EPISODE_ID}",
            "",
            f"- 生成日時: {datetime.now():%Y-%m-%d %H:%M}",
            f"- 映像 {videos} 本 / 全 {len(files)} ファイル",
            "",
            "**カット番号は編集時に手で埋める。**"
            " `撮影計画.md` のカットリストと突き合わせて、使ったものにチェックを入れる。",
            "",
            "| 時刻 | 振り分け先 | ファイル | カット番号 |",
            "|---|---|---|---|",
        ]
        rows = [(m, s, p_) for k in assigned for m, p_, s in assigned[k]]
        for moment, slot, src in sorted(rows):
            lines.append(f"| {moment:%m/%d %H:%M} | {slot.folder} | `{src.name}` | |")
        index.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n素材一覧を書きました: {index}")

    print(f"\n{action}: {total} 本  /  映像素材: {videos} 本")
    if videos >= args.target:
        print(f"合格ライン（{args.target}本以上）を満たしています。")
    else:
        print(f"合格ラインに {args.target - videos} 本足りません。"
              f"（`企画書.md` の合格ラインを参照）")
    return 0
