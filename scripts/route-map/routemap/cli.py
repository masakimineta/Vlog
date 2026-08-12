"""コマンドラインから動かす部分。"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import itinerary
from .encode import encode, write_png_sequence
from .render import Renderer

SIZES = {
    "4k": (3840, 2160),
    "1080p": (1920, 1080),
    "720p": (1280, 720),
}


def parse_size(value: str) -> tuple[int, int]:
    if value.lower() in SIZES:
        return SIZES[value.lower()]
    try:
        w, h = value.lower().split("x")
        return int(w), int(h)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"サイズの指定が読めません: {value}（例: 4k / 1080p / 1920x1080）"
        )


def build_timeline(
    leg_count: int,
    fps: int,
    draw_sec: float,
    hold_sec: float,
    intro_sec: float,
    outro_sec: float,
) -> list[tuple[int, float]]:
    """各フレームで「何本目をどこまで描くか」を並べたリストを作る。"""
    timeline: list[tuple[int, float]] = []

    # 地図と空港だけ映して、少し溜める
    timeline += [(0, 0.0)] * round(intro_sec * fps)

    for i in range(leg_count):
        draw_frames = max(1, round(draw_sec * fps))
        for f in range(draw_frames):
            timeline.append((i, (f + 1) / draw_frames))
        timeline += [(i, 1.0)] * round(hold_sec * fps)

    # 全部引き終わった状態で止める
    timeline += [(leg_count, 0.0)] * round(outro_sec * fps)
    return timeline


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m routemap",
        description="行程から、線が伸びていくルート図の動画を作ります。",
    )
    p.add_argument(
        "-o", "--output", type=Path, default=Path("routemap.mp4"),
        help="出力ファイル。.mp4 / .mov / .webm（既定: routemap.mp4）",
    )
    p.add_argument(
        "--size", type=parse_size, default="1080p",
        help="解像度。4k / 1080p / 720p / 1920x1080（既定: 1080p）",
    )
    p.add_argument("--fps", type=int, default=30, help="フレームレート（既定: 30）")
    p.add_argument(
        "--theme", choices=["dark", "light"], default="dark",
        help="配色（既定: dark）",
    )
    p.add_argument(
        "--alpha", action="store_true",
        help="背景を透過にする。映像に重ねるとき用。.mov か .webm で出力すること",
    )
    p.add_argument(
        "--no-map", action="store_true",
        help="海岸線を描かず、点と線だけの図にする",
    )
    p.add_argument("--draw-sec", type=float, default=0.40, help="1 レグを描く秒数")
    p.add_argument("--hold-sec", type=float, default=0.15, help="1 レグ描いたあと止める秒数")
    p.add_argument("--intro-sec", type=float, default=1.0, help="頭で止める秒数")
    p.add_argument("--outro-sec", type=float, default=1.8, help="最後に止める秒数")
    p.add_argument(
        "--days", type=int, nargs="+", metavar="N",
        help="指定した日だけ描く（例: --days 1 2）。省略すると全日",
    )
    p.add_argument(
        "--supersample", type=int, default=2,
        help="この倍率で描いてから縮小し、線のギザギザを消す（既定: 2）",
    )
    p.add_argument("--font", type=Path, help="使うフォントファイル")
    p.add_argument(
        "--frames", type=Path, metavar="DIR",
        help="動画ではなく連番 PNG をこのフォルダに書き出す",
    )
    p.add_argument(
        "--bow-base", type=float, default=0.012,
        help="弧のふくらみの基準値（画面の高さに対する割合）",
    )
    p.add_argument(
        "--bow-spread", type=float, default=0.018,
        help="同じ区間を繰り返したときに外へ広げる量。0 にすると扇状にならず重なる",
    )
    args = p.parse_args(argv)

    itinerary.validate()
    legs = itinerary.LEGS
    if args.days:
        legs = [leg for leg in legs if leg.day in set(args.days)]
        if not legs:
            print(f"指定された日に便がありません: {args.days}", file=sys.stderr)
            return 1

    width, height = args.size
    renderer = Renderer(
        legs=legs,
        width=width,
        height=height,
        theme=args.theme,
        supersample=args.supersample,
        show_map=not args.no_map,
        transparent=args.alpha,
        font_path=str(args.font) if args.font else None,
        bow_base=args.bow_base,
        bow_spread=args.bow_spread,
    )

    timeline = build_timeline(
        len(legs), args.fps, args.draw_sec, args.hold_sec,
        args.intro_sec, args.outro_sec,
    )
    total = len(timeline)
    print(
        f"{len(legs)} レグ / {width}x{height} / {args.fps}fps / "
        f"{total} フレーム（約 {total / args.fps:.1f} 秒）"
    )

    started = time.time()

    def frames():
        for n, (index, progress) in enumerate(timeline, 1):
            if n % 30 == 0 or n == total:
                elapsed = time.time() - started
                print(
                    f"\r  {n}/{total} フレーム  {elapsed:.0f}秒経過",
                    end="", flush=True,
                )
            yield renderer.frame(index, progress)

    try:
        if args.frames:
            count = write_png_sequence(frames(), args.frames)
            print(f"\n書き出しました: {args.frames}/ に {count} 枚")
        else:
            encode(frames(), args.output, width, height, args.fps, args.alpha)
            print(f"\n書き出しました: {args.output}")
    except (ValueError, RuntimeError) as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    return 0
