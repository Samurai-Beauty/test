"""テスト共通ヘルパー(ダミーデータのみ。実在の従業員名・実データ禁止)。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from payroll.models import Break, TeamMember, Timecard, WageInfo
from payroll.timeutil import JST

MON = date(2026, 7, 6)  # 月曜(基準週)
SUN = date(2026, 7, 12)  # 日曜(既定ルールの法定休日)

_seq = iter(range(100_000))


def jst(day: date, hhmm: str, plus_days: int = 0) -> datetime:
    hh, mm = map(int, hhmm.split(":"))
    return datetime.combine(day + timedelta(days=plus_days), time(hh, mm), tzinfo=JST)


def tc(
    member: str,
    day: date,
    start: str,
    end: str | None,
    breaks: list = (),
    end_plus_days: int = 0,
    location: str = "LFAKE001",
    status: str | None = None,
) -> Timecard:
    """テスト用タイムカード。breaks要素: (開始, 終了[, 開始+日, 終了+日, is_paid])。"""
    parsed_breaks = []
    for spec in breaks:
        b_start, b_end = spec[0], spec[1]
        sd = spec[2] if len(spec) > 2 else 0
        ed = spec[3] if len(spec) > 3 else sd
        is_paid = spec[4] if len(spec) > 4 else False
        parsed_breaks.append(
            Break(
                start_at=jst(day, b_start, sd),
                end_at=jst(day, b_end, ed) if b_end else None,
                is_paid=is_paid,
            )
        )
    end_at = jst(day, end, end_plus_days) if end else None
    return Timecard(
        id=f"TC{next(_seq)}",
        team_member_id=member,
        location_id=location,
        start_at=jst(day, start),
        end_at=end_at,
        breaks=parsed_breaks,
        status=status or ("CLOSED" if end_at else "OPEN"),
    )


def make_member(member_id: str, name: str, code: str | None = None) -> TeamMember:
    return TeamMember(id=member_id, display_name=name, reference_id=code)


def make_wage(member_id: str, yen: int | None) -> WageInfo:
    return WageInfo(team_member_id=member_id, hourly_rate_yen=Decimal(yen) if yen is not None else None)
