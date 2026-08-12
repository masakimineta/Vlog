"""1 フレームぶんの絵を描く。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import geo
from .itinerary import AIRPORTS, DAY_LABELS, Leg

DATA_DIR = Path(__file__).parent / "data"

# 日本語が出せるフォントの候補。上から順に探して、最初に見つかったものを使う。
FONT_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    # Windows
    "C:/Windows/Fonts/YuGothB.ttc",
    "C:/Windows/Fonts/meiryob.ttc",
    "C:/Windows/Fonts/msgothic.ttc",
    # Linux
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


@dataclass(frozen=True)
class Theme:
    sea: tuple[int, int, int]
    land: tuple[int, int, int]
    coast: tuple[int, int, int]
    grid: tuple[int, int, int]
    route: tuple[int, int, int]
    route_head: tuple[int, int, int]
    dot_idle: tuple[int, int, int]
    dot_live: tuple[int, int, int]
    text: tuple[int, int, int]
    text_dim: tuple[int, int, int]


THEMES = {
    # 映像に重ねても沈まない、暗い海に暖色の線
    "dark": Theme(
        sea=(13, 21, 32),
        land=(30, 44, 60),
        coast=(62, 86, 112),
        grid=(26, 38, 52),
        route=(255, 94, 58),
        route_head=(255, 214, 170),
        dot_idle=(96, 122, 148),
        dot_live=(255, 255, 255),
        text=(232, 238, 245),
        text_dim=(132, 154, 178),
    ),
    "light": Theme(
        sea=(238, 243, 248),
        land=(255, 255, 255),
        coast=(176, 192, 208),
        grid=(226, 234, 242),
        route=(226, 62, 32),
        route_head=(255, 150, 90),
        dot_idle=(150, 168, 186),
        dot_live=(20, 32, 46),
        text=(24, 36, 50),
        text_dim=(110, 130, 150),
    ),
}


def find_font() -> str | None:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def load_coastline() -> list[list[list[float]]]:
    with open(DATA_DIR / "japan-coastline.json", encoding="utf-8") as f:
        return json.load(f)["rings"]


class Renderer:
    """行程を受け取り、進み具合を指定してフレームを 1 枚返す。"""

    def __init__(
        self,
        legs: list[Leg],
        width: int,
        height: int,
        theme: str = "dark",
        supersample: int = 2,
        show_map: bool = True,
        transparent: bool = False,
        font_path: str | None = None,
        bow_base: float = 0.012,
        bow_spread: float = 0.018,
    ):
        self.legs = legs
        self.width = width
        self.height = height
        self.theme = THEMES[theme]
        self.ss = max(1, supersample)
        self.show_map = show_map
        self.transparent = transparent

        # 実際に描く解像度。あとで縮小してギザギザを消す。
        self.w = width * self.ss
        self.h = height * self.ss

        self.coastline = load_coastline() if show_map else []

        # 空港と海岸線が全部入るように画角を決める
        points = [(a.lon, a.lat) for a in AIRPORTS.values()]
        if show_map:
            # 海岸線は空港のまわりだけ考慮する（北海道まで入れると図が小さくなる）
            lon_min = min(p[0] for p in points) - 1.6
            lon_max = max(p[0] for p in points) + 1.4
            lat_min = min(p[1] for p in points) - 1.3
            lat_max = max(p[1] for p in points) + 1.3
            points = [(lon_min, lat_min), (lon_max, lat_max)]
        self.view = geo.Viewport.fit(points, self.w, self.h, margin=0.10)

        # 各レグの弧をあらかじめ計算しておく（毎フレーム計算しなくていい）
        bows = geo.assign_bows(
            legs, base=bow_base * self.h, spread=bow_spread * self.h
        )
        self.arcs = []
        for leg, bow in zip(legs, bows):
            # 弧は必ずコード順（例: FUK→KMI）で作り、逆向きの便は点の並びだけ
            # 反転させる。こうしないと往路と復路が同じ側に重なる。
            reverse = leg.origin > leg.dest
            a, b = AIRPORTS[leg.origin], AIRPORTS[leg.dest]
            if reverse:
                a, b = b, a
            pts = geo.arc_points(
                self.view.to_screen(a.lon, a.lat),
                self.view.to_screen(b.lon, b.lat),
                bow,
            )
            if reverse:
                pts.reverse()
            self.arcs.append(pts)

        path = font_path or find_font()
        self.font_big = self._font(path, int(self.h * 0.045))
        self.font_mid = self._font(path, int(self.h * 0.030))
        self.font_small = self._font(path, int(self.h * 0.022))

    @staticmethod
    def _font(path: str | None, size: int):
        if path:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                pass
        return ImageFont.load_default(size)

    # --- 部品ごとの描画 ---------------------------------------------

    def _draw_map(self, d: ImageDraw.ImageDraw) -> None:
        t = self.theme
        for ring in self.coastline:
            pts = [self.view.to_screen(lon, lat) for lon, lat in ring]
            if len(pts) < 3:
                continue
            d.polygon(pts, fill=t.land, outline=t.coast, width=max(1, self.ss))

    def _draw_grid(self, d: ImageDraw.ImageDraw) -> None:
        """緯線・経線をうっすら入れて、地図であることを分かりやすくする。"""
        t = self.theme
        for lon in range(126, 145):
            x0, y0 = self.view.to_screen(lon, 24)
            x1, y1 = self.view.to_screen(lon, 46)
            d.line([(x0, y0), (x1, y1)], fill=t.grid, width=max(1, self.ss))
        for lat in range(28, 42):
            x0, y0 = self.view.to_screen(120, lat)
            x1, y1 = self.view.to_screen(150, lat)
            d.line([(x0, y0), (x1, y1)], fill=t.grid, width=max(1, self.ss))

    def _draw_airports(self, d: ImageDraw.ImageDraw, visited: set[str]) -> None:
        t = self.theme
        r = self.h * 0.008
        for code, ap in AIRPORTS.items():
            x, y = self.view.to_screen(ap.lon, ap.lat)
            live = code in visited
            color = t.dot_live if live else t.dot_idle
            if live:
                # 訪問済みは淡い輪を足して目立たせる
                halo = r * 2.1
                d.ellipse(
                    [x - halo, y - halo, x + halo, y + halo],
                    outline=t.route,
                    width=max(1, int(self.ss * 1.5)),
                )
            d.ellipse([x - r, y - r, x + r, y + r], fill=color)

            gap = r * 2.4
            anchors = {
                "right": ((x + gap, y), "lm"),
                "left": ((x - gap, y), "rm"),
                "above": ((x, y - gap), "ms"),
                "below": ((x, y + gap), "ma"),
            }
            pos, anchor = anchors.get(ap.label_side, anchors["right"])
            d.text(
                pos,
                code,
                font=self.font_small,
                fill=t.text if live else t.text_dim,
                anchor=anchor,
            )

    def _draw_routes(self, d: ImageDraw.ImageDraw, index: int, progress: float) -> None:
        t = self.theme
        width = max(2, int(self.h * 0.0035))

        # 済んだレグ
        for arc in self.arcs[:index]:
            d.line(arc, fill=t.route, width=width, joint="curve")

        # 描いている途中のレグ
        if index < len(self.arcs) and progress > 0:
            drawn = geo.partial(self.arcs[index], progress)
            if len(drawn) >= 2:
                d.line(drawn, fill=t.route, width=width, joint="curve")
                # 先端の光点
                hx, hy = drawn[-1]
                hr = width * 1.6
                d.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=t.route_head)

    def _draw_hud(self, d: ImageDraw.ImageDraw, index: int, progress: float) -> None:
        t = self.theme
        pad = self.h * 0.06
        done = index + (1 if progress > 0 else 0)
        done = min(done, len(self.legs))
        current = self.legs[min(index, len(self.legs) - 1)]

        # 左上: 何日目か
        d.text(
            (pad, pad),
            DAY_LABELS.get(current.day, f"Day{current.day}"),
            font=self.font_mid,
            fill=t.text,
            anchor="ls",
        )

        # 右上: レグの通し番号
        d.text(
            (self.w - pad, pad),
            f"{done:02d}",
            font=self.font_big,
            fill=t.text,
            anchor="rs",
        )
        d.text(
            (self.w - pad, pad + self.h * 0.035),
            f"/ {len(self.legs)} レグ",
            font=self.font_small,
            fill=t.text_dim,
            anchor="rs",
        )

        # 左下: いま飛んでいる区間
        if progress > 0:
            label = f"{current.origin} → {current.dest}"
            if current.time:
                label += f"   {current.time}"
            d.text(
                (pad, self.h - pad),
                label,
                font=self.font_small,
                fill=t.text_dim,
                anchor="ls",
            )

    # --- 1 枚描く ---------------------------------------------------

    def frame(self, index: int, progress: float) -> Image.Image:
        """index 番目のレグを progress まで描いた状態の 1 枚を返す。"""
        t = self.theme
        bg = (0, 0, 0, 0) if self.transparent else (*t.sea, 255)
        img = Image.new("RGBA", (self.w, self.h), bg)
        d = ImageDraw.Draw(img)

        if self.show_map:
            if not self.transparent:
                self._draw_grid(d)
            self._draw_map(d)

        # 描き終わったレグの両端が「訪問済み」。描いている途中は出発地だけ。
        visited: set[str] = set()
        for leg in self.legs[: min(index, len(self.legs))]:
            visited.add(leg.origin)
            visited.add(leg.dest)
        if index < len(self.legs):
            visited.add(self.legs[index].origin)
            if progress >= 1.0:
                visited.add(self.legs[index].dest)

        self._draw_routes(d, index, progress)
        self._draw_airports(d, visited)
        self._draw_hud(d, index, progress)

        if self.ss > 1:
            img = img.resize((self.width, self.height), Image.LANCZOS)
        return img
