"""T5: export_mf.py のテスト(構築書4.4 E1〜E3)。"""

import csv
from pathlib import Path

import pytest

from payroll.export_mf import format_minutes, load_mapping, write_mf_csv
from payroll.models import MonthlySummary
from payroll.rules import ConfigError

from tests.helpers import make_member

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"

MEMBERS = {
    "TMA": make_member("TMA", "試験 太郎", "EMP001"),
    "TMB": make_member("TMB", "試験 花子", "EMP002"),
}


def make_summary(member_id="TMA", regular=510, overtime=0, over60=0, night=0, holiday=0) -> MonthlySummary:
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
        holiday_night_minutes=0,
    )


def write_mapping(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "mapping.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def read_csv(path: Path, encoding="utf-8-sig") -> list[list[str]]:
    with path.open(encoding=encoding, newline="") as f:
        return list(csv.reader(f))


class TestFormatMinutes:
    def test_e1_hhmm(self):
        assert format_minutes(510, "hhmm", 2) == "08:30"
        assert format_minutes(0, "hhmm", 2) == "00:00"
        assert format_minutes(65, "hhmm", 2) == "01:05"

    def test_e2_decimal(self):
        assert format_minutes(510, "decimal", 2) == "8.5"
        assert format_minutes(480, "decimal", 2) == "8"
        assert format_minutes(470, "decimal", 2) == "7.83"  # 四捨五入


class TestDefaultMapping:
    def test_config_mapping_loads(self):
        mapping = load_mapping(CONFIG_DIR / "mf_import_mapping.yaml")
        assert mapping.time_format == "hhmm"
        assert [c.source for c in mapping.columns] == [
            "employee_code",
            "name",
            "regular",
            "overtime",
            "overtime_over60",
            "night",
            "holiday",
        ]

    def test_e1_default_mapping_outputs_hhmm(self, tmp_path):
        mapping = load_mapping(CONFIG_DIR / "mf_import_mapping.yaml")
        path = write_mf_csv([make_summary()], MEMBERS, mapping, tmp_path / "mf.csv")
        rows = read_csv(path)
        assert rows[0] == ["従業員コード", "氏名", "所定内時間", "時間外時間", "60時間超時間外", "深夜時間", "休日時間"]
        assert rows[1] == ["EMP001", "試験 太郎", "08:30", "00:00", "00:00", "00:00", "00:00"]


class TestMappingDriven:
    def test_e2_decimal_via_mapping_only(self, tmp_path):
        mapping_path = write_mapping(
            tmp_path,
            """
encoding: utf-8-sig
time_format: decimal
decimal_places: 2
columns:
  - {header: 従業員コード, source: employee_code}
  - {header: 所定内時間, source: regular}
""",
        )
        mapping = load_mapping(mapping_path)
        path = write_mf_csv([make_summary(regular=510)], MEMBERS, mapping, tmp_path / "mf.csv")
        rows = read_csv(path)
        assert rows[1] == ["EMP001", "8.5"]

    def test_e3_column_order_changes_with_mapping_only(self, tmp_path):
        # コード変更なしで mapping の並び替えだけが列順に反映されること
        reordered = write_mapping(
            tmp_path,
            """
encoding: utf-8-sig
time_format: hhmm
decimal_places: 2
columns:
  - {header: 深夜時間, source: night}
  - {header: 氏名, source: name}
  - {header: 従業員コード, source: employee_code}
""",
        )
        mapping = load_mapping(reordered)
        path = write_mf_csv([make_summary(regular=480, night=60)], MEMBERS, mapping, tmp_path / "mf.csv")
        rows = read_csv(path)
        assert rows[0] == ["深夜時間", "氏名", "従業員コード"]
        assert rows[1] == ["01:00", "試験 太郎", "EMP001"]

    def test_per_column_format_override(self, tmp_path):
        mapping_path = write_mapping(
            tmp_path,
            """
encoding: utf-8-sig
time_format: hhmm
decimal_places: 2
columns:
  - {header: 所定内時間, source: regular}
  - {header: 所定内時間(十進), source: regular, format: decimal}
""",
        )
        mapping = load_mapping(mapping_path)
        path = write_mf_csv([make_summary(regular=510)], MEMBERS, mapping, tmp_path / "mf.csv")
        rows = read_csv(path)
        assert rows[1] == ["08:30", "8.5"]

    def test_cp932_encoding(self, tmp_path):
        mapping_path = write_mapping(
            tmp_path,
            """
encoding: cp932
time_format: hhmm
decimal_places: 2
columns:
  - {header: 氏名, source: name}
""",
        )
        mapping = load_mapping(mapping_path)
        path = write_mf_csv([make_summary()], MEMBERS, mapping, tmp_path / "mf.csv")
        rows = read_csv(path, encoding="cp932")
        assert rows[1] == ["試験 太郎"]

    def test_multiple_members_sorted(self, tmp_path):
        mapping = load_mapping(CONFIG_DIR / "mf_import_mapping.yaml")
        summaries = [make_summary("TMB", regular=480), make_summary("TMA", regular=510)]
        path = write_mf_csv(summaries, MEMBERS, mapping, tmp_path / "mf.csv")
        rows = read_csv(path)
        assert [row[0] for row in rows[1:]] == ["EMP001", "EMP002"]


class TestMappingValidation:
    def test_unknown_source_rejected(self, tmp_path):
        path = write_mapping(
            tmp_path,
            """
columns:
  - {header: 謎の列, source: unknown_field}
""",
        )
        with pytest.raises(ConfigError, match="unknown_field"):
            load_mapping(path)

    def test_invalid_time_format_rejected(self, tmp_path):
        path = write_mapping(
            tmp_path,
            """
time_format: seconds
columns:
  - {header: 所定内時間, source: regular}
""",
        )
        with pytest.raises(ConfigError, match="time_format"):
            load_mapping(path)

    def test_empty_columns_rejected(self, tmp_path):
        path = write_mapping(tmp_path, "time_format: hhmm\n")
        with pytest.raises(ConfigError, match="columns"):
            load_mapping(path)

    def test_missing_file_rejected(self, tmp_path):
        with pytest.raises(ConfigError):
            load_mapping(tmp_path / "none.yaml")
