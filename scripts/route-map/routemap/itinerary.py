"""行程データ。

**行程が変わったらこのファイルだけ直します。** ほかは触らなくて構いません。

- 空港を増やすときは AIRPORTS に 1 行足す（緯度・経度は Google マップで空港を
  右クリックすると出てきます）
- 便を増減するときは LEGS の行を足す・消す・並べ替える

LEGS は上から順にアニメーションで描かれます。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Airport:
    code: str      # 図に出る 3 レターコード
    name: str      # 日本語名（今は図には出していない。メモ用）
    lat: float
    lon: float
    # ラベルを点のどちら側に置くか。図が重なるときだけ変える。
    # "right" / "left" / "above" / "below"
    label_side: str = "right"


@dataclass(frozen=True)
class Leg:
    day: int       # 何日目か（1 始まり）
    origin: str    # 出発空港のコード
    dest: str      # 到着空港のコード
    time: str = ""  # 図の隅に出す時刻。空文字なら出ない


# --- 空港 -------------------------------------------------------------

AIRPORTS = {
    a.code: a
    for a in [
        Airport("HND", "羽田", 35.5533, 139.7811, label_side="right"),
        Airport("ITM", "伊丹", 34.7855, 135.4382, label_side="above"),
        Airport("FUK", "福岡", 33.5859, 130.4506, label_side="left"),
        Airport("KMI", "宮崎", 31.8772, 131.4486, label_side="below"),
    ]
}


# --- 日ごとの見出し ---------------------------------------------------

DAY_LABELS = {
    1: "Day1  8/20",
    2: "Day2  8/21",
    3: "Day3  8/22",
}


# --- 便（この順に線が伸びます）----------------------------------------

LEGS = [
    # Day1 — 羽田から福岡へ入り、宮崎を 2 往復
    Leg(1, "HND", "FUK", "12:50"),
    Leg(1, "FUK", "KMI", "15:25"),
    Leg(1, "KMI", "FUK", "17:45"),
    Leg(1, "FUK", "KMI", "19:15"),
    Leg(1, "KMI", "FUK", "21:30"),
    # Day2 — 宮崎を 3 往復して羽田へ
    Leg(2, "FUK", "KMI", "09:50"),
    Leg(2, "KMI", "FUK", "11:55"),
    Leg(2, "FUK", "KMI", "12:40"),
    Leg(2, "KMI", "FUK", "14:50"),
    Leg(2, "FUK", "KMI", "15:25"),
    Leg(2, "KMI", "FUK", "17:45"),
    Leg(2, "FUK", "HND", "20:00"),
    # Day3 — 伊丹を挟んで、最後の宮崎往復
    Leg(3, "HND", "ITM", "08:20"),
    Leg(3, "ITM", "FUK", "10:05"),
    Leg(3, "FUK", "KMI", "11:50"),
    Leg(3, "KMI", "FUK", "13:05"),
    Leg(3, "FUK", "HND", "14:40"),
]


def validate() -> None:
    """LEGS に AIRPORTS へ無い空港コードが混ざっていないか確認する。"""
    unknown = {
        code
        for leg in LEGS
        for code in (leg.origin, leg.dest)
        if code not in AIRPORTS
    }
    if unknown:
        raise ValueError(
            "AIRPORTS に無い空港コードが LEGS にあります: "
            + ", ".join(sorted(unknown))
        )
