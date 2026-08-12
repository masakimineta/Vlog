"""緯度経度を画面の座標に変換して、路線の弧を計算する。"""

from __future__ import annotations

import math
from dataclasses import dataclass


def project(lon: float, lat: float, lat_ref: float) -> tuple[float, float]:
    """緯度経度を平面に落とす。

    国内スケールなら正確な図法は不要で、経度に緯度ぶんの補正をかけるだけで
    十分に見られる形になる（高緯度ほど経度 1 度の実距離が短くなるため）。
    """
    return lon * math.cos(math.radians(lat_ref)), -lat


@dataclass
class Viewport:
    """投影した座標を、指定サイズの画像の中に収める。"""

    width: int
    height: int
    lat_ref: float
    scale: float
    off_x: float
    off_y: float

    @classmethod
    def fit(
        cls,
        points: list[tuple[float, float]],
        width: int,
        height: int,
        margin: float = 0.08,
    ) -> "Viewport":
        """points（経度・緯度の並び）が全部入るように縮尺と位置を決める。"""
        lat_ref = sum(p[1] for p in points) / len(points)
        projected = [project(lon, lat, lat_ref) for lon, lat in points]
        xs = [p[0] for p in projected]
        ys = [p[1] for p in projected]
        span_x = max(xs) - min(xs)
        span_y = max(ys) - min(ys)

        pad_x = width * margin
        pad_y = height * margin
        scale = min(
            (width - 2 * pad_x) / span_x if span_x else float("inf"),
            (height - 2 * pad_y) / span_y if span_y else float("inf"),
        )

        # 収まる範囲の中心を画像の中心に合わせる
        mid_x = (max(xs) + min(xs)) / 2
        mid_y = (max(ys) + min(ys)) / 2
        return cls(
            width=width,
            height=height,
            lat_ref=lat_ref,
            scale=scale,
            off_x=width / 2 - mid_x * scale,
            off_y=height / 2 - mid_y * scale,
        )

    def to_screen(self, lon: float, lat: float) -> tuple[float, float]:
        x, y = project(lon, lat, self.lat_ref)
        return x * self.scale + self.off_x, y * self.scale + self.off_y


def arc_points(
    start: tuple[float, float],
    end: tuple[float, float],
    bow: float,
    steps: int = 96,
) -> list[tuple[float, float]]:
    """2 点を結ぶ弧（2 次ベジエ）の通過点を返す。

    bow は弧のふくらみを画面座標の絶対値で指定する。正負で膨らむ向きが変わる。
    距離に比例させないのは、福岡⇔宮崎のような短い区間でも扇が開くようにするため。
    """
    x0, y0 = start
    x1, y1 = end
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length == 0:
        return [start, end]

    # 進行方向に対して垂直な単位ベクトル
    nx, ny = -dy / length, dx / length
    # 制御点は中点から垂直に bow だけずらす（ベジエは制御点の半分まで寄るので 2 倍）
    cx = (x0 + x1) / 2 + nx * bow * 2
    cy = (y0 + y1) / 2 + ny * bow * 2

    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        pts.append(
            (
                u * u * x0 + 2 * u * t * cx + t * t * x1,
                u * u * y0 + 2 * u * t * cy + t * t * y1,
            )
        )
    return pts


def partial(points: list[tuple[float, float]], progress: float) -> list[tuple[float, float]]:
    """弧を progress（0.0〜1.0）のところまで切り出す。線が伸びる表現に使う。"""
    progress = max(0.0, min(1.0, progress))
    if progress <= 0:
        return []
    if progress >= 1:
        return list(points)

    # 弧長で切ると速度が一定に見える
    lengths = [0.0]
    for a, b in zip(points, points[1:]):
        lengths.append(lengths[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = lengths[-1]
    if total == 0:
        return [points[0]]

    target = total * progress
    out = [points[0]]
    for i in range(1, len(points)):
        if lengths[i] < target:
            out.append(points[i])
            continue
        # 最後の 1 区間は途中で止める
        prev = lengths[i - 1]
        seg = lengths[i] - prev
        t = (target - prev) / seg if seg else 0
        ax, ay = points[i - 1]
        bx, by = points[i]
        out.append((ax + (bx - ax) * t, ay + (by - ay) * t))
        break
    return out


def assign_bows(legs, base: float, spread: float) -> list[float]:
    """各レグの弧のふくらみを決める。

    同じ区間を何度も飛ぶと線が完全に重なって、画面が止まって見える。
    そこで **往路と復路を反対側に曲げ、往復するたびに外へ広げて扇状にする**。
    福岡⇔宮崎を 6 往復すると、上下に 6 本ずつ開いた図になる。

    符号は「区間をコード順に並べたとき」を基準にした向きで返す。進行方向を
    基準にすると、往路と復路で垂直方向も一緒に反転してしまい、結局同じ側に
    重なってしまうため。
    """
    seen: dict[tuple[str, str], int] = {}
    bows = []
    for leg in legs:
        key = (leg.origin, leg.dest)
        index = seen.get(key, 0)
        seen[key] = index + 1
        sign = 1.0 if leg.origin < leg.dest else -1.0
        bows.append(sign * (base + spread * index))
    return bows
