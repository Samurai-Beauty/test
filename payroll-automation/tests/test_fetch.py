"""T7: square_client.py / fetch.py のテスト(構築書4.3 F1〜F3)。

httpx.MockTransport でSquare APIをモックする(本番API・実データには接続しない)。
"""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from payroll.fetch import fetch_period, fetch_range, load_raw, parse_raw
from payroll.rules import ConfigError, load_rules
from payroll.square_client import SquareClient, SquareApiError

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
RULES = load_rules(CONFIG_DIR / "payroll_rules.yaml")

LOCATIONS = {"LFAKE001": "テスト店A", "LFAKE002": "テスト店B"}


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_client(handler, sleeps: list | None = None) -> SquareClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, base_url="https://connect.squareup.test")
    return SquareClient(
        "DUMMY_TOKEN_FOR_TEST",
        client=http_client,
        sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
    )


def full_api_handler(request: httpx.Request) -> httpx.Response:
    """全エンドポイントをfixtureで応答する正常系ハンドラ(2ページ構成)。"""
    path = request.url.path
    if path == "/v2/labor/timecards/search":
        body = json.loads(request.content)
        if body.get("cursor") == "PAGE2":
            return httpx.Response(200, json=load_fixture("square_timecards_page2.json"))
        page1 = dict(load_fixture("square_timecards_page1.json"))
        page1["cursor"] = "PAGE2"
        return httpx.Response(200, json=page1)
    if path == "/v2/labor/team-member-wages":
        return httpx.Response(200, json=load_fixture("square_wages.json"))
    if path == "/v2/labor/workweek-configs":
        return httpx.Response(200, json=load_fixture("square_workweek_configs.json"))
    if path == "/v2/team-members/search":
        return httpx.Response(200, json=load_fixture("square_team_members.json"))
    return httpx.Response(404, json={"errors": [{"detail": f"unknown path {path}"}]})


class TestF1Pagination:
    def test_two_pages_combined(self):
        requests: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return full_api_handler(request)

        client = make_client(handler)
        timecards = client.search_timecards(["LFAKE001", "LFAKE002"], "2026-06-29T00:00:00+09:00", "2026-08-01T00:00:00+09:00")
        assert [tc["id"] for tc in timecards] == ["TC_FIX_1", "TC_FIX_2", "TC_FIX_3", "TC_FIX_4"]
        # 1回目はcursorなし、2回目はcursor付きで呼ばれる
        assert "cursor" not in requests[0] and requests[1]["cursor"] == "PAGE2"
        # 絞り込み条件(location・期間)がリクエストに含まれる
        filt = requests[0]["query"]["filter"]
        assert filt["location_ids"] == ["LFAKE001", "LFAKE002"]
        assert filt["start"]["start_at"] == "2026-06-29T00:00:00+09:00"


class TestF2Retry:
    def test_429_then_success(self):
        sleeps: list[float] = []
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] == 1:
                return httpx.Response(429, json={"errors": [{"code": "RATE_LIMITED"}]})
            return full_api_handler(request)

        client = make_client(handler, sleeps)
        wages = client.list_team_member_wages()
        assert len(wages) == 3
        assert sleeps == [2.0]  # 指数バックオフの1回目

    def test_auth_error_fails_immediately_without_retry(self):
        sleeps: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"errors": [{"code": "UNAUTHORIZED"}]})

        client = make_client(handler, sleeps)
        with pytest.raises(SquareApiError) as exc_info:
            client.list_team_member_wages()
        assert exc_info.value.status_code == 401
        assert sleeps == []  # 認証エラーはリトライしない

    def test_empty_token_rejected(self):
        with pytest.raises(SquareApiError, match="SQUARE_ACCESS_TOKEN"):
            SquareClient("")


class TestF3FailureLeavesNoPartialOutput:
    def test_3_retries_then_exception(self, tmp_path):
        sleeps: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"errors": [{"code": "INTERNAL"}]})

        client = make_client(handler, sleeps)
        with pytest.raises(SquareApiError, match="4回失敗"):
            fetch_period(client, RULES, LOCATIONS, "2026-07", tmp_path)
        assert sleeps == [2.0, 4.0, 8.0]  # 指数バックオフで3回リトライ
        # outputに部分ファイルが残らない
        assert list(tmp_path.rglob("*")) == []

    def test_partial_success_still_writes_nothing(self, tmp_path):
        # タイムカード取得は成功、その後の時給マスタ取得が失敗するケース
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v2/labor/team-member-wages":
                return httpx.Response(503, json={"errors": [{"code": "SERVICE_UNAVAILABLE"}]})
            return full_api_handler(request)

        client = make_client(handler)
        with pytest.raises(SquareApiError):
            fetch_period(client, RULES, LOCATIONS, "2026-07", tmp_path)
        assert list(tmp_path.rglob("*")) == []


class TestFetchPeriod:
    def test_happy_path_saves_raw_json(self, tmp_path):
        client = make_client(full_api_handler)
        stats = fetch_period(client, RULES, LOCATIONS, "2026-07", tmp_path)

        assert stats.timecard_count == 4
        assert stats.wage_count == 3
        assert stats.team_member_count == 3
        assert stats.workweek_config_count == 1
        assert stats.warnings == []  # fixtureの週起算日はMONで一致
        assert stats.output_path == tmp_path / "2026-07" / "raw_timecards.json"
        assert stats.output_path.exists()
        assert not (tmp_path / "2026-07" / "raw_timecards.json.tmp").exists()

        payload = load_raw(tmp_path, "2026-07")
        parsed = parse_raw(payload, RULES)
        assert len(parsed.timecards) == 4
        assert parsed.members["TM_A"].display_name == "試験 太郎"
        assert parsed.wages["TM_B"].hourly_rate_yen == 1300

    def test_workweek_mismatch_warned(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v2/labor/workweek-configs":
                return httpx.Response(200, json={"workweek_configs": [{"id": "WW_X", "start_of_week": "SUN"}]})
            return full_api_handler(request)

        client = make_client(handler)
        stats = fetch_period(client, RULES, LOCATIONS, "2026-07", tmp_path)
        assert any("週起算日" in w for w in stats.warnings)

    def test_fetch_range_covers_preceding_week(self):
        # 2026-07-01(水)を含む週の起算日=6/29(月)から、期間末日+1の0:00まで
        start_dt, end_dt = fetch_range("2026-07", RULES)
        assert start_dt.date() == date(2026, 6, 29)
        assert start_dt.isoformat() == "2026-06-29T00:00:00+09:00"
        assert end_dt.isoformat() == "2026-08-01T00:00:00+09:00"

    def test_load_raw_missing_file_hints_fetch(self, tmp_path):
        with pytest.raises(ConfigError, match="payroll fetch"):
            load_raw(tmp_path, "2026-07")
