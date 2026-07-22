"""MF給与インポートCSV生成(T5)。

列構成・並び順・時間表記・文字コードはすべて config/mf_import_mapping.yaml で
定義する(TBD-1)。MF給与の実テンプレート確定後は mapping の修正のみで対応し、
コード変更を不要にする。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

from .models import MonthlySummary, TeamMember
from .rules import ConfigError

TIME_FORMATS = ("hhmm", "decimal")

# summary から参照できる項目(時間系は分値を保持し、出力時に書式化する)
TIME_SOURCES = ("regular", "overtime", "overtime_over60", "night", "holiday", "total")
TEXT_SOURCES = ("employee_code", "name", "days_worked")
ALLOWED_SOURCES = TIME_SOURCES + TEXT_SOURCES


@dataclass(frozen=True)
class MfColumn:
    header: str
    source: str
    time_format: str | None = None  # None = mapping全体の time_format を使う


@dataclass(frozen=True)
class MfMapping:
    encoding: str
    time_format: str
    decimal_places: int
    columns: tuple[MfColumn, ...]


def load_mapping(path: str | Path) -> MfMapping:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"MF列マッピングファイルがありません: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ConfigError(f"mf_import_mapping.yaml の形式が不正です: {path}")

    time_format = str(data.get("time_format", "hhmm"))
    if time_format not in TIME_FORMATS:
        raise ConfigError(f"mf_import_mapping.yaml: time_format は {'/'.join(TIME_FORMATS)} のいずれか: {time_format!r}")

    decimal_places = data.get("decimal_places", 2)
    if not isinstance(decimal_places, int) or decimal_places < 0:
        raise ConfigError(f"mf_import_mapping.yaml: decimal_places は0以上の整数: {decimal_places!r}")

    raw_columns = data.get("columns")
    if not isinstance(raw_columns, list) or not raw_columns:
        raise ConfigError("mf_import_mapping.yaml: columns を1列以上定義してください")

    columns: list[MfColumn] = []
    for idx, raw in enumerate(raw_columns):
        if not isinstance(raw, dict) or not raw.get("header") or not raw.get("source"):
            raise ConfigError(f"mf_import_mapping.yaml: columns[{idx}] には header と source が必要です")
        source = str(raw["source"])
        if source not in ALLOWED_SOURCES:
            raise ConfigError(
                f"mf_import_mapping.yaml: columns[{idx}].source '{source}' は未対応"
                f"(対応: {', '.join(ALLOWED_SOURCES)})"
            )
        col_format = raw.get("format")
        if col_format is not None and str(col_format) not in TIME_FORMATS:
            raise ConfigError(f"mf_import_mapping.yaml: columns[{idx}].format は {'/'.join(TIME_FORMATS)} のいずれか")
        columns.append(MfColumn(header=str(raw["header"]), source=source, time_format=col_format))

    return MfMapping(
        encoding=str(data.get("encoding", "utf-8-sig")),
        time_format=time_format,
        decimal_places=decimal_places,
        columns=tuple(columns),
    )


def format_minutes(minutes: int, time_format: str, decimal_places: int) -> str:
    """分値 → MF向け表記。hhmm='08:30' / decimal='8.5'(末尾ゼロは省く)。"""
    if time_format == "hhmm":
        return f"{minutes // 60:02d}:{minutes % 60:02d}"
    quantum = Decimal(1).scaleb(-decimal_places) if decimal_places > 0 else Decimal(1)
    value = (Decimal(minutes) / Decimal(60)).quantize(quantum, rounding=ROUND_HALF_UP)
    text = format(value.normalize(), "f")
    return text


def build_row_values(summary: MonthlySummary, member: TeamMember | None) -> dict[str, object]:
    return {
        "employee_code": member.employee_code if member else summary.team_member_id,
        "name": member.display_name if member else summary.team_member_id,
        "days_worked": summary.days_worked,
        "regular": summary.regular_minutes,
        "overtime": summary.overtime_minutes,
        "overtime_over60": summary.overtime_over60_minutes,
        "night": summary.night_minutes,
        "holiday": summary.holiday_minutes,
        "total": summary.total_minutes,
    }


def write_mf_csv(
    summaries: list[MonthlySummary],
    members: dict[str, TeamMember],
    mapping: MfMapping,
    path: str | Path,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=mapping.encoding, newline="") as f:
        writer = csv.writer(f)
        writer.writerow([col.header for col in mapping.columns])
        for summary in sorted(summaries, key=lambda s: s.team_member_id):
            values = build_row_values(summary, members.get(summary.team_member_id))
            row: list[str] = []
            for col in mapping.columns:
                value = values[col.source]
                if col.source in TIME_SOURCES:
                    fmt = col.time_format or mapping.time_format
                    row.append(format_minutes(int(value), fmt, mapping.decimal_places))
                else:
                    row.append(str(value))
            writer.writerow(row)
    return path
