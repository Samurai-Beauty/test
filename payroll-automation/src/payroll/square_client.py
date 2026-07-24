"""Square API 薄ラッパ(T7)。

読み取り専用のエンドポイント(search / list / get)のみを実装する。
書き込み系APIは本モジュールに存在しない・追加しないこと(構築書0-5)。

リトライ: 429/5xx・通信断は指数バックオフで最大3回リトライし、それでも
失敗したら SquareApiError で明示的に異常終了する(部分データで続行しない)。
"""

from __future__ import annotations

import time
from typing import Callable, Iterable

import httpx

BASE_URL = "https://connect.squareup.com"
_PAGE_LIMIT = 200
_RETRYABLE_STATUS = {429}


class SquareApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None, detail: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class SquareClient:
    """認証トークンは環境変数由来(.env)。ログに個人名・時給を出さないこと。"""

    def __init__(
        self,
        access_token: str,
        *,
        base_url: str = BASE_URL,
        client: httpx.Client | None = None,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not access_token:
            raise SquareApiError("SQUARE_ACCESS_TOKEN が設定されていません(.env を確認)")
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        self._client = client or httpx.Client(base_url=base_url, timeout=30.0)
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------
    # 低レベルリクエスト(リトライ付き)
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, *, json_body: dict | None = None, params: dict | None = None) -> dict:
        last_error: str | None = None
        last_status: int | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                self._sleep(self._backoff_base * (2 ** (attempt - 1)))  # 2s, 4s, 8s
            try:
                response = self._client.request(
                    method, path, json=json_body, params=params, headers=self._headers
                )
            except httpx.TransportError as exc:
                last_error, last_status = f"通信エラー: {exc.__class__.__name__}", None
                continue
            if response.status_code in _RETRYABLE_STATUS or response.status_code >= 500:
                last_error, last_status = f"HTTP {response.status_code}", response.status_code
                continue
            if response.status_code >= 400:
                # 認証エラー等はリトライしても回復しないため即時異常終了
                raise SquareApiError(
                    f"Square APIエラー(HTTP {response.status_code}): {path}",
                    status_code=response.status_code,
                    detail=response.text[:500],
                )
            return response.json()
        raise SquareApiError(
            f"Square APIへのリクエストが{self._max_retries + 1}回失敗しました({last_error}): {path}",
            status_code=last_status,
        )

    # ------------------------------------------------------------------
    # 読み取り専用エンドポイント
    # ------------------------------------------------------------------

    def search_timecards(self, location_ids: Iterable[str], start_at: str, end_at: str) -> list[dict]:
        """POST /v2/labor/timecards/search — 期間・店舗で絞り込み、全ページ結合。"""
        results: list[dict] = []
        cursor: str | None = None
        while True:
            body: dict = {
                "query": {
                    "filter": {
                        "location_ids": list(location_ids),
                        "start": {"start_at": start_at, "end_at": end_at},
                    }
                },
                "limit": _PAGE_LIMIT,
            }
            if cursor:
                body["cursor"] = cursor
            data = self._request("POST", "/v2/labor/timecards/search", json_body=body)
            results.extend(data.get("timecards") or data.get("shifts") or [])
            cursor = data.get("cursor")
            if not cursor:
                return results

    def list_team_member_wages(self) -> list[dict]:
        """GET /v2/labor/team-member-wages — 時給マスタ(全ページ結合)。"""
        results: list[dict] = []
        cursor: str | None = None
        while True:
            params: dict = {"limit": _PAGE_LIMIT}
            if cursor:
                params["cursor"] = cursor
            data = self._request("GET", "/v2/labor/team-member-wages", params=params)
            results.extend(data.get("team_member_wages") or [])
            cursor = data.get("cursor")
            if not cursor:
                return results

    def list_workweek_configs(self) -> list[dict]:
        """GET /v2/labor/workweek-configs — 週起算日設定。"""
        data = self._request("GET", "/v2/labor/workweek-configs")
        return data.get("workweek_configs") or []

    def search_team_members(self, location_ids: Iterable[str]) -> list[dict]:
        """POST /v2/team-members/search — 氏名・在籍情報(退職者含む全件)。"""
        results: list[dict] = []
        cursor: str | None = None
        while True:
            body: dict = {
                "query": {"filter": {"location_ids": list(location_ids)}},
                "limit": _PAGE_LIMIT,
            }
            if cursor:
                body["cursor"] = cursor
            data = self._request("POST", "/v2/team-members/search", json_body=body)
            results.extend(data.get("team_members") or [])
            cursor = data.get("cursor")
            if not cursor:
                return results
