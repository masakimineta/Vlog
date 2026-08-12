"""ffmpeg を使わない部分の動作確認。

    cd scripts/auto-edit && python -m unittest discover tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autoedit.config import Config
from autoedit.ffmpeg import MediaInfo
from autoedit.matcher import find_best_match, normalize, split_on_gaps, trim_fillers
from autoedit.media import Clip, parse_cut_id
from autoedit.planner import build_plan
from autoedit.script_parser import parse_script


def make_clip(cut_id: str, duration: float, segments: list[tuple[float, float, str]]
              ) -> Clip:
    clip = Clip(
        path=Path(f"/tmp/{cut_id}.mp4"),
        cut_id=cut_id,
        info=MediaInfo(duration, 1920, 1080, 30.0, True, True),
        kind="speech" if segments else "broll",
    )
    clip.segments = [{"start": s, "end": e, "text": t} for s, e, t in segments]
    for start, end, text in segments:
        span = (end - start) / max(len(text), 1)
        for index, char in enumerate(text):
            clip.words.append({
                "start": round(start + span * index, 3),
                "end": round(start + span * (index + 1), 3),
                "text": char,
            })
    return clip


class TestFilenames(unittest.TestCase):
    def test_cut_id_from_naming_rule(self):
        self.assertEqual(parse_cut_id("20260412-EP001-A001-清水寺.mp4"), "A001")
        self.assertEqual(parse_cut_id("20260412-EP001-B012-手元.mp4"), "B012")

    def test_episode_number_is_not_mistaken_for_cut_id(self):
        self.assertIsNone(parse_cut_id("20260412-EP001-清水寺.mp4"))

    def test_no_cut_id(self):
        self.assertIsNone(parse_cut_id("IMG_0421.mov"))


class TestNormalize(unittest.TestCase):
    def test_absorbs_punctuation_and_width(self):
        self.assertEqual(normalize("今は６時に、起きて。"), normalize("今は6時に起きて"))


class TestScriptParser(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / "台本.md"

    def tearDown(self):
        self.tmp.cleanup()

    def test_parses_sections_visuals_and_narration(self):
        self.path.write_text(
            "# 台本: EP001 テスト\n\n"
            "## 表記ルール\n\n- 無視される\n\n"
            "## 導入\n\n"
            "[映像] B001 風景 / B002 手元 3秒\n\n"
            "> こんにちは。\n\n"
            "[テロップ] テスト\n\n"
            "## 使わなかったが残しておくネタ\n\n- 無視される\n",
            encoding="utf-8",
        )
        sections = parse_script(self.path)

        self.assertEqual([s.title for s in sections], ["導入"])
        section = sections[0]
        self.assertEqual(len(section.visuals), 2)
        self.assertEqual(section.visuals[0].cut_ids, ["B001"])
        self.assertEqual(section.visuals[1].seconds, 3.0)
        self.assertEqual(section.captions, ["テスト"])
        self.assertEqual(section.narrations[0].text, "こんにちは。")
        self.assertTrue(section.narrations[0].verbatim)

    def test_bullets_are_non_verbatim(self):
        self.path.write_text("# 台本\n\n## 本編\n\n- 要点をひとつ話す\n", encoding="utf-8")
        section = parse_script(self.path)[0]
        self.assertFalse(section.narrations[0].verbatim)

    def test_strips_bold_and_timecode_from_headings(self):
        self.path.write_text(
            "# 台本\n\n## 導入（0:00-0:15）\n\n> **強調**した話。\n", encoding="utf-8"
        )
        section = parse_script(self.path)[0]
        self.assertEqual(section.title, "導入")
        self.assertEqual(section.narrations[0].text, "強調した話。")


class TestMatcher(unittest.TestCase):
    def test_finds_matching_span_across_clips(self):
        clips = [
            make_clip("A001", 12.0, [(0.5, 4.0, "今日はいい天気ですね")]),
            make_clip("A002", 12.0, [(1.0, 5.0, "朝の筋トレは続かなかった")]),
        ]
        match = find_best_match("朝の筋トレは続かなかった。", clips, threshold=0.55)
        self.assertIsNotNone(match)
        self.assertEqual(match.clip.cut_id, "A002")
        self.assertAlmostEqual(match.start, 1.0, delta=0.5)
        self.assertAlmostEqual(match.end, 5.0, delta=0.5)

    def test_returns_none_below_threshold(self):
        clips = [make_clip("A001", 12.0, [(0.5, 4.0, "今日はいい天気ですね")])]
        self.assertIsNone(
            find_best_match("まったく関係のない内容です", clips, threshold=0.9)
        )

    def test_prefers_the_better_take(self):
        clips = [
            make_clip("A001", 12.0, [(0.0, 3.0, "えーと、散歩と両方は")]),
            make_clip("A002", 12.0, [(0.0, 3.0, "散歩と両方は無理でした")]),
        ]
        match = find_best_match("散歩と両方は無理でした", clips, threshold=0.5)
        self.assertEqual(match.clip.cut_id, "A002")

    def test_split_on_gaps_drops_dead_air(self):
        clip = make_clip("A001", 12.0, [(0.0, 2.0, "まえはん"), (5.0, 7.0, "あとはん")])
        spans = split_on_gaps(clip, 0.0, 7.0, max_gap=0.6)
        self.assertEqual(len(spans), 2)
        self.assertAlmostEqual(spans[0][1], 2.0, delta=0.1)
        self.assertAlmostEqual(spans[1][0], 5.0, delta=0.1)

    def test_trim_fillers_removes_leading_filler(self):
        clip = Clip(path=Path("/tmp/a.mp4"), cut_id="A001",
                    info=MediaInfo(10, 1920, 1080, 30, True, True), kind="speech")
        clip.words = [
            {"start": 0.0, "end": 0.5, "text": "えー"},
            {"start": 0.5, "end": 1.5, "text": "本題"},
        ]
        start, end = trim_fillers(clip, 0.0, 1.5, ["えー"])
        self.assertAlmostEqual(start, 0.5, delta=0.01)
        self.assertAlmostEqual(end, 1.5, delta=0.01)


class TestPlanner(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.script = Path(self.tmp.name) / "台本.md"
        self.config = Config()

    def tearDown(self):
        self.tmp.cleanup()

    def _clips(self):
        broll = Clip(path=Path("/tmp/B001.mp4"), cut_id="B001",
                     info=MediaInfo(6.0, 1920, 1080, 30, True, False), kind="broll")
        return [make_clip("A001", 12.0, [(0.5, 5.0, "朝の散歩は続いています")]), broll]

    def test_narrated_section_gets_broll_overlay(self):
        self.script.write_text(
            "# 台本\n\n## 導入\n\n[映像] B001 風景\n\n> 朝の散歩は続いています。\n",
            encoding="utf-8",
        )
        self.config.speaker_head_seconds = 1.0
        plan = build_plan(self._clips(), parse_script(self.script), self.config)

        kinds = [item.note for item in plan.items]
        self.assertTrue(any("被せ" in note for note in kinds), kinds)
        # 音声はすべて話者クリップから取る
        for item in plan.items:
            self.assertTrue(item.audio_path.endswith("A001.mp4"))
        self.assertEqual(plan.chapters[0]["title"], "導入")

    def test_visual_only_section_becomes_standalone_broll(self):
        self.script.write_text(
            "# 台本\n\n## 風景\n\n[映像] B001 空 3秒\n", encoding="utf-8"
        )
        plan = build_plan(self._clips(), parse_script(self.script), self.config)
        self.assertEqual(len(plan.items), 1)
        self.assertEqual(plan.items[0].type, "broll")
        self.assertAlmostEqual(plan.items[0].duration, 3.0, delta=0.01)

    def test_unmatched_narration_is_warned(self):
        # 映像指定があるので、照合に失敗してもプラン自体は成立する
        self.script.write_text(
            "# 台本\n\n## 導入\n\n[映像] B001 風景\n\n"
            "> まったく違う内容の長い台詞をここに書きます。\n",
            encoding="utf-8",
        )
        plan = build_plan(self._clips(), parse_script(self.script), self.config)
        self.assertTrue(any("見つかりませんでした" in w for w in plan.warnings))

    def test_all_unmatched_raises_with_detail(self):
        self.script.write_text(
            "# 台本\n\n## 導入\n\n> まったく違う内容の長い台詞をここに書きます。\n",
            encoding="utf-8",
        )
        with self.assertRaises(ValueError) as raised:
            build_plan(self._clips(), parse_script(self.script), self.config)
        # なぜ失敗したかが分かるように、照合できなかった行が含まれること
        self.assertIn("まったく違う内容", str(raised.exception))

    def test_without_script_orders_by_cut_id(self):
        plan = build_plan(self._clips(), None, self.config)
        self.assertEqual(plan.items[0].type, "speech")
        self.assertTrue(any("台本が指定されていない" in w for w in plan.warnings))

    def test_subtitles_are_timeline_relative(self):
        self.script.write_text(
            "# 台本\n\n## 導入\n\n> 朝の散歩は続いています。\n", encoding="utf-8"
        )
        plan = build_plan(self._clips(), parse_script(self.script), self.config)
        self.assertTrue(plan.subtitles)
        self.assertLess(plan.subtitles[0]["start"], 1.0)


if __name__ == "__main__":
    unittest.main()
