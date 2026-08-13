"""撮影日と行程の対応。

**便を変更したときは `scripts/route-map/routemap/itinerary.py` の `LEGS` を直すだけでいい。**
このツールはそこから行程を読むので、ルート図の動画と素材の振り分けが必ず一致する。
このファイルで持つのは「何日目が何月何日か」だけ。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

# scripts/route-map を読み込めるようにする（行程の定義をひとつに保つため）
_ROUTE_MAP = Path(__file__).resolve().parents[2] / "route-map"
if str(_ROUTE_MAP) not in sys.path:
    sys.path.insert(0, str(_ROUTE_MAP))

from routemap.itinerary import LEGS, validate  # noqa: E402

EPISODE_ID = "EP002"

# 何日目が何月何日か。Day0 は国内線に含まれない移動日（シドニー→羽田）。
DAY_DATES: dict[int, date] = {
    0: date(2026, 8, 19),
    1: date(2026, 8, 20),
    2: date(2026, 8, 21),
    3: date(2026, 8, 22),
    # JL51 は 8/22 の夜に羽田を出て、シドニー到着は 8/23 朝。
    # 機内と到着後の素材はこの日付になる。
    4: date(2026, 8, 23),
}

DAY_LABELS: dict[int, str] = {
    0: "Day0_Sydney出発",
    1: "Day1",
    2: "Day2",
    3: "Day3",
    4: "Day4_JL51機内-Sydney到着",
}


@dataclass(frozen=True)
class Slot:
    """素材を入れる箱（1レグぶん、または当日の最後の便より後）。"""

    day: int
    order: int          # その日の中での並び順
    folder: str         # 実際に作るフォルダ名
    description: str    # 索引に出す説明

    def sort_key(self) -> tuple[int, int]:
        return (self.day, self.order)


def _parse(value: str) -> time | None:
    try:
        h, m = value.split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        return None


def legs_of_day(day: int):
    """その日の便を出発時刻順に返す。"""
    legs = [leg for leg in LEGS if leg.day == day and _parse(leg.time)]
    return sorted(legs, key=lambda leg: _parse(leg.time))


def leg_number(leg) -> int:
    """通し番号（1〜17）。テロップの LEG xx / 17 と揃える。"""
    return LEGS.index(leg) + 1


def build_slots() -> list[Slot]:
    """全日ぶんの箱を作る。"""
    validate()
    slots: list[Slot] = []
    for day in sorted(DAY_DATES):
        label = DAY_LABELS[day]
        legs = legs_of_day(day)
        if not legs:
            # Day0 のように便を管理していない日は 1 箱にまとめる
            slots.append(
                Slot(day, 0, f"{label}", f"{DAY_DATES[day]:%m/%d} 移動日")
            )
            continue
        for order, leg in enumerate(legs, start=1):
            n = leg_number(leg)
            slots.append(
                Slot(
                    day,
                    order,
                    f"{label}/L{n:02d}_{leg.origin}-{leg.dest}_{leg.time.replace(':', '')}",
                    f"{n:02d}/17  {leg.origin}→{leg.dest} {leg.time} 発の前後",
                )
            )
        slots.append(
            Slot(day, 99, f"{label}/L{leg_number(legs[-1]):02d}_after",
                 "その日の最終便より後（食事・屋台・ホテル・振り返り）")
        )
    return slots


def slot_for(moment: datetime, slots: list[Slot]) -> Slot | None:
    """撮影時刻から、入れるべき箱を選ぶ。

    「次に乗る便」で振り分ける。空港で待っている間に撮った素材は、
    これから乗る便の箱に入るので、編集でそのまま順番に使える。
    """
    day = next((d for d, dt in DAY_DATES.items() if dt == moment.date()), None)
    if day is None:
        return None

    legs = legs_of_day(day)
    day_slots = [s for s in slots if s.day == day]
    if not legs:
        return day_slots[0] if day_slots else None

    for order, leg in enumerate(legs, start=1):
        if moment.time() <= _parse(leg.time):
            return next((s for s in day_slots if s.order == order), None)
    # 最終便より後
    return next((s for s in day_slots if s.order == 99), None)
