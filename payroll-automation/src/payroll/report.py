"""承認①提示用の集計表・エラーリスト出力(T4)。

summary.md / summary.csv / errors.md を生成する。エラー(打刻漏れ・重複・
単価未設定)が1件でもあれば summary.md の冒頭に目立つ形で件数を表示する。
出力ファイルには給与情報が含まれるため output/ はgitignore対象。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .models import AggregationResult, TeamMember, WageInfo
from .rules import Rules
from .timeutil import format_hhmm


@dataclass
class ReportFiles:
    summary_md: Path
    summary_csv: Path
    errors_md: Path
    error_count: int = 0
    warning_count: int = 0
    member_count: int = 0


def _name_of(member_id: str, members: dict[str, TeamMember]) -> str:
    member = members.get(member_id)
    return member.display_name if member else member_id


def _store_names(location_ids: list[str], locations: dict[str, str]) -> str:
    return "・".join(locations.get(loc, loc) for loc in location_ids) or "-"


def _wage_missing_ids(result: AggregationResult, wages: dict[str, WageInfo]) -> list[str]:
    missing = []
    for summary in result.summaries:
        info = wages.get(summary.team_member_id)
        if info is None or info.hourly_rate_yen is None:
            missing.append(summary.team_member_id)
    return missing


def _collect_warnings(result: AggregationResult, wages: dict[str, WageInfo]) -> list[tuple[str, str]]:
    """(member_id, テキスト) 形式の警告一覧(休憩不備・時給重複設定)。"""
    entries: list[tuple[str, str]] = []
    for dw in result.daily_works:
        for warning in dw.warnings:
            entries.append((dw.team_member_id, f"{dw.work_date}: {warning}"))
    for info in wages.values():
        for warning in info.warnings:
            entries.append((info.team_member_id, warning))
    return entries


def build_summary_md(
    result: AggregationResult,
    members: dict[str, TeamMember],
    wages: dict[str, WageInfo],
    locations: dict[str, str],
    rules: Rules,
) -> str:
    wage_missing = _wage_missing_ids(result, wages)
    error_count = len(result.errors) + len(wage_missing)
    warning_count = len(_collect_warnings(result, wages))
    generated_at = datetime.now(rules.tzinfo)

    lines: list[str] = []
    lines.append(f"# 給与集計表 {result.period}")
    lines.append("")
    lines.append(f"- 生成日時: {generated_at:%Y-%m-%d %H:%M} ({rules.timezone})")
    lines.append(f"- 対象期間: {result.period}(締め日設定: {'月末' if rules.closing_day == 99 else f'{rules.closing_day}日'})")
    lines.append(f"- 対象者: {len(result.summaries)}名")
    lines.append("")

    if error_count or warning_count:
        lines.append(f"> ⚠️ **エラー {error_count}件 / 警告 {warning_count}件 — `errors.md` を必ず確認してください。**")
        lines.append("> エラーを解消しないままの承認・MF給与インポートは禁止です(承認ゲート①)。")
        lines.append("")

    lines.append("| 従業員 | 店舗 | 出勤日数 | 総労働 | 所定内 | 時間外 | 時間外(月60h超) | 深夜 | うち休日深夜 | 休日 | 時給 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")

    def sort_key(s):
        return (_name_of(s.team_member_id, members), s.team_member_id)

    for s in sorted(result.summaries, key=sort_key):
        wage_state = "未設定 ⚠️" if s.team_member_id in wage_missing else "設定済"
        lines.append(
            "| {name} | {stores} | {days} | {total} | {reg} | {ot} | {ot60} | {night} | {hn} | {hol} | {wage} |".format(
                name=_name_of(s.team_member_id, members),
                stores=_store_names(s.location_ids, locations),
                days=s.days_worked,
                total=format_hhmm(s.total_minutes),
                reg=format_hhmm(s.regular_minutes),
                ot=format_hhmm(s.overtime_minutes),
                ot60=format_hhmm(s.overtime_over60_minutes),
                night=format_hhmm(s.night_minutes),
                hn=format_hhmm(s.holiday_night_minutes),
                hol=format_hhmm(s.holiday_minutes),
                wage=wage_state,
            )
        )

    if result.summaries:
        lines.append(
            "| **合計** | - | {days} | {total} | {reg} | {ot} | {ot60} | {night} | {hn} | {hol} | - |".format(
                days=sum(s.days_worked for s in result.summaries),
                total=format_hhmm(sum(s.total_minutes for s in result.summaries)),
                reg=format_hhmm(sum(s.regular_minutes for s in result.summaries)),
                ot=format_hhmm(sum(s.overtime_minutes for s in result.summaries)),
                ot60=format_hhmm(sum(s.overtime_over60_minutes for s in result.summaries)),
                night=format_hhmm(sum(s.night_minutes for s in result.summaries)),
                hn=format_hhmm(sum(s.holiday_night_minutes for s in result.summaries)),
                hol=format_hhmm(sum(s.holiday_minutes for s in result.summaries)),
            )
        )
    else:
        lines.append("")
        lines.append("対象期間に集計対象の勤務がありません。")

    lines.append("")
    lines.append(
        f"凡例: 時間外=割増{rules.premium_overtime} / 月60h超={rules.premium_overtime_over_60h} / "
        f"深夜=+{rules.premium_late_night} / 休日={rules.premium_legal_holiday}"
        f"(休日×深夜={rules.premium_legal_holiday + rules.premium_late_night})"
    )
    lines.append("")
    lines.append("承認ゲート①: 本表と errors.md を確認・承認後に `payroll export-mf` を実行してください。")
    return "\n".join(lines) + "\n"


def build_errors_md(
    result: AggregationResult,
    members: dict[str, TeamMember],
    wages: dict[str, WageInfo],
    locations: dict[str, str],
) -> str:
    wage_missing = _wage_missing_ids(result, wages)
    warnings = _collect_warnings(result, wages)

    lines: list[str] = [f"# エラー・警告リスト {result.period}", ""]

    if not result.errors and not wage_missing and not warnings:
        lines.append("エラー・警告はありません。")
        return "\n".join(lines) + "\n"

    if result.errors:
        lines.append(f"## 集計から除外したタイムカード({len(result.errors)}件)")
        lines.append("")
        lines.append("修正のうえ再集計するまで、該当分は支給時間に含まれていません。")
        lines.append("")
        kind_labels = {
            "open_timecard": "退勤打刻漏れ",
            "overlap": "タイムカード重複",
            "invalid_timecard": "不正な打刻",
        }
        for err in result.errors:
            label = kind_labels.get(err.kind, err.kind)
            date_text = f" {err.work_date}" if err.work_date else ""
            lines.append(f"- [{label}] {_name_of(err.team_member_id, members)}{date_text}: {err.message}")
        lines.append("")

    if wage_missing:
        lines.append(f"## 単価未設定({len(wage_missing)}名)")
        lines.append("")
        lines.append("Squareに時給が登録されていないため金額計算ができません。")
        lines.append("")
        for member_id in wage_missing:
            lines.append(f"- {_name_of(member_id, members)}")
        lines.append("")

    if warnings:
        lines.append(f"## 警告({len(warnings)}件)— 労働者有利側で集計済み")
        lines.append("")
        for member_id, text in warnings:
            lines.append(f"- {_name_of(member_id, members)} {text}")
        lines.append("")

    return "\n".join(lines) + "\n"


def build_summary_csv_rows(
    result: AggregationResult,
    members: dict[str, TeamMember],
    wages: dict[str, WageInfo],
    locations: dict[str, str],
) -> list[list[str]]:
    header = [
        "team_member_id",
        "従業員コード",
        "氏名",
        "店舗",
        "出勤日数",
        "総労働分",
        "所定内分",
        "時間外分",
        "時間外60h超分",
        "深夜分",
        "うち休日深夜分",
        "休日分",
        "時給設定",
        "警告件数",
    ]
    rows = [header]
    for s in sorted(result.summaries, key=lambda x: (_name_of(x.team_member_id, members), x.team_member_id)):
        member = members.get(s.team_member_id)
        info = wages.get(s.team_member_id)
        has_wage = info is not None and info.hourly_rate_yen is not None
        rows.append(
            [
                s.team_member_id,
                member.employee_code if member else s.team_member_id,
                _name_of(s.team_member_id, members),
                _store_names(s.location_ids, locations),
                str(s.days_worked),
                str(s.total_minutes),
                str(s.regular_minutes),
                str(s.overtime_minutes),
                str(s.overtime_over60_minutes),
                str(s.night_minutes),
                str(s.holiday_night_minutes),
                str(s.holiday_minutes),
                "1" if has_wage else "0",
                str(len(s.warnings)),
            ]
        )
    return rows


def write_reports(
    result: AggregationResult,
    members: dict[str, TeamMember],
    wages: dict[str, WageInfo],
    locations: dict[str, str],
    rules: Rules,
    output_dir: str | Path,
) -> ReportFiles:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_md = output_dir / "summary.md"
    summary_csv = output_dir / "summary.csv"
    errors_md = output_dir / "errors.md"

    summary_md.write_text(build_summary_md(result, members, wages, locations, rules), encoding="utf-8")
    with summary_csv.open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(build_summary_csv_rows(result, members, wages, locations))
    errors_md.write_text(build_errors_md(result, members, wages, locations), encoding="utf-8")

    return ReportFiles(
        summary_md=summary_md,
        summary_csv=summary_csv,
        errors_md=errors_md,
        error_count=len(result.errors) + len(_wage_missing_ids(result, wages)),
        warning_count=len(_collect_warnings(result, wages)),
        member_count=len(result.summaries),
    )
