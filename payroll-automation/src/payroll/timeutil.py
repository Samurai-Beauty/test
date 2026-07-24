"""タイムゾーン変換ユーティリティ(T2)。

Square APIはRFC3339(UTC 'Z' またはオフセット付き)で日時を返す。
深夜判定・日次分割に直結するため、内部処理はすべて Asia/Tokyo など
ルール指定のタイムゾーンへ変換してから行う(設計書 3.3)。
"""

from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def parse_rfc3339(value: str) -> datetime:
    """RFC3339文字列 → aware datetime。naive(オフセット無し)は不正として拒否する。"""
    if not isinstance(value, str) or not value:
        raise ValueError(f"日時文字列が不正です: {value!r}")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError(f"タイムゾーン情報のない日時は扱えません: {value!r}")
    return dt


def to_zone(value: str | datetime, tz: ZoneInfo = JST) -> datetime:
    """RFC3339文字列 or aware datetime → 指定タイムゾーンの datetime。"""
    dt = parse_rfc3339(value) if isinstance(value, str) else value
    if dt.tzinfo is None:
        raise ValueError("naive datetime は扱えません(タイムゾーン必須)")
    return dt.astimezone(tz)


def ceil_minutes(seconds: float) -> int:
    """秒 → 分(端数秒は切り上げ=労働者有利側)。"""
    if seconds <= 0:
        return 0
    return math.ceil(seconds / 60)


def format_hhmm(minutes: int) -> str:
    """分 → 'H:MM' 表記(集計表示用)。"""
    sign = "-" if minutes < 0 else ""
    minutes = abs(minutes)
    return f"{sign}{minutes // 60}:{minutes % 60:02d}"
