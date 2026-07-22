"""給与計算ルール(config/payroll_rules.yaml)の読込とバリデーション。

時給・割増率・締め日などの値は一切ハードコードせず、すべて本モジュール経由で
configから取得する。労基法上の法定値(日8h・週40h・月60h)のみ aggregate.py に
定数として持つ。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import calendar
import yaml


class ConfigError(Exception):
    """config記述の不備(必須キー欠落・不正値)。"""


WEEKDAY_NAMES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")

# 締め日 99 は「月末」を意味する特殊値(TBD-2)
CLOSING_DAY_MONTH_END = 99

ROUNDING_METHODS = ("none", "up")  # 切り捨て(down)は労働者不利のため許可しない

LEGAL_HOLIDAY_MODES = ("weekly_one",)  # 現状は固定曜日方式のみ(TBD-5)


@dataclass(frozen=True)
class Rules:
    closing_day: int
    payment_day: int
    timezone: str
    rounding_unit_minutes: int
    rounding_method: str  # none | up
    workweek_start_day: str  # MON..SUN
    legal_holiday_mode: str  # weekly_one
    legal_holiday_day: str  # MON..SUN
    premium_overtime: Decimal
    premium_overtime_over_60h: Decimal
    premium_late_night: Decimal
    premium_legal_holiday: Decimal
    night_start: time
    night_end: time

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def with_overrides(self, **kwargs) -> "Rules":
        """テスト・シミュレーション用に一部の値を差し替えたコピーを返す。"""
        return replace(self, **kwargs)


def _require(data: dict, path: str):
    node = data
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            raise ConfigError(f"payroll_rules.yaml: 必須キー '{path}' がありません")
        node = node[key]
    return node


def _as_decimal(value, path: str) -> Decimal:
    try:
        # YAMLのfloat(例 1.25)を経由しても誤差が出ないよう str を挟む
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ConfigError(f"payroll_rules.yaml: '{path}' が数値ではありません: {value!r}") from exc


def _as_time(value, path: str) -> time:
    try:
        hh, mm = str(value).split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError) as exc:
        raise ConfigError(f"payroll_rules.yaml: '{path}' は HH:MM 形式で指定してください: {value!r}") from exc


def _as_weekday(value, path: str) -> str:
    day = str(value).upper()
    if day not in WEEKDAY_NAMES:
        raise ConfigError(f"payroll_rules.yaml: '{path}' は {'/'.join(WEEKDAY_NAMES)} のいずれか: {value!r}")
    return day


def load_rules(path: str | Path) -> Rules:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"ルール設定ファイルがありません: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ConfigError(f"payroll_rules.yaml の形式が不正です: {path}")

    closing_day = _require(data, "period.closing_day")
    payment_day = _require(data, "period.payment_day")
    if not isinstance(closing_day, int) or not (
        1 <= closing_day <= 31 or closing_day == CLOSING_DAY_MONTH_END
    ):
        raise ConfigError(f"payroll_rules.yaml: 'period.closing_day' は 1-31 または 99(月末): {closing_day!r}")

    timezone = str(_require(data, "timezone"))
    try:
        ZoneInfo(timezone)
    except Exception as exc:
        raise ConfigError(f"payroll_rules.yaml: 'timezone' が不正です: {timezone!r}") from exc

    unit_minutes = _require(data, "rounding.unit_minutes")
    if not isinstance(unit_minutes, int) or unit_minutes < 1:
        raise ConfigError(f"payroll_rules.yaml: 'rounding.unit_minutes' は1以上の整数: {unit_minutes!r}")

    method = str(_require(data, "rounding.method"))
    if method not in ROUNDING_METHODS:
        raise ConfigError(
            f"payroll_rules.yaml: 'rounding.method' は {'/'.join(ROUNDING_METHODS)} のいずれか"
            f"(切り捨ては労働者不利のため不可): {method!r}"
        )

    holiday_mode = str(_require(data, "legal_holiday.mode"))
    if holiday_mode not in LEGAL_HOLIDAY_MODES:
        raise ConfigError(f"payroll_rules.yaml: 'legal_holiday.mode' は {'/'.join(LEGAL_HOLIDAY_MODES)} のみ対応: {holiday_mode!r}")

    night_start = _as_time(_require(data, "night_hours.start"), "night_hours.start")
    night_end = _as_time(_require(data, "night_hours.end"), "night_hours.end")

    return Rules(
        closing_day=closing_day,
        payment_day=int(payment_day) if isinstance(payment_day, int) else CLOSING_DAY_MONTH_END,
        timezone=timezone,
        rounding_unit_minutes=unit_minutes,
        rounding_method=method,
        workweek_start_day=_as_weekday(_require(data, "workweek.start_day"), "workweek.start_day"),
        legal_holiday_mode=holiday_mode,
        legal_holiday_day=_as_weekday(_require(data, "legal_holiday.day"), "legal_holiday.day"),
        premium_overtime=_as_decimal(_require(data, "premium_rates.overtime"), "premium_rates.overtime"),
        premium_overtime_over_60h=_as_decimal(
            _require(data, "premium_rates.overtime_over_60h"), "premium_rates.overtime_over_60h"
        ),
        premium_late_night=_as_decimal(_require(data, "premium_rates.late_night"), "premium_rates.late_night"),
        premium_legal_holiday=_as_decimal(
            _require(data, "premium_rates.legal_holiday"), "premium_rates.legal_holiday"
        ),
        night_start=night_start,
        night_end=night_end,
    )


def load_locations(path: str | Path) -> dict[str, str]:
    """config/locations.yaml → {location_id: 店舗名}。"""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"店舗設定ファイルがありません: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    locations = (data or {}).get("locations")
    if not isinstance(locations, dict) or not locations:
        raise ConfigError(f"locations.yaml: 'locations' に location_id: 店舗名 を1件以上定義してください")
    return {str(k): str(v) for k, v in locations.items()}


def period_range(period: str, rules: Rules) -> tuple[date, date]:
    """給与期間 'YYYY-MM' → (開始日, 終了日) を締め日設定から求める。

    closing_day=99(月末)なら暦月そのもの。closing_day=15 なら
    前月16日〜当月15日(月にその日が無ければ月末に丸める)。
    """
    try:
        year_s, month_s = period.split("-")
        year, month = int(year_s), int(month_s)
        if not 1 <= month <= 12:
            raise ValueError
    except ValueError as exc:
        raise ConfigError(f"期間は 'YYYY-MM' 形式で指定してください: {period!r}") from exc

    if rules.closing_day == CLOSING_DAY_MONTH_END:
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
        return start, end

    closing = min(rules.closing_day, calendar.monthrange(year, month)[1])
    end = date(year, month, closing)
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    prev_closing = min(rules.closing_day, calendar.monthrange(prev_year, prev_month)[1])
    start_anchor = date(prev_year, prev_month, prev_closing)
    from datetime import timedelta

    return start_anchor + timedelta(days=1), end
