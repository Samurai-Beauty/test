"""CLIエントリポイント(T8)。

コマンドを fetch / aggregate / export-mf / verify の4つに分離していること
自体が承認ゲートの実装である(設計書5)。自動で次工程へ進む連続実行モードは
存在しないし、追加してはならない。振込・給与確定を行う機能もない。

標準出力には件数・期間・ファイルパスのみを出し、個人名・時給を出さない。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from . import __version__
from .aggregate import aggregate
from .export_mf import load_mapping, write_mf_csv
from .fetch import fetch_period, load_raw, parse_raw
from .report import write_reports
from .rules import ConfigError, load_locations, load_rules
from .square_client import SquareApiError, SquareClient
from .verify import read_mf_result_csv, verify, write_verify_report

EXIT_OK = 0
EXIT_FAILURE = 1  # 実行時エラー・検算NG
EXIT_CONFIG = 2  # 設定・前提条件の不備


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="payroll",
        description="Samurai Beauty 給与計算自動化(集計・検算・データ生成のみ。確定計算はMF給与、振込は人間)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config-dir", default="config", help="設定ディレクトリ(既定: config)")
    parser.add_argument("--output-dir", default="output", help="出力ディレクトリ(既定: output)")

    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="Squareからタイムカード・時給・週設定を取得して保存")
    p_fetch.add_argument("--period", required=True, help="給与期間 YYYY-MM(例: 2026-07)")

    p_agg = sub.add_parser("aggregate", help="取得済みデータから集計し summary/errors を出力(→承認①)")
    p_agg.add_argument("--period", required=True, help="給与期間 YYYY-MM")

    p_export = sub.add_parser("export-mf", help="MF給与インポートCSVを生成(承認①の後に人間が実行)")
    p_export.add_argument("--period", required=True, help="給与期間 YYYY-MM")

    p_verify = sub.add_parser("verify", help="MF給与の計算結果CSVと独立計算を突合(→承認②)")
    p_verify.add_argument("--period", required=True, help="給与期間 YYYY-MM")
    p_verify.add_argument("--mf-csv", required=True, help="MF給与からエクスポートした計算結果CSVのパス")

    return parser


def _load_config(args):
    config_dir = Path(args.config_dir)
    rules = load_rules(config_dir / "payroll_rules.yaml")
    locations = load_locations(config_dir / "locations.yaml")
    return rules, locations


def cmd_fetch(args) -> int:
    rules, locations = _load_config(args)
    load_dotenv()
    token = os.environ.get("SQUARE_ACCESS_TOKEN", "").strip()
    if not token:
        print(
            "SQUARE_ACCESS_TOKEN が設定されていません。\n"
            "読み取り専用スコープ(TIMECARDS_READ, EMPLOYEES_READ)のトークンを .env に設定してください。\n"
            "例: cp .env.example .env && エディタで .env を編集",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    client = SquareClient(token)
    try:
        stats = fetch_period(client, rules, locations, args.period, args.output_dir)
    finally:
        client.close()

    # 個人名・時給は出力しない(件数と期間のみ)
    print(f"期間 {stats.period}(取得範囲 {stats.range_start} 〜 {stats.range_end})")
    print(
        f"取得: タイムカード {stats.timecard_count}件 / 時給マスタ {stats.wage_count}件 / "
        f"メンバー {stats.team_member_count}名 / 週設定 {stats.workweek_config_count}件"
    )
    for warning in stats.warnings:
        print(f"警告: {warning}")
    print(f"保存先: {stats.output_path}")
    print(f"次の工程: payroll aggregate --period {stats.period}")
    return EXIT_OK


def _aggregate_from_raw(args):
    rules, locations = _load_config(args)
    payload = load_raw(args.output_dir, args.period)
    parsed = parse_raw(payload, rules)
    result = aggregate(parsed.timecards, rules, args.period)
    return rules, locations, parsed, result


def cmd_aggregate(args) -> int:
    rules, locations, parsed, result = _aggregate_from_raw(args)
    files = write_reports(
        result, parsed.members, parsed.wages, locations, rules, Path(args.output_dir) / args.period
    )
    print(f"期間 {args.period}: 対象 {files.member_count}名 / エラー {files.error_count}件 / 警告 {files.warning_count}件")
    print(f"集計表: {files.summary_md}")
    print(f"        {files.summary_csv}")
    print(f"エラーリスト: {files.errors_md}")
    if files.error_count:
        print("⚠️ エラーがあります。errors.md の内容を解消(打刻修正→fetchからやり直し)するか、")
        print("   除外内容を理解・承認したうえで次工程に進んでください。")
    print("承認ゲート①: summary.md をサムライ社長が確認・承認後、payroll export-mf を実行してください。")
    return EXIT_OK


def cmd_export_mf(args) -> int:
    rules, locations, parsed, result = _aggregate_from_raw(args)
    mapping = load_mapping(Path(args.config_dir) / "mf_import_mapping.yaml")
    output_path = Path(args.output_dir) / args.period / "mf_import.csv"
    write_mf_csv(result.summaries, parsed.members, mapping, output_path)
    print(f"MF給与インポートCSVを生成しました: {output_path}({len(result.summaries)}名)")
    if result.errors:
        print(f"⚠️ 未解消のエラーが {len(result.errors)}件あります(該当分は不算入)。errors.md を確認してください。")
    print("MFクラウド給与に手動インポートし、計算結果CSVをエクスポートしたら payroll verify を実行してください。")
    return EXIT_OK


def cmd_verify(args) -> int:
    rules, locations, parsed, result = _aggregate_from_raw(args)
    mf_rows = read_mf_result_csv(args.mf_csv)
    verify_result = verify(result.summaries, parsed.members, parsed.wages, mf_rows, rules, args.period)
    report_path = write_verify_report(verify_result, Path(args.output_dir) / args.period)
    print(f"RESULT: {'OK' if verify_result.ok else 'NG'}")
    print(f"検算レポート: {report_path}")
    if verify_result.ok:
        print("承認ゲート②: 差異ゼロを確認しました。レポート確認後、MF給与側で給与確定に進めます。")
    else:
        print("差異または未解決の問題があります。原因を特定するまで給与確定は禁止です(詳細はレポート参照)。")
    return EXIT_OK if verify_result.ok else EXIT_FAILURE


_COMMANDS = {
    "fetch": cmd_fetch,
    "aggregate": cmd_aggregate,
    "export-mf": cmd_export_mf,
    "verify": cmd_verify,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _COMMANDS[args.command](args)
    except ConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except SquareApiError as exc:
        print(f"Square APIエラー: {exc}", file=sys.stderr)
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
