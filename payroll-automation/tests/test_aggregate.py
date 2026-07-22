"""T3: aggregate.py のテスト(構築書4.1 A1〜A17 全ケース + 境界ケース)。

fixtureはすべてダミーデータ。基準週: 2026-07-06(月)〜2026-07-12(日)。
既定ルール: 月末締め / 週起算MON / 法定休日SUN / 深夜22:00-05:00 / 丸めなし。
"""

from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from payroll.aggregate import (
    DAILY_LEGAL_MINUTES,
    MONTHLY_OVER60_MINUTES,
    WEEKLY_LEGAL_MINUTES,
    aggregate,
    apply_over_60h,
    apply_rounding,
    calc_night_overlap,
    calc_weekly_overtime,
    classify_holiday,
    deduct_breaks,
    split_daily_overtime,
    week_start_of,
)
from payroll.models import Break, Timecard, parse_timecard
from payroll.rules import load_rules
from payroll.timeutil import JST

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
RULES = load_rules(CONFIG_DIR / "payroll_rules.yaml")

MON = date(2026, 7, 6)  # 月曜
SUN = date(2026, 7, 12)  # 日曜(既定ルールの法定休日)
assert MON.weekday() == 0 and SUN.weekday() == 6


def jst(day: date, hhmm: str, plus_days: int = 0) -> datetime:
    hh, mm = map(int, hhmm.split(":"))
    return datetime.combine(day + timedelta(days=plus_days), time(hh, mm), tzinfo=JST)


_seq = iter(range(10_000))


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


def summary_of(result, member: str):
    return next(s for s in result.summaries if s.team_member_id == member)


# ---------------------------------------------------------------------------
# A1〜A17(構築書4.1)
# ---------------------------------------------------------------------------


class TestSpecCases:
    def test_a1_regular_day(self):
        # 9:00-18:00 休憩60分 → 所定内480分、残業0
        result = aggregate([tc("M1", MON, "09:00", "18:00", [("12:00", "13:00")])], RULES, "2026-07")
        s = summary_of(result, "M1")
        assert (s.regular_minutes, s.overtime_minutes) == (480, 0)
        assert s.night_minutes == 0 and s.holiday_minutes == 0
        assert s.total_minutes == 480

    def test_a2_daily_overtime(self):
        # 9:00-20:00 休憩60分 → 所定内480、残業120
        result = aggregate([tc("M1", MON, "09:00", "20:00", [("12:00", "13:00")])], RULES, "2026-07")
        s = summary_of(result, "M1")
        assert (s.regular_minutes, s.overtime_minutes) == (480, 120)

    def test_a3_evening_shift_night_overlap(self):
        # 15:00-23:30 休憩30分(18:00-18:30)→ 深夜90分(22:00-23:30)を含む
        result = aggregate([tc("M1", MON, "15:00", "23:30", [("18:00", "18:30")])], RULES, "2026-07")
        s = summary_of(result, "M1")
        assert s.night_minutes == 90
        assert (s.regular_minutes, s.overtime_minutes) == (480, 0)

    def test_a4_overnight_shift(self):
        # 21:00-翌6:00 休憩60分(翌1:00-2:00)→ 始業日の勤務として計上
        result = aggregate(
            [tc("M1", MON, "21:00", "06:00", [("01:00", "02:00", 1)], end_plus_days=1)],
            RULES,
            "2026-07",
        )
        s = summary_of(result, "M1")
        assert s.days_worked == 1
        daily = result.daily_works[0]
        assert daily.work_date == MON  # 始業日基準
        # 実労働8h、深夜=22:00-翌5:00のうち休憩(1:00-2:00)を除く実労働重なり=6h
        assert daily.total_minutes == 480
        assert daily.night_minutes == 360
        assert (s.regular_minutes, s.overtime_minutes) == (480, 0)

    def test_a5_multiple_breaks(self):
        # 休憩3回(合計90分)がすべて控除される: 9:00-19:30 実働9h
        result = aggregate(
            [
                tc(
                    "M1",
                    MON,
                    "09:00",
                    "19:30",
                    [("11:30", "12:00"), ("14:00", "14:30"), ("16:00", "16:30")],
                )
            ],
            RULES,
            "2026-07",
        )
        s = summary_of(result, "M1")
        assert s.total_minutes == 540
        assert (s.regular_minutes, s.overtime_minutes) == (480, 60)

    def test_a6_weekly_overtime(self):
        # 週6日×7h(日次残業なし)→ 週40h超の2hが残業になる
        cards = [
            tc("M1", MON + timedelta(days=i), "09:00", "16:00") for i in range(6)  # 月〜土
        ]
        result = aggregate(cards, RULES, "2026-07")
        s = summary_of(result, "M1")
        assert s.regular_minutes == WEEKLY_LEGAL_MINUTES  # 2400
        assert s.overtime_minutes == 120
        # 超過分は6日目(土)の所定内から振り替わる
        saturday = next(d for d in result.daily_works if d.work_date == MON + timedelta(days=5))
        assert (saturday.regular_minutes, saturday.overtime_minutes) == (300, 120)

    def test_a7_no_double_counting(self):
        # 週5日×9h → 日次残業5hのみ。週40h判定で二重計上しない(週次追加残業0)
        cards = [
            tc("M1", MON + timedelta(days=i), "09:00", "19:00", [("12:00", "13:00")])
            for i in range(5)  # 月〜金
        ]
        result = aggregate(cards, RULES, "2026-07")
        s = summary_of(result, "M1")
        assert s.regular_minutes == 2400
        assert s.overtime_minutes == 300  # 日次1h×5のみ。週次の上乗せなし

    def test_a8_legal_holiday_exclusive(self):
        # 法定休日に8h勤務 → holiday=480、overtime=0(排他)
        result = aggregate([tc("M1", SUN, "09:00", "18:00", [("12:00", "13:00")])], RULES, "2026-07")
        s = summary_of(result, "M1")
        assert s.holiday_minutes == 480
        assert s.overtime_minutes == 0 and s.regular_minutes == 0

    def test_a9_holiday_night(self):
        # 法定休日 20:00-24:00 → holiday=240、うち深夜120(割増1.60対象の内訳)
        result = aggregate([tc("M1", SUN, "20:00", "00:00", end_plus_days=1)], RULES, "2026-07")
        s = summary_of(result, "M1")
        assert s.holiday_minutes == 240
        assert s.night_minutes == 120
        assert s.holiday_night_minutes == 120  # 1.60対象の内訳
        assert s.overtime_minutes == 0

    def test_a10_monthly_over_60h(self):
        # 月間残業65h → normal_ot=60h、over60_ot=5h
        cards = []
        # 3週×5日(月〜金)の12h勤務(休憩1h、日次残業4h)= 残業60h
        for week in range(3):
            for dow in range(5):
                cards.append(
                    tc("M1", MON + timedelta(days=week * 7 + dow), "09:00", "22:00", [("12:00", "13:00")])
                )
        # 4週目: 12h×1日(残業4h)+ 10h×1日(残業1h)= 残業5h
        cards.append(tc("M1", MON + timedelta(days=21), "09:00", "22:00", [("12:00", "13:00")]))
        cards.append(tc("M1", MON + timedelta(days=22), "09:00", "19:00", [("12:00", "13:00")]))
        result = aggregate(cards, RULES, "2026-07")
        s = summary_of(result, "M1")
        assert s.overtime_minutes == MONTHLY_OVER60_MINUTES  # 3600 = 60h
        assert s.overtime_over60_minutes == 300  # 5h

    def test_a11_rounding_up_15min(self):
        # 丸め: unit=15/method=up、実働7h50m → 8h00m(労働者有利方向)
        rules = RULES.with_overrides(rounding_unit_minutes=15, rounding_method="up")
        result = aggregate([tc("M1", MON, "09:00", "16:50")], rules, "2026-07")
        s = summary_of(result, "M1")
        assert s.regular_minutes == 480

    def test_a12_rounding_none(self):
        # 丸め: method=none → 1分単位そのまま
        result = aggregate([tc("M1", MON, "09:00", "16:50")], RULES, "2026-07")
        s = summary_of(result, "M1")
        assert s.regular_minutes == 470

    def test_a13_open_timecard_excluded_and_reported(self):
        # end_at=None(OPEN)→ 集計除外+エラーリスト掲載。他日の有効打刻は集計される
        cards = [
            tc("M1", MON, "09:00", None),  # 退勤打刻漏れ
            tc("M1", MON + timedelta(days=1), "09:00", "18:00", [("12:00", "13:00")]),
        ]
        result = aggregate(cards, RULES, "2026-07")
        errors = [e for e in result.errors if e.kind == "open_timecard"]
        assert len(errors) == 1
        assert errors[0].team_member_id == "M1" and errors[0].work_date == MON
        s = summary_of(result, "M1")
        assert s.total_minutes == 480  # 有効な火曜分のみ

    def test_a14_break_out_of_range(self):
        # 休憩がstart_at前に開始 → 警告+休憩控除なしで集計
        result = aggregate(
            [tc("M1", MON, "09:00", "18:00", [("08:30", "09:30")])], RULES, "2026-07"
        )
        s = summary_of(result, "M1")
        assert s.total_minutes == 540  # 控除なし(9h)
        assert (s.regular_minutes, s.overtime_minutes) == (480, 60)
        assert any("範囲外" in w for w in s.warnings)

    def test_a15_overlap_excludes_member_only(self):
        # 同一人物の時間帯重複 → 該当者除外+エラー、他の従業員は正常集計
        cards = [
            tc("MX", MON, "09:00", "18:00"),
            tc("MX", MON, "17:00", "21:00"),  # 前のカードと重複
            tc("MY", MON, "09:00", "18:00", [("12:00", "13:00")]),
        ]
        result = aggregate(cards, RULES, "2026-07")
        overlap_errors = [e for e in result.errors if e.kind == "overlap"]
        assert len(overlap_errors) == 1 and overlap_errors[0].team_member_id == "MX"
        assert [s.team_member_id for s in result.summaries] == ["MY"]
        assert summary_of(result, "MY").regular_minutes == 480

    def test_a16_utc_input_night_judged_in_jst(self):
        # UTC入力(2026-07-01T13:00:00Z=JST22:00)→ 深夜判定がJST基準で正しい
        raw = {
            "id": "TC_UTC",
            "team_member_id": "M1",
            "location_id": "LFAKE001",
            "start_at": "2026-07-01T13:00:00Z",  # JST 7/1 22:00
            "end_at": "2026-07-01T15:00:00Z",  # JST 7/2 00:00
            "status": "CLOSED",
        }
        result = aggregate([parse_timecard(raw)], RULES, "2026-07")
        s = summary_of(result, "M1")
        assert s.night_minutes == 120
        daily = result.daily_works[0]
        assert daily.work_date == date(2026, 7, 1)

    def test_a17_month_end_overnight_belongs_to_start_month(self):
        # 月末締め: 6/30 22:00-7/1 6:00 の勤務 → 始業日基準で6月分に計上
        card = tc("M1", date(2026, 6, 30), "22:00", "06:00", end_plus_days=1)
        june = aggregate([card], RULES, "2026-06")
        s = summary_of(june, "M1")
        assert s.total_minutes == 480
        assert s.night_minutes == 420  # 22:00-翌5:00
        assert june.daily_works[0].work_date == date(2026, 6, 30)

        july = aggregate([tc("M1", date(2026, 6, 30), "22:00", "06:00", end_plus_days=1)], RULES, "2026-07")
        assert july.summaries == []  # 7月分には現れない


# ---------------------------------------------------------------------------
# 関数単体・境界ケース
# ---------------------------------------------------------------------------


class TestUnits:
    def test_split_daily_overtime(self):
        assert split_daily_overtime(480) == (480, 0)
        assert split_daily_overtime(600) == (480, 120)
        assert split_daily_overtime(0) == (0, 0)

    def test_apply_over_60h_boundary(self):
        assert apply_over_60h(MONTHLY_OVER60_MINUTES) == (3600, 0)  # ちょうど60hは通常割増
        assert apply_over_60h(3900) == (3600, 300)
        assert apply_over_60h(0) == (0, 0)

    def test_apply_rounding(self):
        rules_up = RULES.with_overrides(rounding_unit_minutes=15, rounding_method="up")
        assert apply_rounding(470, rules_up) == 480
        assert apply_rounding(480, rules_up) == 480  # 単位ぴったりは変わらない
        assert apply_rounding(470, RULES) == 470  # method=none
        assert apply_rounding(0, rules_up) == 0

    def test_calc_night_overlap_overnight(self):
        start = jst(MON, "21:00")
        end = jst(MON, "06:00", 1)
        assert calc_night_overlap(start, end, RULES) == 420  # 22:00-翌5:00

    def test_calc_night_overlap_early_morning(self):
        # 早朝勤務 4:00-9:00 → 深夜は4:00-5:00の60分(前日窓の早朝側)
        assert calc_night_overlap(jst(MON, "04:00"), jst(MON, "09:00"), RULES) == 60

    def test_calc_night_overlap_daytime_zero(self):
        assert calc_night_overlap(jst(MON, "09:00"), jst(MON, "18:00"), RULES) == 0

    def test_calc_night_overlap_non_crossing_window(self):
        # 深夜帯が日を跨がない設定(例 20:00-23:00)でも動作する
        rules = RULES.with_overrides(night_start=time(20, 0), night_end=time(23, 0))
        assert calc_night_overlap(jst(MON, "19:00"), jst(MON, "22:00"), rules) == 120

    def test_deduct_breaks_paid_break_not_deducted(self):
        # 有給休憩は労働時間扱い(控除しない)
        card = tc("M1", MON, "09:00", "18:00", [("12:00", "13:00", 0, 0, True)])
        minutes, warnings = deduct_breaks(card)
        assert minutes == 540
        assert warnings == []

    def test_deduct_breaks_missing_end(self):
        card = tc("M1", MON, "09:00", "18:00", [("12:00", None)])
        minutes, warnings = deduct_breaks(card)
        assert minutes == 540  # 控除せず(労働者有利側)
        assert any("欠落" in w for w in warnings)

    def test_week_start_of_respects_config(self):
        assert week_start_of(SUN, RULES) == MON  # 週起算MON: 日曜は同週
        rules_sun = RULES.with_overrides(workweek_start_day="SUN")
        assert week_start_of(SUN, rules_sun) == SUN

    def test_classify_holiday(self):
        assert classify_holiday(SUN, RULES) is True
        assert classify_holiday(MON, RULES) is False

    def test_calc_weekly_overtime_returns_added(self):
        works = aggregate(
            [tc("M1", MON + timedelta(days=i), "09:00", "16:00") for i in range(6)],
            RULES,
            "2026-07",
        ).daily_works
        # aggregate内で適用済みのため、追加なしを再確認
        assert calc_weekly_overtime(list(works)) == 0

    def test_invalid_timecard_end_before_start(self):
        card = tc("M1", MON, "18:00", "09:00")  # 終業が始業以前
        result = aggregate([card], RULES, "2026-07")
        assert [e.kind for e in result.errors] == ["invalid_timecard"]
        assert result.summaries == []

    def test_split_shift_same_day_merged(self):
        # 同日2枚(中抜け)は合算して日8h超を判定する
        cards = [
            tc("M1", MON, "09:00", "14:00"),
            tc("M1", MON, "16:00", "21:00"),  # 合計10h
        ]
        result = aggregate(cards, RULES, "2026-07")
        s = summary_of(result, "M1")
        assert s.days_worked == 1
        assert (s.regular_minutes, s.overtime_minutes) == (480, 120)

    def test_empty_input(self):
        result = aggregate([], RULES, "2026-07")
        assert result.summaries == [] and result.errors == []

    def test_multi_location_summary(self):
        cards = [
            tc("M1", MON, "09:00", "13:00", location="LFAKE001"),
            tc("M1", MON + timedelta(days=1), "09:00", "13:00", location="LFAKE002"),
        ]
        result = aggregate(cards, RULES, "2026-07")
        s = summary_of(result, "M1")
        assert s.location_ids == ["LFAKE001", "LFAKE002"]
        assert s.days_worked == 2
