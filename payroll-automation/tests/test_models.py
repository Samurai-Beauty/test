"""T2: タイムゾーン変換・モデル変換のテスト。

fixtureはすべてダミーデータ(実在の従業員名・実データは使用しない)。
"""

from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from payroll.models import parse_team_member, parse_timecard, parse_wages
from payroll.timeutil import JST, ceil_minutes, format_hhmm, parse_rfc3339, to_zone


class TestParseRfc3339:
    def test_utc_z_suffix(self):
        dt = parse_rfc3339("2026-07-01T13:00:00Z")
        assert dt == datetime(2026, 7, 1, 13, 0, tzinfo=timezone.utc)

    def test_offset_input(self):
        dt = parse_rfc3339("2026-07-01T22:00:00+09:00")
        assert dt.utcoffset().total_seconds() == 9 * 3600

    def test_naive_rejected(self):
        with pytest.raises(ValueError):
            parse_rfc3339("2026-07-01T22:00:00")

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            parse_rfc3339("")


class TestToZone:
    def test_utc_to_jst(self):
        # A16の前提: 2026-07-01T13:00:00Z は JST 22:00
        dt = to_zone("2026-07-01T13:00:00Z", JST)
        assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 7, 1, 22, 0)

    def test_offset_to_jst(self):
        dt = to_zone("2026-07-01T22:00:00+09:00", JST)
        assert dt.hour == 22
        assert dt.tzinfo == JST

    def test_utc_midnight_crossing(self):
        # UTC 15:30 = JST 翌0:30(日付が進む)
        dt = to_zone("2026-06-30T15:30:00Z", JST)
        assert (dt.month, dt.day, dt.hour, dt.minute) == (7, 1, 0, 30)


class TestHelpers:
    def test_ceil_minutes(self):
        assert ceil_minutes(0) == 0
        assert ceil_minutes(-30) == 0
        assert ceil_minutes(60) == 1
        assert ceil_minutes(61) == 2  # 端数秒は労働者有利側へ切り上げ

    def test_format_hhmm(self):
        assert format_hhmm(0) == "0:00"
        assert format_hhmm(510) == "8:30"
        assert format_hhmm(65) == "1:05"


class TestParseTimecard:
    def test_new_api_keys(self):
        tc = parse_timecard(
            {
                "id": "TC1",
                "team_member_id": "TM_DUMMY_1",
                "location_id": "LFAKE001",
                "start_at": "2026-07-06T00:00:00Z",
                "end_at": "2026-07-06T09:00:00Z",
                "breaks": [
                    {"start_at": "2026-07-06T03:00:00Z", "end_at": "2026-07-06T04:00:00Z", "is_paid": False}
                ],
                "status": "CLOSED",
            }
        )
        assert tc.team_member_id == "TM_DUMMY_1"
        assert tc.start_at.hour == 9  # JST変換済み
        assert tc.end_at.hour == 18
        assert len(tc.breaks) == 1 and tc.breaks[0].start_at.hour == 12

    def test_legacy_shift_keys(self):
        tc = parse_timecard(
            {
                "id": "TC2",
                "employee_id": "TM_DUMMY_2",
                "location_id": "LFAKE001",
                "start_at": "2026-07-06T09:00:00+09:00",
                "break_entries": [{"start_at": "2026-07-06T12:00:00+09:00", "end_at": None}],
            }
        )
        assert tc.team_member_id == "TM_DUMMY_2"
        assert tc.end_at is None
        assert tc.status == "OPEN"
        assert tc.breaks[0].end_at is None

    def test_custom_timezone(self):
        tc = parse_timecard(
            {
                "id": "TC3",
                "team_member_id": "TM_DUMMY_3",
                "location_id": "LFAKE001",
                "start_at": "2026-07-06T00:00:00Z",
                "end_at": "2026-07-06T09:00:00Z",
            },
            tz=ZoneInfo("UTC"),
        )
        assert tc.start_at.hour == 0


class TestParseTeamMember:
    def test_name_and_code(self):
        m = parse_team_member(
            {"id": "TM_DUMMY_1", "family_name": "試験", "given_name": "太郎", "reference_id": "EMP001"}
        )
        assert m.display_name == "試験 太郎"
        assert m.employee_code == "EMP001"

    def test_fallback_to_id(self):
        m = parse_team_member({"id": "TM_DUMMY_9"})
        assert m.display_name == "TM_DUMMY_9"
        assert m.employee_code == "TM_DUMMY_9"


class TestParseWages:
    def test_jpy_amount_is_yen(self):
        wages = parse_wages(
            [{"team_member_id": "TM_DUMMY_1", "title": "スタイリスト", "hourly_rate": {"amount": 1200, "currency": "JPY"}}]
        )
        assert wages["TM_DUMMY_1"].hourly_rate_yen == Decimal(1200)

    def test_multiple_rates_take_max_with_warning(self):
        wages = parse_wages(
            [
                {"team_member_id": "TM_DUMMY_1", "hourly_rate": {"amount": 1200, "currency": "JPY"}},
                {"team_member_id": "TM_DUMMY_1", "hourly_rate": {"amount": 1500, "currency": "JPY"}},
            ]
        )
        info = wages["TM_DUMMY_1"]
        assert info.hourly_rate_yen == Decimal(1500)  # 労働者有利側=高い方
        assert info.warnings

    def test_missing_rate(self):
        wages = parse_wages([{"team_member_id": "TM_DUMMY_2"}])
        assert wages["TM_DUMMY_2"].hourly_rate_yen is None
