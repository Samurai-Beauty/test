"""MF給与計算結果との突合(T6、承認ゲート②)。

独立計算(時給×カテゴリ時間×割増率、Decimal)とMF給与のエクスポートCSVを
比較し、人別差異を verify_report.md に出力する。差異ゼロなら RESULT: OK、
1円でも差異があれば RESULT: NG と原因候補を出す。差異がある間は給与確定禁止。

MF側CSVの列仕様は仮(TBD-1): 従業員コード, 氏名, 総支給額
※ここでの総支給額は勤怠連動の支給部分を想定。通勤手当等が混ざる場合は
  TBD-1確定時に列マッピングを見直す。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from .models import MonthlySummary, TeamMember, WageInfo
from .rules import ConfigError, Rules

# MF給与エクスポートCSVの列名(仮仕様 TBD-1)
MF_COLUMN_CODE = "従業員コード"
MF_COLUMN_NAME = "氏名"
MF_COLUMN_GROSS = "総支給額"


@dataclass
class VerifyLine:
    employee_code: str
    name: str
    expected_yen: Decimal | None  # 独立計算(None=単価未設定で計算不可)
    mf_yen: Decimal | None  # MF給与側(None=MF側に存在しない)
    note: str = ""

    @property
    def diff_yen(self) -> Decimal | None:
        if self.expected_yen is None or self.mf_yen is None:
            return None
        return self.mf_yen - self.expected_yen


@dataclass
class VerifyResult:
    period: str
    ok: bool
    lines: list[VerifyLine] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def calc_expected_gross(summary: MonthlySummary, hourly_rate_yen: Decimal, rules: Rules) -> Decimal:
    """勤怠連動の支給額を独立計算する(検算用。確定額はMF給与が正)。

    時間外1.25 / 月60h超1.50 / 休日1.35 は各カテゴリの支給率、深夜0.25は加算分。
    円未満は四捨五入(MF側の丸め仕様が異なる場合はTBD-1確定時に合わせる)。
    """

    def hours(minutes: int) -> Decimal:
        return Decimal(minutes) / Decimal(60)

    amount = (
        hourly_rate_yen * hours(summary.regular_minutes)
        + hourly_rate_yen * hours(summary.overtime_minutes) * rules.premium_overtime
        + hourly_rate_yen * hours(summary.overtime_over60_minutes) * rules.premium_overtime_over_60h
        + hourly_rate_yen * hours(summary.holiday_minutes) * rules.premium_legal_holiday
        + hourly_rate_yen * hours(summary.night_minutes) * rules.premium_late_night
    )
    return amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def read_mf_result_csv(path: str | Path) -> list[dict[str, str]]:
    """MF給与の計算結果CSV(仮仕様)を読み込む。utf-8-sig → cp932 の順に試す。"""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"MF給与の計算結果CSVがありません: {path}")
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "cp932"):
        try:
            with path.open(encoding=encoding, newline="") as f:
                rows = list(csv.DictReader(f))
            break
        except UnicodeDecodeError as exc:
            last_error = exc
    else:
        raise ConfigError(f"MF給与CSVの文字コードを判定できません: {path}") from last_error

    if rows and MF_COLUMN_CODE not in rows[0]:
        raise ConfigError(
            f"MF給与CSVに『{MF_COLUMN_CODE}』列がありません(仮仕様の列: "
            f"{MF_COLUMN_CODE}/{MF_COLUMN_NAME}/{MF_COLUMN_GROSS}。TBD-1確定時はverify.pyの列名定数を更新)"
        )
    return rows


def _parse_yen(text: str) -> Decimal:
    cleaned = (text or "").replace(",", "").replace("¥", "").replace("円", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ConfigError(f"MF給与CSVの金額を解釈できません: {text!r}") from exc


def verify(
    summaries: list[MonthlySummary],
    members: dict[str, TeamMember],
    wages: dict[str, WageInfo],
    mf_rows: list[dict[str, str]],
    rules: Rules,
    period: str,
) -> VerifyResult:
    mf_by_code: dict[str, dict[str, str]] = {}
    for row in mf_rows:
        code = (row.get(MF_COLUMN_CODE) or "").strip()
        if code:
            mf_by_code[code] = row

    lines: list[VerifyLine] = []
    issues: list[str] = []
    matched_codes: set[str] = set()

    for summary in sorted(summaries, key=lambda s: s.team_member_id):
        member = members.get(summary.team_member_id)
        code = member.employee_code if member else summary.team_member_id
        name = member.display_name if member else summary.team_member_id

        info = wages.get(summary.team_member_id)
        expected: Decimal | None = None
        note = ""
        if info is None or info.hourly_rate_yen is None:
            note = "単価未設定のため独立計算不可"
            issues.append(f"{name}: 時給が未設定のため検算できません(Square側の時給登録を確認)")
        else:
            expected = calc_expected_gross(summary, info.hourly_rate_yen, rules)

        mf_row = mf_by_code.get(code)
        mf_yen: Decimal | None = None
        if mf_row is None:
            issues.append(f"{name}(コード{code}): MF給与側に存在しません(取込漏れの可能性)")
            note = (note + " / " if note else "") + "MF側に欠落"
        else:
            matched_codes.add(code)
            mf_yen = _parse_yen(mf_row.get(MF_COLUMN_GROSS, ""))

        line = VerifyLine(employee_code=code, name=name, expected_yen=expected, mf_yen=mf_yen, note=note)
        lines.append(line)
        if line.diff_yen is not None and line.diff_yen != 0:
            issues.append(f"{name}(コード{code}): 差異 {line.diff_yen:+}円(MF {mf_yen}円 / 独立計算 {expected}円)")

    for code, row in mf_by_code.items():
        if code not in matched_codes:
            name = (row.get(MF_COLUMN_NAME) or code).strip()
            issues.append(f"{name}(コード{code}): MF給与側にのみ存在します(集計対象外の人物が混入)")

    ok = not issues and all(line.diff_yen == 0 for line in lines)
    return VerifyResult(period=period, ok=ok, lines=lines, issues=issues)


def render_verify_report(result: VerifyResult) -> str:
    lines: list[str] = []
    lines.append(f"RESULT: {'OK' if result.ok else 'NG'}")
    lines.append("")
    lines.append(f"# 検算レポート {result.period}")
    lines.append("")
    if result.ok:
        lines.append("MF給与の計算結果と独立計算は全員一致しました(差異ゼロ)。")
        lines.append("承認ゲート②: 内容確認のうえMF給与側で給与確定に進めます。")
    else:
        lines.append("**差異または未解決の問題があります。原因を特定するまで給与確定は禁止です(承認ゲート②)。**")
    lines.append("")

    lines.append("| 従業員コード | 氏名 | MF給与(円) | 独立計算(円) | 差異(円) | 備考 |")
    lines.append("|---|---|---:|---:|---:|---|")
    for line in result.lines:
        diff = line.diff_yen
        lines.append(
            "| {code} | {name} | {mf} | {expected} | {diff} | {note} |".format(
                code=line.employee_code,
                name=line.name,
                mf="-" if line.mf_yen is None else f"{line.mf_yen:,}",
                expected="-" if line.expected_yen is None else f"{line.expected_yen:,}",
                diff="-" if diff is None else f"{diff:+,}",
                note=line.note or "",
            )
        )
    lines.append("")

    if result.issues:
        lines.append("## 検出された問題")
        lines.append("")
        for issue in result.issues:
            lines.append(f"- {issue}")
        lines.append("")
        lines.append("## 原因候補")
        lines.append("")
        lines.append("- 丸め設定(TBD-3)や締め期間(TBD-2)がMF給与側の設定と不一致")
        lines.append("- 割増率(config/payroll_rules.yaml)とMF給与側の割増設定の不一致")
        lines.append("- 時給マスタの不一致(Square listTeamMemberWages と MF給与の単価)")
        lines.append("- MF給与側の総支給額に勤怠連動以外の手当・控除が混入(TBD-1)")
        lines.append("")

    return "\n".join(lines) + "\n"


def write_verify_report(result: VerifyResult, output_dir: str | Path) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "verify_report.md"
    path.write_text(render_verify_report(result), encoding="utf-8")
    return path
