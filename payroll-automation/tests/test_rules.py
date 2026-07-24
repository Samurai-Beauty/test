"""T1: rules.py のテスト(config読込・バリデーション・期間計算)。"""

from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from payroll.rules import ConfigError, Rules, load_locations, load_rules, period_range

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


@pytest.fixture()
def default_rules() -> Rules:
    return load_rules(CONFIG_DIR / "payroll_rules.yaml")


def _write_rules(tmp_path: Path, mutate) -> Path:
    """既定configを読み込み、mutate(dict)で改変したYAMLを書き出す。"""
    with (CONFIG_DIR / "payroll_rules.yaml").open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    mutate(data)
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


class TestLoadRules:
    def test_default_config_loads(self, default_rules: Rules):
        assert default_rules.closing_day == 99
        assert default_rules.timezone == "Asia/Tokyo"
        assert default_rules.rounding_unit_minutes == 1
        assert default_rules.rounding_method == "none"
        assert default_rules.workweek_start_day == "MON"
        assert default_rules.legal_holiday_day == "SUN"
        assert default_rules.night_start == time(22, 0)
        assert default_rules.night_end == time(5, 0)

    def test_premium_rates_are_exact_decimals(self, default_rules: Rules):
        # float経由の誤差が出ないこと(金額計算はDecimal縛り)
        assert default_rules.premium_overtime == Decimal("1.25")
        assert default_rules.premium_overtime_over_60h == Decimal("1.50")
        assert default_rules.premium_late_night == Decimal("0.25")
        assert default_rules.premium_legal_holiday == Decimal("1.35")

    def test_unit_minutes_below_1_rejected(self, tmp_path):
        path = _write_rules(tmp_path, lambda d: d["rounding"].__setitem__("unit_minutes", 0))
        with pytest.raises(ConfigError, match="unit_minutes"):
            load_rules(path)

    def test_rounding_down_rejected(self, tmp_path):
        # 切り捨ては労働者不利のため設定自体を拒否する
        path = _write_rules(tmp_path, lambda d: d["rounding"].__setitem__("method", "down"))
        with pytest.raises(ConfigError, match="method"):
            load_rules(path)

    def test_missing_required_key_rejected(self, tmp_path):
        path = _write_rules(tmp_path, lambda d: d.pop("premium_rates"))
        with pytest.raises(ConfigError, match="premium_rates"):
            load_rules(path)

    def test_invalid_weekday_rejected(self, tmp_path):
        path = _write_rules(tmp_path, lambda d: d["workweek"].__setitem__("start_day", "MONDAY?"))
        with pytest.raises(ConfigError, match="start_day"):
            load_rules(path)

    def test_invalid_night_format_rejected(self, tmp_path):
        path = _write_rules(tmp_path, lambda d: d["night_hours"].__setitem__("start", "22時"))
        with pytest.raises(ConfigError, match="night_hours.start"):
            load_rules(path)

    def test_invalid_closing_day_rejected(self, tmp_path):
        path = _write_rules(tmp_path, lambda d: d["period"].__setitem__("closing_day", 32))
        with pytest.raises(ConfigError, match="closing_day"):
            load_rules(path)

    def test_missing_file_rejected(self, tmp_path):
        with pytest.raises(ConfigError):
            load_rules(tmp_path / "nothing.yaml")


class TestLocations:
    def test_default_locations_load(self):
        locations = load_locations(CONFIG_DIR / "locations.yaml")
        assert len(locations) == 3
        assert locations["LAC6K4H5FG5RV"] == "西新宿本店"
        assert locations["L62PANFASQNVH"] == "新宿三丁目"
        assert locations["L95B23P42GDEJ"] == "渋谷東"

    def test_empty_locations_rejected(self, tmp_path):
        path = tmp_path / "locations.yaml"
        path.write_text("locations: {}\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_locations(path)


class TestPeriodRange:
    def test_month_end_closing(self, default_rules: Rules):
        assert period_range("2026-07", default_rules) == (date(2026, 7, 1), date(2026, 7, 31))
        assert period_range("2026-02", default_rules) == (date(2026, 2, 1), date(2026, 2, 28))

    def test_mid_month_closing(self, default_rules: Rules):
        rules = default_rules.with_overrides(closing_day=15)
        assert period_range("2026-07", rules) == (date(2026, 6, 16), date(2026, 7, 15))
        # 1月分は前年12月16日から
        assert period_range("2026-01", rules) == (date(2025, 12, 16), date(2026, 1, 15))

    def test_closing_day_clamped_to_month_length(self, default_rules: Rules):
        rules = default_rules.with_overrides(closing_day=31)
        # 2月は28日締めに丸める
        assert period_range("2026-02", rules) == (date(2026, 2, 1), date(2026, 2, 28))

    def test_invalid_period_format_rejected(self, default_rules: Rules):
        with pytest.raises(ConfigError):
            period_range("202607", default_rules)
        with pytest.raises(ConfigError):
            period_range("2026-13", default_rules)
