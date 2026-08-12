"""自動編集の設定。

既定値はここに書かれている。エピソードごとに変えたい場合は
JSON ファイルを用意して `--config autoedit.json` で渡す。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


@dataclass
class Config:
    # --- 書き出し ---
    width: int = 1920
    height: int = 1080
    fps: int = 30
    crf: int = 20
    preset: str = "medium"
    audio_bitrate: str = "192k"

    # --- 文字起こし ---
    whisper_model: str = "small"
    language: str = "ja"
    #: この秒数より喋りが短いクリップは「風景クリップ」として扱う
    speech_min_seconds: float = 1.5
    #: クリップ全体に占める喋りの割合がこれ未満なら風景クリップとみなす
    speech_min_ratio: float = 0.05

    # --- カット ---
    #: 台本と文字起こしの一致率がこれ未満なら採用しない
    match_threshold: float = 0.55
    #: 採用区間の前後に足す余白（秒）
    pad_before: float = 0.15
    pad_after: float = 0.35
    #: 喋りの合間の無音がこの秒数を超えたら詰める
    max_gap_seconds: float = 0.6
    #: 台本にBロール指定がある場合、冒頭この秒数は話者の映像を見せる
    speaker_head_seconds: float = 3.0
    #: 単独で挟むBロールの既定の長さ（秒）
    broll_seconds: float = 4.0
    #: 台本がないとき、1 クリップを最大何秒まで使うか
    broll_max_seconds: float = 12.0
    #: 除去するフィラー語
    fillers: list[str] = field(default_factory=lambda: [
        "えー", "えーと", "えっと", "あの", "あのー", "その", "まあ", "なんか",
    ])

    # --- 音声 ---
    #: 最終的なラウドネス（YouTube の基準に合わせる）
    target_lufs: float = -14.0
    #: BGM の基本音量（dB）
    bgm_gain_db: float = -8.0
    #: 喋りの下でBGMをどれだけ下げるか（dB）
    bgm_duck_db: float = -12.0
    #: 風景クリップの環境音の音量（dB）
    ambient_gain_db: float = -10.0
    bgm_fade_in: float = 1.5
    bgm_fade_out: float = 3.0

    # --- 字幕 ---
    burn_subtitles: bool = True
    subtitle_font_size: int = 22
    subtitle_font: str = ""  # 空ならOSごとの既定フォントを使う

    @classmethod
    def load(cls, path: Path | None) -> "Config":
        config = cls()
        if path is None:
            return config

        data = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(
                f"設定ファイルに未知の項目があります: {', '.join(sorted(unknown))}"
            )
        for key, value in data.items():
            setattr(config, key, value)
        return config

    def to_dict(self) -> dict:
        return asdict(self)


def default_subtitle_font() -> str:
    """OS ごとの日本語が出るフォント名を返す。"""
    import sys

    if sys.platform == "darwin":
        return "Hiragino Sans"
    if sys.platform == "win32":
        return "Yu Gothic UI"
    return "Noto Sans CJK JP"
