"""タイムカード・時給マスタ・週設定の取得と保存(T7)。

取得結果は output/{period}/raw_timecards.json に保存し、再集計時にAPIを
再呼び出ししなくて済むようにする。書き込みは全API成功後に一時ファイル経由で
アトミックに行い、失敗時に部分ファイルを残さない(設計書6)。

注意: ログ・標準出力に個人名・時給を出さない(件数と期間のみ)。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path

from .aggregate import week_start_of
from .models import TeamMember, Timecard, WageInfo, parse_team_member, parse_timecard, parse_wages
from .rules import ConfigError, Rules, period_range
from .square_client import SquareClient

SCHEMA_VERSION = 1
RAW_FILENAME = "raw_timecards.json"


@dataclass
class FetchStats:
    period: str
    range_start: str
    range_end: str
    timecard_count: int
    wage_count: int
    team_member_count: int
    workweek_config_count: int
    output_path: Path
    warnings: list[str] = field(default_factory=list)


def fetch_range(period: str, rules: Rules) -> tuple[datetime, datetime]:
    """取得範囲(始業時刻ベース)。週40h判定のため締め期間の前の週起算日から取る。

    夜勤の日跨ぎは始業日基準で期間に帰属するため、終端は期間末日+1日の0:00で足りる。
    """
    period_start, period_end = period_range(period, rules)
    tz = rules.tzinfo
    start_date = week_start_of(period_start, rules)
    start_dt = datetime.combine(start_date, time.min, tzinfo=tz)
    end_dt = datetime.combine(period_end + timedelta(days=1), time.min, tzinfo=tz)
    return start_dt, end_dt


def fetch_period(
    client: SquareClient,
    rules: Rules,
    locations: dict[str, str],
    period: str,
    output_dir: str | Path,
) -> FetchStats:
    """全データ取得 → 検証 → アトミック保存。失敗時は例外(部分出力なし)。"""
    start_dt, end_dt = fetch_range(period, rules)
    start_iso, end_iso = start_dt.isoformat(), end_dt.isoformat()
    location_ids = list(locations.keys())

    timecards = client.search_timecards(location_ids, start_iso, end_iso)
    wages = client.list_team_member_wages()
    workweek_configs = client.list_workweek_configs()
    team_members = client.search_team_members(location_ids)

    warnings: list[str] = []
    for config in workweek_configs:
        square_start = str(config.get("start_of_week", "")).upper()
        if square_start and square_start[:3] != rules.workweek_start_day:
            warnings.append(
                f"Squareの週起算日({square_start})と config の workweek.start_day"
                f"({rules.workweek_start_day})が不一致です。週40h判定に影響するため TBD-4 を確認してください"
            )
            break

    payload = {
        "schema_version": SCHEMA_VERSION,
        "period": period,
        "range": {"start_at": start_iso, "end_at": end_iso},
        "fetched_at": datetime.now(rules.tzinfo).isoformat(),
        "timecards": timecards,
        "team_member_wages": wages,
        "workweek_configs": workweek_configs,
        "team_members": team_members,
    }

    # 全取得成功後にのみ書き込む(一時ファイル → アトミックrename)
    period_dir = Path(output_dir) / period
    period_dir.mkdir(parents=True, exist_ok=True)
    output_path = period_dir / RAW_FILENAME
    tmp_path = period_dir / (RAW_FILENAME + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp_path, output_path)

    return FetchStats(
        period=period,
        range_start=start_iso,
        range_end=end_iso,
        timecard_count=len(timecards),
        wage_count=len(wages),
        team_member_count=len(team_members),
        workweek_config_count=len(workweek_configs),
        output_path=output_path,
        warnings=warnings,
    )


@dataclass
class ParsedRaw:
    period: str
    timecards: list[Timecard]
    members: dict[str, TeamMember]
    wages: dict[str, WageInfo]


def load_raw(output_dir: str | Path, period: str) -> dict:
    path = Path(output_dir) / period / RAW_FILENAME
    if not path.exists():
        raise ConfigError(
            f"取得済みデータがありません: {path}\n先に `payroll fetch --period {period}` を実行してください"
        )
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def parse_raw(payload: dict, rules: Rules) -> ParsedRaw:
    tz = rules.tzinfo
    timecards = [parse_timecard(raw, tz) for raw in payload.get("timecards", [])]
    members = {m.id: m for m in (parse_team_member(raw) for raw in payload.get("team_members", []))}
    wages = parse_wages(payload.get("team_member_wages", []))
    return ParsedRaw(
        period=str(payload.get("period", "")),
        timecards=timecards,
        members=members,
        wages=wages,
    )
