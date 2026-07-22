"""勤怠分類アルゴリズム(T3、設計書4.2)。

タイムカード(JST変換済み)を 所定内 / 時間外 / 深夜 / 法定休日 に分類する。

方針:
- 暦日を跨ぐ継続勤務は「始業日の勤務」として扱う(労基法解釈に準拠)
- 深夜は加算割増のため所定内/時間外と重複してよい。法定休日は時間外と排他
- 週40時間超の判定では日次で時間外計上済みの時間を除外し二重計上を防ぐ
- 迷ったら会社不利・労働者有利側に倒す(休憩の打刻不備は控除しない等)
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Iterable

from .models import (
    AggregationError,
    AggregationResult,
    DailyWork,
    MonthlySummary,
    Timecard,
)
from .rules import Rules, period_range
from .timeutil import ceil_minutes

# --- 労基法上の法定値。会社の給与規程(config)ではなく法律の定数のためここに置く ---
DAILY_LEGAL_MINUTES = 8 * 60  # 労基法32条: 1日8時間
WEEKLY_LEGAL_MINUTES = 40 * 60  # 労基法32条: 週40時間
MONTHLY_OVER60_MINUTES = 60 * 60  # 労基法37条: 月60時間超の時間外は割増率引き上げ

_WEEKDAY_INDEX = {name: i for i, name in enumerate(("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"))}

Interval = tuple[datetime, datetime]


# ---------------------------------------------------------------------------
# 休憩控除
# ---------------------------------------------------------------------------


def _subtract_interval(intervals: list[Interval], cut: Interval) -> list[Interval]:
    """区間リストから cut 区間を差し引く。"""
    cut_start, cut_end = cut
    result: list[Interval] = []
    for start, end in intervals:
        if cut_end <= start or cut_start >= end:
            result.append((start, end))
            continue
        if cut_start > start:
            result.append((start, cut_start))
        if cut_end < end:
            result.append((cut_end, end))
    return result


def work_intervals(timecard: Timecard) -> tuple[list[Interval], list[str]]:
    """実労働区間(無給休憩を差し引いた区間)と警告を返す。

    休憩の打刻不備(終了欠落・勤務範囲外・逆転)は控除せず警告に回す
    (過大計上側に倒す=会社不利・労働者有利。設計書6)。有給休憩は労働時間扱い。
    """
    warnings: list[str] = []
    intervals: list[Interval] = [(timecard.start_at, timecard.end_at)]
    for br in timecard.breaks:
        if br.end_at is None:
            warnings.append(
                f"休憩終了打刻の欠落({br.start_at:%H:%M}〜)のため控除せず集計(労働者有利側)"
            )
            continue
        if br.is_paid:
            continue  # 有給休憩は労働時間扱い(控除しない)
        out_of_range = (
            br.end_at <= br.start_at
            or br.start_at < timecard.start_at
            or br.end_at > timecard.end_at
        )
        if out_of_range:
            warnings.append(
                f"休憩({br.start_at:%H:%M}-{br.end_at:%H:%M})が勤務時間の範囲外のため控除せず集計(労働者有利側)"
            )
            continue
        intervals = _subtract_interval(intervals, (br.start_at, br.end_at))
    return intervals, warnings


def deduct_breaks(timecard: Timecard) -> tuple[int, list[str]]:
    """休憩控除後の実労働分数(端数秒切り上げ)と警告を返す。"""
    intervals, warnings = work_intervals(timecard)
    seconds = sum((end - start).total_seconds() for start, end in intervals)
    return ceil_minutes(seconds), warnings


# ---------------------------------------------------------------------------
# 深夜・休日・残業の各判定
# ---------------------------------------------------------------------------


def night_overlap_seconds(start: datetime, end: datetime, rules: Rules) -> float:
    """単一区間と深夜帯(既定22:00-翌05:00、日跨ぎ対応)の重なり秒数。"""
    tz = start.tzinfo
    total = 0.0
    day = start.date() - timedelta(days=1)  # 前日窓の早朝側(00:00-05:00)を拾う
    while day <= end.date():
        window_start = datetime.combine(day, rules.night_start, tzinfo=tz)
        if rules.night_end <= rules.night_start:  # 日跨ぎ(22:00→翌05:00)
            window_end = datetime.combine(day + timedelta(days=1), rules.night_end, tzinfo=tz)
        else:
            window_end = datetime.combine(day, rules.night_end, tzinfo=tz)
        overlap_start = max(start, window_start)
        overlap_end = min(end, window_end)
        if overlap_end > overlap_start:
            total += (overlap_end - overlap_start).total_seconds()
        day += timedelta(days=1)
    return total


def calc_night_overlap(start: datetime, end: datetime, rules: Rules) -> int:
    """単一区間の深夜重なり分数(日跨ぎ対応)。"""
    return ceil_minutes(night_overlap_seconds(start, end, rules))


def classify_holiday(work_date: date, rules: Rules) -> bool:
    """法定休日か。現状は固定曜日方式(weekly_one)のみ(TBD-5)。"""
    return work_date.weekday() == _WEEKDAY_INDEX[rules.legal_holiday_day]


def split_daily_overtime(daily_minutes: int) -> tuple[int, int]:
    """日次の実労働を(所定内, 時間外=8h超)に分割する。"""
    regular = min(daily_minutes, DAILY_LEGAL_MINUTES)
    return regular, daily_minutes - regular


def week_start_of(work_date: date, rules: Rules) -> date:
    """設定の週起算日(TBD-4)に基づく週の開始日。"""
    offset = (work_date.weekday() - _WEEKDAY_INDEX[rules.workweek_start_day]) % 7
    return work_date - timedelta(days=offset)


def calc_weekly_overtime(week_records: list[DailyWork]) -> int:
    """同一人物・同一週の記録に週40時間超の残業を追加計上する(破壊的更新)。

    日次で時間外計上済みの時間(overtime_minutes)は算定基礎に含めず、
    法定休日労働も除外することで二重計上を防ぐ。所定内の累計が週40hを
    超えた分だけを、超えた日の所定内から時間外へ振り替える。
    戻り値は追加した時間外の合計分数。
    """
    added = 0
    cumulative = 0
    for record in sorted(week_records, key=lambda r: r.work_date):
        if record.holiday_minutes:
            continue  # 法定休日労働は週40hの算定から除外(割増1.35で別計上)
        room = max(WEEKLY_LEGAL_MINUTES - cumulative, 0)
        within = min(record.regular_minutes, room)
        excess = record.regular_minutes - within
        cumulative += within
        if excess > 0:
            record.regular_minutes -= excess
            record.overtime_minutes += excess
            added += excess
    return added


def apply_rounding(minutes: int, rules: Rules) -> int:
    """カテゴリ合計への丸め。method=up(労働者有利の切り上げ)のみ許可。"""
    unit = rules.rounding_unit_minutes
    if rules.rounding_method == "none" or unit <= 1 or minutes <= 0:
        return minutes
    return math.ceil(minutes / unit) * unit


def apply_over_60h(monthly_overtime_minutes: int) -> tuple[int, int]:
    """月間時間外を(60h以内, 60h超過分)に分割する。"""
    over = max(monthly_overtime_minutes - MONTHLY_OVER60_MINUTES, 0)
    return monthly_overtime_minutes - over, over


# ---------------------------------------------------------------------------
# メイン集計
# ---------------------------------------------------------------------------


def _validate_timecards(
    timecards: Iterable[Timecard],
) -> tuple[list[Timecard], list[AggregationError]]:
    """OPEN・不正・重複タイムカードを除外しエラーを返す(設計書6)。"""
    errors: list[AggregationError] = []
    valid: list[Timecard] = []
    for tc in timecards:
        work_date = tc.start_at.date()
        if tc.end_at is None or tc.status.upper() == "OPEN":
            errors.append(
                AggregationError(
                    kind="open_timecard",
                    team_member_id=tc.team_member_id,
                    work_date=work_date,
                    message=f"退勤打刻漏れ(OPEN)のため集計から除外(出勤 {tc.start_at:%H:%M})",
                )
            )
            continue
        if tc.end_at <= tc.start_at:
            errors.append(
                AggregationError(
                    kind="invalid_timecard",
                    team_member_id=tc.team_member_id,
                    work_date=work_date,
                    message=f"終業({tc.end_at:%m/%d %H:%M})が始業({tc.start_at:%m/%d %H:%M})以前のため除外",
                )
            )
            continue
        valid.append(tc)

    # 同一人物のタイムカード重複・時間重なり → 該当者のみ除外して続行
    by_member: dict[str, list[Timecard]] = defaultdict(list)
    for tc in valid:
        by_member[tc.team_member_id].append(tc)
    excluded: set[str] = set()
    for member_id, cards in by_member.items():
        cards.sort(key=lambda c: c.start_at)
        for prev, nxt in zip(cards, cards[1:]):
            if nxt.start_at < prev.end_at:
                excluded.add(member_id)
                errors.append(
                    AggregationError(
                        kind="overlap",
                        team_member_id=member_id,
                        work_date=prev.start_at.date(),
                        message=(
                            f"タイムカードの時間帯が重複"
                            f"({prev.start_at:%m/%d %H:%M}-{prev.end_at:%H:%M} と {nxt.start_at:%m/%d %H:%M}〜)。"
                            "当人の集計を停止(要修正後に再集計)"
                        ),
                    )
                )
                break

    return [tc for tc in valid if tc.team_member_id not in excluded], errors


def aggregate(timecards: Iterable[Timecard], rules: Rules, period: str) -> AggregationResult:
    """タイムカード一式を月次集計する(設計書4.2の手順1〜8)。"""
    period_start, period_end = period_range(period, rules)
    valid, errors = _validate_timecards(timecards)

    # 手順1-2: 日次勤務の確定(始業日基準、同日複数打刻は合算)+ 休憩控除
    day_acc: dict[tuple[str, date], dict] = {}
    for tc in valid:
        intervals, warnings = work_intervals(tc)
        worked_seconds = sum((end - start).total_seconds() for start, end in intervals)
        night_seconds = sum(night_overlap_seconds(start, end, rules) for start, end in intervals)
        key = (tc.team_member_id, tc.start_at.date())
        acc = day_acc.setdefault(
            key, {"seconds": 0.0, "night_seconds": 0.0, "location_id": tc.location_id, "warnings": []}
        )
        acc["seconds"] += worked_seconds
        acc["night_seconds"] += night_seconds
        acc["warnings"].extend(warnings)

    # 手順3・5・6: 日8h超の時間外 / 深夜 / 法定休日(時間外と排他)
    daily_works: list[DailyWork] = []
    for (member_id, work_date), acc in sorted(day_acc.items()):
        total_minutes = ceil_minutes(acc["seconds"])
        night_minutes = min(ceil_minutes(acc["night_seconds"]), total_minutes)
        if classify_holiday(work_date, rules):
            regular, overtime, holiday = 0, 0, total_minutes
        else:
            regular, overtime = split_daily_overtime(total_minutes)
            holiday = 0
        daily_works.append(
            DailyWork(
                team_member_id=member_id,
                work_date=work_date,
                location_id=acc["location_id"],
                total_minutes=total_minutes,
                regular_minutes=regular,
                overtime_minutes=overtime,
                night_minutes=night_minutes,
                holiday_minutes=holiday,
                warnings=acc["warnings"],
            )
        )

    # 手順4: 週40h超の時間外(週起算日基準、日次計上分は二重計上しない)
    weeks: dict[tuple[str, date], list[DailyWork]] = defaultdict(list)
    for dw in daily_works:
        weeks[(dw.team_member_id, week_start_of(dw.work_date, rules))].append(dw)
    for records in weeks.values():
        calc_weekly_overtime(records)

    # 締め期間内のみを月次集計へ(始業日基準。A17: 月末夜勤は始業月に計上)
    in_period = [dw for dw in daily_works if period_start <= dw.work_date <= period_end]

    # 手順7-8: 月60h超の分割と丸め(丸めはカテゴリ合計へ適用)
    summaries: list[MonthlySummary] = []
    for member_id in sorted({dw.team_member_id for dw in in_period}):
        rows = [dw for dw in in_period if dw.team_member_id == member_id]
        regular = apply_rounding(sum(r.regular_minutes for r in rows), rules)
        overtime_total = apply_rounding(sum(r.overtime_minutes for r in rows), rules)
        night = apply_rounding(sum(r.night_minutes for r in rows), rules)
        holiday = apply_rounding(sum(r.holiday_minutes for r in rows), rules)
        normal_overtime, over60 = apply_over_60h(overtime_total)
        holiday_night = sum(r.night_minutes for r in rows if r.holiday_minutes)

        warnings: list[str] = []
        for r in rows:
            for w in r.warnings:
                text = f"{r.work_date}: {w}"
                if text not in warnings:
                    warnings.append(text)

        summaries.append(
            MonthlySummary(
                team_member_id=member_id,
                period=period,
                days_worked=len(rows),
                location_ids=sorted({r.location_id for r in rows}),
                total_minutes=regular + normal_overtime + over60 + holiday,
                regular_minutes=regular,
                overtime_minutes=normal_overtime,
                overtime_over60_minutes=over60,
                night_minutes=night,
                holiday_minutes=holiday,
                holiday_night_minutes=holiday_night,
                warnings=warnings,
            )
        )

    return AggregationResult(period=period, daily_works=in_period, summaries=summaries, errors=errors)
