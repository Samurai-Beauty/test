"""T4: report.py のテスト。

受け入れ基準3: エラー系(A13-A15)が承認①の資料(errors.md)に必ず現れること。
"""

from datetime import timedelta
from pathlib import Path

import pytest
from freezegun import freeze_time

from payroll.aggregate import aggregate
from payroll.report import build_errors_md, build_summary_md, write_reports
from payroll.rules import load_rules

from tests.helpers import MON, SUN, make_member, make_wage, tc

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
RULES = load_rules(CONFIG_DIR / "payroll_rules.yaml")

LOCATIONS = {"LFAKE001": "テスト店A", "LFAKE002": "テスト店B"}

MEMBERS = {
    "M1": make_member("M1", "試験 太郎", "EMP001"),
    "M2": make_member("M2", "試験 花子", "EMP002"),
    "MX": make_member("MX", "試験 次郎", "EMP003"),
    "MY": make_member("MY", "試験 三奈", "EMP004"),
}

WAGES = {
    "M1": make_wage("M1", 1200),
    "M2": make_wage("M2", 1300),
    "MX": make_wage("MX", 1200),
    # MY は単価未設定
}


@pytest.fixture()
def dirty_result():
    """エラー・警告を含む集計結果(A13 OPEN / A14 範囲外休憩 / A15 重複)。"""
    cards = [
        tc("M1", MON, "09:00", "18:00", [("12:00", "13:00")]),
        tc("M1", MON + timedelta(days=1), "09:00", "18:00", [("08:30", "09:30")]),  # A14 警告
        tc("M2", MON + timedelta(days=2), "09:00", None),  # A13 OPEN
        tc("M2", MON + timedelta(days=3), "09:00", "18:00", [("12:00", "13:00")]),
        tc("MX", MON, "09:00", "18:00"),
        tc("MX", MON, "17:00", "21:00"),  # A15 重複
        tc("MY", SUN, "09:00", "18:00", [("12:00", "13:00")], location="LFAKE002"),  # 休日出勤
    ]
    return aggregate(cards, RULES, "2026-07")


@pytest.fixture()
def clean_result():
    return aggregate([tc("M1", MON, "09:00", "18:00", [("12:00", "13:00")])], RULES, "2026-07")


class TestSummaryMd:
    def test_error_banner_on_top_when_errors_exist(self, dirty_result):
        md = build_summary_md(dirty_result, MEMBERS, WAGES, LOCATIONS, RULES)
        # OPEN 1件 + 重複 1件 + 単価未設定 1名 = エラー3件、休憩警告1件
        banner_line = next(line for line in md.splitlines() if "エラー" in line)
        assert "エラー 3件" in banner_line and "警告 1件" in banner_line
        # 冒頭(表より前)に表示されること
        assert md.index("エラー 3件") < md.index("| 従業員 |")

    def test_no_banner_when_clean(self, clean_result):
        md = build_summary_md(clean_result, {"M1": MEMBERS["M1"]}, {"M1": WAGES["M1"]}, LOCATIONS, RULES)
        assert "エラー" not in md.split("| 従業員 |")[0]  # 表の前にバナーなし

    def test_member_rows_and_store_names(self, dirty_result):
        md = build_summary_md(dirty_result, MEMBERS, WAGES, LOCATIONS, RULES)
        assert "試験 太郎" in md and "テスト店A" in md
        assert "試験 三奈" in md and "テスト店B" in md
        assert "試験 次郎" not in md  # 重複エラーで集計除外(errors.mdに掲載)
        assert "未設定 ⚠️" in md  # MY の時給
        # M1: 月曜480 + 火曜540(休憩控除なし) = 17:00
        assert "| 試験 太郎 | テスト店A | 2 | 17:00 |" in md

    @freeze_time("2026-07-31T06:00:00Z")  # JST 2026-07-31 15:00
    def test_generated_timestamp_in_jst(self, clean_result):
        md = build_summary_md(clean_result, MEMBERS, WAGES, LOCATIONS, RULES)
        assert "2026-07-31 15:00" in md

    def test_empty_summaries_message(self):
        result = aggregate([], RULES, "2026-07")
        md = build_summary_md(result, {}, {}, LOCATIONS, RULES)
        assert "集計対象の勤務がありません" in md


class TestErrorsMd:
    def test_a13_a15_and_wage_missing_all_reported(self, dirty_result):
        md = build_errors_md(dirty_result, MEMBERS, WAGES, LOCATIONS)
        # A13: OPENタイムカード(人名・日付付き)
        assert "退勤打刻漏れ" in md
        assert "試験 花子" in md and "2026-07-08" in md
        # A15: 重複
        assert "タイムカード重複" in md and "試験 次郎" in md
        # A14: 休憩警告(人名・日付付き)
        assert "範囲外" in md and "試験 太郎 2026-07-07" in md
        # 単価未設定
        assert "単価未設定" in md and "試験 三奈" in md

    def test_clean_result_reports_nothing(self, clean_result):
        md = build_errors_md(clean_result, MEMBERS, WAGES, LOCATIONS)
        assert "エラー・警告はありません" in md


class TestWriteReports:
    def test_files_written_with_counts(self, dirty_result, tmp_path):
        files = write_reports(dirty_result, MEMBERS, WAGES, LOCATIONS, RULES, tmp_path)
        assert files.summary_md.exists() and files.errors_md.exists() and files.summary_csv.exists()
        assert files.error_count == 3
        assert files.warning_count == 1
        assert files.member_count == 3  # M1, M2, MY(MXは除外)

    def test_csv_is_excel_friendly(self, dirty_result, tmp_path):
        files = write_reports(dirty_result, MEMBERS, WAGES, LOCATIONS, RULES, tmp_path)
        raw = files.summary_csv.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM(Excelで文字化けしない)
        import csv

        with files.summary_csv.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        assert rows[0][2] == "氏名"
        assert len(rows) == 1 + 3  # ヘッダ + 3名
        codes = {row[1] for row in rows[1:]}
        assert codes == {"EMP001", "EMP002", "EMP004"}
