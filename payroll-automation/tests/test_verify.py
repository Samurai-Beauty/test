"""T6: verify.py のテスト(構築書4.2 V1〜V4)。

金額はすべて手計算のダミー値(実在の従業員・実データは使用しない)。
- EMPA: 時給1,000円 × 所定内8h = 8,000円
- EMPB: 時給1,200円 × (所定内8h + 残業1h×1.25) = 9,600 + 1,500 = 11,100円
"""

from decimal import Decimal
from pathlib import Path

import pytest

from payroll.models import MonthlySummary
from payroll.rules import ConfigError, load_rules
from payroll.verify import (
    calc_expected_gross,
    read_mf_result_csv,
    render_verify_report,
    verify,
)

from tests.helpers import make_member, make_wage

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
RULES = load_rules(CONFIG_DIR / "payroll_rules.yaml")

MEMBERS = {
    "TMA": make_member("TMA", "試験 太郎", "EMPA"),
    "TMB": make_member("TMB", "試験 花子", "EMPB"),
}
WAGES = {"TMA": make_wage("TMA", 1000), "TMB": make_wage("TMB", 1200)}


def make_summary(member_id, regular=0, overtime=0, over60=0, night=0, holiday=0) -> MonthlySummary:
    return MonthlySummary(
        team_member_id=member_id,
        period="2026-07",
        days_worked=1,
        location_ids=["LFAKE001"],
        total_minutes=regular + overtime + over60 + holiday,
        regular_minutes=regular,
        overtime_minutes=overtime,
        overtime_over60_minutes=over60,
        night_minutes=night,
        holiday_minutes=holiday,
        holiday_night_minutes=min(night, holiday),
    )


SUMMARIES = [
    make_summary("TMA", regular=480),
    make_summary("TMB", regular=480, overtime=60),
]


class TestCalcExpectedGross:
    def test_v4_hourly_1200_overtime_2h(self):
        # 時給1,200円×残業2h → 1200×2×1.25 = 3,000円(Decimal検証)
        summary = make_summary("TMB", overtime=120)
        assert calc_expected_gross(summary, Decimal(1200), RULES) == Decimal("3000")

    def test_holiday_night_combination(self):
        # 休日4h(1.35)+うち深夜2h(+0.25)= 5,400 + 500 = 5,900円(実質1.60)
        summary = make_summary("TMA", holiday=240, night=120)
        assert calc_expected_gross(summary, Decimal(1000), RULES) == Decimal("5900")

    def test_over60_premium(self):
        # 60h超残業2h: 1000×2×1.5 = 3,000円
        summary = make_summary("TMA", over60=120)
        assert calc_expected_gross(summary, Decimal(1000), RULES) == Decimal("3000")

    def test_yen_rounding_half_up(self):
        # 時給1,015円×所定内50分 = 845.83…円 → 846円(四捨五入)
        summary = make_summary("TMA", regular=50)
        assert calc_expected_gross(summary, Decimal(1015), RULES) == Decimal("846")


class TestVerify:
    def test_v1_exact_match_ok(self):
        mf_rows = read_mf_result_csv(FIXTURES / "mf_result_ok.csv")
        result = verify(SUMMARIES, MEMBERS, WAGES, mf_rows, RULES, "2026-07")
        assert result.ok is True
        report = render_verify_report(result)
        assert report.startswith("RESULT: OK")

    def test_v2_single_100yen_diff_identified(self, tmp_path):
        path = tmp_path / "mf.csv"
        path.write_text(
            "従業員コード,氏名,総支給額\nEMPA,試験 太郎,8000\nEMPB,試験 花子,11200\n",
            encoding="utf-8",
        )
        result = verify(SUMMARIES, MEMBERS, WAGES, read_mf_result_csv(path), RULES, "2026-07")
        assert result.ok is False
        assert any("試験 花子" in issue and "+100円" in issue for issue in result.issues)
        # 差異のない人は巻き込まれない
        assert not any("試験 太郎" in issue for issue in result.issues)
        report = render_verify_report(result)
        assert report.startswith("RESULT: NG")
        assert "原因候補" in report

    def test_v3_member_missing_in_mf(self, tmp_path):
        path = tmp_path / "mf.csv"
        path.write_text("従業員コード,氏名,総支給額\nEMPA,試験 太郎,8000\n", encoding="utf-8")
        result = verify(SUMMARIES, MEMBERS, WAGES, read_mf_result_csv(path), RULES, "2026-07")
        assert result.ok is False
        assert any("EMPB" in issue and "存在しません" in issue for issue in result.issues)

    def test_extra_member_in_mf_reported(self, tmp_path):
        path = tmp_path / "mf.csv"
        path.write_text(
            "従業員コード,氏名,総支給額\nEMPA,試験 太郎,8000\nEMPB,試験 花子,11100\nEMPZ,謎の 人物,10000\n",
            encoding="utf-8",
        )
        result = verify(SUMMARIES, MEMBERS, WAGES, read_mf_result_csv(path), RULES, "2026-07")
        assert result.ok is False
        assert any("EMPZ" in issue and "のみ存在" in issue for issue in result.issues)

    def test_missing_wage_blocks_ok(self, tmp_path):
        path = tmp_path / "mf.csv"
        path.write_text("従業員コード,氏名,総支給額\nEMPA,試験 太郎,8000\n", encoding="utf-8")
        wages = {"TMA": make_wage("TMA", None)}
        result = verify([make_summary("TMA", regular=480)], MEMBERS, wages, read_mf_result_csv(path), RULES, "2026-07")
        assert result.ok is False
        assert any("単価未設定" in line.note for line in result.lines)
        assert any("時給が未設定" in issue for issue in result.issues)


class TestReadMfCsv:
    def test_cp932_supported(self, tmp_path):
        path = tmp_path / "mf_cp932.csv"
        path.write_bytes("従業員コード,氏名,総支給額\nEMPA,試験 太郎,8000\n".encode("cp932"))
        rows = read_mf_result_csv(path)
        assert rows[0]["従業員コード"] == "EMPA"

    def test_comma_and_yen_symbols_parsed(self, tmp_path):
        path = tmp_path / "mf.csv"
        path.write_text('従業員コード,氏名,総支給額\nEMPA,試験 太郎,"¥8,000"\n', encoding="utf-8")
        result = verify([make_summary("TMA", regular=480)], MEMBERS, WAGES, read_mf_result_csv(path), RULES, "2026-07")
        assert result.ok is True

    def test_missing_code_column_rejected(self, tmp_path):
        path = tmp_path / "mf.csv"
        path.write_text("社員番号,氏名,総支給額\nEMPA,試験 太郎,8000\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="従業員コード"):
            read_mf_result_csv(path)

    def test_missing_file_rejected(self, tmp_path):
        with pytest.raises(ConfigError):
            read_mf_result_csv(tmp_path / "none.csv")
