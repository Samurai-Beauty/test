"""T8: CLI結合テスト+一気通貫E2E(受け入れ基準2)。

fetch(httpxモック)→ aggregate → report → export-mf → verify を
実CLIエントリポイント経由で通す。標準出力に個人名が出ないことも検証する。
"""

import csv
from pathlib import Path

import httpx
import pytest

from payroll import cli
from payroll.square_client import SquareClient

from tests.test_fetch import full_api_handler

CONFIG_DIR = str(Path(__file__).resolve().parents[1] / "config")
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fake_client_factory(token: str) -> SquareClient:
    transport = httpx.MockTransport(full_api_handler)
    return SquareClient(token, client=httpx.Client(transport=transport, base_url="https://connect.squareup.test"))


def run_cli(out_dir: Path, *argv: str) -> int:
    return cli.main(["--config-dir", CONFIG_DIR, "--output-dir", str(out_dir), *argv])


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    """モックSquare+ダミートークンのCLI実行環境(cwdも隔離)。"""
    monkeypatch.chdir(tmp_path)  # 開発者ローカルの .env を拾わないように
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "DUMMY_TOKEN_FOR_TEST")
    monkeypatch.setattr(cli, "SquareClient", fake_client_factory)
    return tmp_path / "output"


class TestEndToEnd:
    def test_full_pipeline(self, cli_env, capsys):
        out = cli_env

        # 1) fetch(モック)
        assert run_cli(out, "fetch", "--period", "2026-07") == 0
        captured = capsys.readouterr()
        assert "タイムカード 4件" in captured.out
        assert "試験" not in captured.out  # 個人名をログに出さない
        assert (out / "2026-07" / "raw_timecards.json").exists()

        # 2) aggregate → 承認①の資料
        assert run_cli(out, "aggregate", "--period", "2026-07") == 0
        captured = capsys.readouterr()
        assert "対象 3名" in captured.out and "エラー 1件" in captured.out
        assert "承認ゲート①" in captured.out
        assert "試験" not in captured.out

        summary_md = (out / "2026-07" / "summary.md").read_text(encoding="utf-8")
        errors_md = (out / "2026-07" / "errors.md").read_text(encoding="utf-8")
        assert "エラー 1件" in summary_md  # 冒頭バナー
        assert "退勤打刻漏れ" in errors_md and "試験 太郎" in errors_md and "2026-07-08" in errors_md

        # 3) export-mf(承認①後に人間が実行する想定)
        assert run_cli(out, "export-mf", "--period", "2026-07") == 0
        with (out / "2026-07" / "mf_import.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        assert rows[0][0] == "従業員コード"
        by_code = {row[0]: row for row in rows[1:]}
        assert by_code["EMP001"][2:] == ["08:00", "00:00", "00:00", "00:00", "00:00"]
        assert by_code["EMP002"][2:] == ["08:00", "01:30", "00:00", "00:30", "00:00"]
        assert by_code["EMP003"][2:] == ["00:00", "00:00", "00:00", "00:00", "05:00"]

        # 4) verify → 承認②(差異ゼロ)
        assert run_cli(out, "verify", "--period", "2026-07", "--mf-csv", str(FIXTURES / "mf_result_e2e.csv")) == 0
        captured = capsys.readouterr()
        assert "RESULT: OK" in captured.out
        report = (out / "2026-07" / "verify_report.md").read_text(encoding="utf-8")
        assert report.startswith("RESULT: OK")

    def test_verify_ng_returns_nonzero(self, cli_env, capsys):
        out = cli_env
        assert run_cli(out, "fetch", "--period", "2026-07") == 0
        assert run_cli(out, "aggregate", "--period", "2026-07") == 0

        broken = out.parent / "mf_broken.csv"
        broken.write_text(
            "従業員コード,氏名,総支給額\nEMP001,試験 太郎,9700\nEMP002,試験 花子,13000\nEMP003,試験 三郎,6750\n",
            encoding="utf-8",
        )
        capsys.readouterr()
        assert run_cli(out, "verify", "--period", "2026-07", "--mf-csv", str(broken)) == 1
        captured = capsys.readouterr()
        assert "RESULT: NG" in captured.out
        report = (out / "2026-07" / "verify_report.md").read_text(encoding="utf-8")
        assert report.startswith("RESULT: NG")
        assert "+100円" in report


class TestGuards:
    def test_fetch_without_token_exits_2(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("SQUARE_ACCESS_TOKEN", raising=False)
        called = []
        monkeypatch.setattr(cli, "SquareClient", lambda token: called.append(token))

        assert run_cli(tmp_path / "output", "fetch", "--period", "2026-07") == 2
        captured = capsys.readouterr()
        assert "SQUARE_ACCESS_TOKEN" in captured.err
        assert called == []  # トークンなしでAPIクライアントを作らない

    def test_aggregate_before_fetch_hints_fetch(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        assert run_cli(tmp_path / "output", "aggregate", "--period", "2026-07") == 2
        captured = capsys.readouterr()
        assert "payroll fetch" in captured.err

    def test_invalid_period_format(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "DUMMY_TOKEN_FOR_TEST")
        monkeypatch.setattr(cli, "SquareClient", fake_client_factory)
        assert run_cli(tmp_path / "output", "fetch", "--period", "2026/07") == 2
        assert "YYYY-MM" in capsys.readouterr().err
