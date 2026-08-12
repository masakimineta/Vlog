"""コマンドラインの入口。

    python -m autoedit auto 素材フォルダ --script 台本.md --bgm bgm.mp3 -o 完成.mp4

段階ごとに実行することもできる。

    python -m autoedit scan   素材フォルダ -o analysis.json
    python -m autoedit plan   -a analysis.json -s 台本.md -o plan.json
    python -m autoedit render -p plan.json -o 完成.mp4
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Config
from .media import Clip, scan_clips
from .planner import build_plan
from .render import render
from .script_parser import parse_script
from .transcribe import TranscriptionUnavailable, transcribe_all


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1

    try:
        return args.handler(args)
    except (FileNotFoundError, ValueError, TranscriptionUnavailable) as error:
        print(f"\nエラー: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n中断しました", file=sys.stderr)
        return 130


# --------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autoedit",
        description="素材と台本から Vlog を自動編集する",
    )
    sub = parser.add_subparsers(dest="command")

    def add_common(p):
        p.add_argument("--config", type=Path, help="設定 JSON のパス")

    scan = sub.add_parser("scan", help="素材を解析して文字起こしする")
    scan.add_argument("source", type=Path, help="素材フォルダ")
    scan.add_argument("-o", "--output", type=Path, default=Path("analysis.json"))
    scan.add_argument("--model", help="Whisper のモデル名（tiny/base/small/medium/large-v3）")
    scan.add_argument("--lang", help="音声の言語（既定: ja）")
    scan.add_argument("--no-transcribe", action="store_true",
                      help="文字起こしせず、すべて風景クリップとして扱う")
    scan.add_argument("--include-audio", action="store_true",
                      help="単体の音声ファイルも対象にする")
    add_common(scan)
    scan.set_defaults(handler=_cmd_scan)

    plan = sub.add_parser("plan", help="編集プランを作る")
    plan.add_argument("-a", "--analysis", type=Path, default=Path("analysis.json"))
    plan.add_argument("-s", "--script", type=Path, help="台本 Markdown")
    plan.add_argument("-b", "--bgm", type=Path, help="BGM の音声ファイル")
    plan.add_argument("-o", "--output", type=Path, default=Path("plan.json"))
    plan.add_argument("--match-threshold", type=float,
                      help="台本と音声の一致率のしきい値（既定: 0.55）")
    add_common(plan)
    plan.set_defaults(handler=_cmd_plan)

    render_cmd = sub.add_parser("render", help="プランから動画を書き出す")
    render_cmd.add_argument("-p", "--plan", type=Path, default=Path("plan.json"))
    render_cmd.add_argument("-o", "--output", type=Path, default=Path("output.mp4"))
    render_cmd.add_argument("--no-subtitles", action="store_true", help="字幕を焼き込まない")
    render_cmd.add_argument("--workdir", type=Path, help="作業フォルダ（指定すると残る）")
    add_common(render_cmd)
    render_cmd.set_defaults(handler=_cmd_render)

    auto = sub.add_parser("auto", help="解析からの書き出しまで通しで実行する")
    auto.add_argument("source", type=Path, help="素材フォルダ")
    auto.add_argument("-s", "--script", type=Path, help="台本 Markdown")
    auto.add_argument("-b", "--bgm", type=Path, help="BGM の音声ファイル")
    auto.add_argument("-o", "--output", type=Path, default=Path("output.mp4"))
    auto.add_argument("--model", help="Whisper のモデル名")
    auto.add_argument("--lang", help="音声の言語")
    auto.add_argument("--no-transcribe", action="store_true")
    auto.add_argument("--no-subtitles", action="store_true")
    auto.add_argument("--match-threshold", type=float)
    auto.add_argument("--workdir", type=Path)
    auto.add_argument("--include-audio", action="store_true")
    add_common(auto)
    auto.set_defaults(handler=_cmd_auto)

    return parser


def _config_from(args) -> Config:
    config = Config.load(getattr(args, "config", None))
    if getattr(args, "model", None):
        config.whisper_model = args.model
    if getattr(args, "lang", None):
        config.language = args.lang
    if getattr(args, "match_threshold", None) is not None:
        config.match_threshold = args.match_threshold
    if getattr(args, "no_subtitles", False):
        config.burn_subtitles = False
    return config


# --------------------------------------------------------------------------

def _cmd_scan(args) -> int:
    config = _config_from(args)
    clips = _scan(args.source, config, args)
    _write_analysis(clips, config, args.output)
    return 0


def _scan(source: Path, config: Config, args) -> list[Clip]:
    print(f"素材を探しています: {source}")
    clips = scan_clips(source, include_audio=getattr(args, "include_audio", False))
    print(f"{len(clips)} 件の素材が見つかりました\n")

    transcribe_all(clips, config, enabled=not args.no_transcribe)

    speech = sum(1 for c in clips if c.kind == "speech")
    print(f"\n喋りあり: {speech} 件 / 風景: {len(clips) - speech} 件")
    return clips


def _write_analysis(clips: list[Clip], config: Config, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "config": config.to_dict(),
        "clips": [clip.to_dict() for clip in clips],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"解析結果を書き出しました: {output}")


def _cmd_plan(args) -> int:
    config = _config_from(args)
    if not args.analysis.exists():
        raise FileNotFoundError(
            f"{args.analysis} がありません。先に scan を実行してください"
        )
    data = json.loads(args.analysis.read_text(encoding="utf-8"))
    clips = [Clip.from_dict(entry) for entry in data["clips"]]

    sections = None
    if args.script:
        sections = parse_script(args.script)
        print(f"台本を読み込みました: {len(sections)} セクション")

    plan = build_plan(clips, sections, config, bgm=args.bgm)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(plan.to_dict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _print_plan(plan)
    print(f"\n編集プランを書き出しました: {args.output}")
    return 0


def _print_plan(plan) -> None:
    print(f"\n--- 編集プラン（全 {_mmss(plan.duration)}／{len(plan.items)} コマ）---")
    section = None
    for item in plan.items:
        if item.section != section:
            section = item.section
            print(f"\n■ {section}")
        print(f"   {item.duration:5.1f}秒  {item.note}")

    if plan.chapters:
        print("\n■ チャプター")
        for chapter in plan.chapters:
            print(f"   {_mmss(chapter['start'])} {chapter['title']}")

    if plan.warnings:
        print("\n■ 注意")
        for warning in plan.warnings:
            print(f"   - {warning}")


def _cmd_render(args) -> int:
    config = _config_from(args)
    if not args.plan.exists():
        raise FileNotFoundError(f"{args.plan} がありません。先に plan を実行してください")

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    if args.no_subtitles:
        config.burn_subtitles = False

    output = render(plan, args.output, config, workdir=args.workdir,
                    keep_workdir=args.workdir is not None)
    print(f"\n完成しました: {output}")
    return 0


def _cmd_auto(args) -> int:
    config = _config_from(args)
    clips = _scan(args.source, config, args)

    analysis_path = args.output.with_suffix(".analysis.json")
    _write_analysis(clips, config, analysis_path)

    sections = None
    if args.script:
        sections = parse_script(args.script)
        print(f"\n台本を読み込みました: {len(sections)} セクション")

    plan = build_plan(clips, sections, config, bgm=args.bgm)
    plan_path = args.output.with_suffix(".plan.json")
    plan_path.write_text(
        json.dumps(plan.to_dict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _print_plan(plan)
    print(f"\n編集プラン: {plan_path}\n")

    output = render(json.loads(plan_path.read_text(encoding="utf-8")),
                    args.output, config, workdir=args.workdir,
                    keep_workdir=args.workdir is not None)
    print(f"\n完成しました: {output}")
    return 0


def _mmss(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}:{secs:02d}"
