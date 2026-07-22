"""データモデル定義(T2)。

金額は Decimal を使用し、float を禁止する(構築書 2)。
日時はすべてルール指定タイムゾーン(既定 Asia/Tokyo)変換済みの aware datetime。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from .timeutil import JST, to_zone


@dataclass
class Break:
    start_at: datetime
    end_at: datetime | None
    is_paid: bool = False


@dataclass
class Timecard:
    id: str
    team_member_id: str
    location_id: str
    start_at: datetime
    end_at: datetime | None  # None = OPEN(退勤打刻漏れ)
    breaks: list[Break] = field(default_factory=list)
    status: str = "CLOSED"  # OPEN | CLOSED


@dataclass
class DailyWork:
    """1勤務日(始業日基準)の分類結果。同日複数打刻は合算済み。"""

    team_member_id: str
    work_date: date  # 始業日
    location_id: str
    total_minutes: int  # 休憩控除後
    regular_minutes: int
    overtime_minutes: int  # 日8h超 + 週40h超(重複排除済み)
    night_minutes: int  # 22:00-05:00 の実労働重なり
    holiday_minutes: int  # 法定休日労働(時間外とは排他)
    warnings: list[str] = field(default_factory=list)


@dataclass
class MonthlySummary:
    """人別月次集計(丸め適用済み、月60h超の内訳含む)。"""

    team_member_id: str
    period: str
    days_worked: int
    location_ids: list[str]
    total_minutes: int
    regular_minutes: int
    overtime_minutes: int  # 60h超を除いた時間外
    overtime_over60_minutes: int  # 月60時間超の時間外(割増1.50)
    night_minutes: int
    holiday_minutes: int
    holiday_night_minutes: int  # 参考値: 休日×深夜(割増1.60)の重なり
    warnings: list[str] = field(default_factory=list)


@dataclass
class AggregationError:
    """集計除外・停止対象のエラー(承認①で必ず提示する)。"""

    kind: str  # open_timecard | overlap | invalid_timecard
    team_member_id: str
    work_date: date | None
    message: str


@dataclass
class AggregationResult:
    period: str
    daily_works: list[DailyWork]
    summaries: list[MonthlySummary]
    errors: list[AggregationError]


@dataclass
class TeamMember:
    id: str
    display_name: str
    reference_id: str | None = None
    status: str = "ACTIVE"

    @property
    def employee_code(self) -> str:
        """MF給与の従業員コード。Squareのreference_idを第一ソースとする。"""
        return self.reference_id or self.id


@dataclass
class WageInfo:
    team_member_id: str
    hourly_rate_yen: Decimal | None  # None = 単価未設定
    title: str | None = None
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Square APIレスポンス(raw dict)→ モデル変換
# ---------------------------------------------------------------------------


def parse_timecard(raw: dict, tz: ZoneInfo = JST) -> Timecard:
    """Square searchTimecards の1件をJST変換済みTimecardへ。

    新API(timecards)のキーを正、旧Shift API(employee_id / break_entries)も許容。
    """
    member_id = raw.get("team_member_id") or raw.get("employee_id") or ""
    start_at = to_zone(raw["start_at"], tz)
    end_raw = raw.get("end_at")
    end_at = to_zone(end_raw, tz) if end_raw else None

    breaks: list[Break] = []
    for br in raw.get("breaks") or raw.get("break_entries") or []:
        br_end = br.get("end_at")
        breaks.append(
            Break(
                start_at=to_zone(br["start_at"], tz),
                end_at=to_zone(br_end, tz) if br_end else None,
                is_paid=bool(br.get("is_paid", False)),
            )
        )

    status = raw.get("status") or ("CLOSED" if end_at else "OPEN")
    return Timecard(
        id=str(raw.get("id", "")),
        team_member_id=str(member_id),
        location_id=str(raw.get("location_id", "")),
        start_at=start_at,
        end_at=end_at,
        breaks=breaks,
        status=str(status),
    )


def parse_team_member(raw: dict) -> TeamMember:
    family = (raw.get("family_name") or "").strip()
    given = (raw.get("given_name") or "").strip()
    name = f"{family} {given}".strip() or raw.get("id", "")
    return TeamMember(
        id=str(raw.get("id", "")),
        display_name=name,
        reference_id=raw.get("reference_id") or None,
        status=str(raw.get("status", "ACTIVE")),
    )


def parse_wages(raw_wages: list[dict]) -> dict[str, WageInfo]:
    """listTeamMemberWages のレスポンス→ {team_member_id: WageInfo}。

    同一人物に複数の時給が設定されている場合は最高額を採用し警告を付ける
    (迷ったら労働者有利側に倒す)。JPYは最小通貨単位=円。
    """
    by_member: dict[str, WageInfo] = {}
    for raw in raw_wages:
        member_id = str(raw.get("team_member_id") or "")
        if not member_id:
            continue
        rate_obj = raw.get("hourly_rate") or {}
        amount = rate_obj.get("amount")
        rate = Decimal(int(amount)) if amount is not None else None
        title = raw.get("title")

        existing = by_member.get(member_id)
        if existing is None:
            by_member[member_id] = WageInfo(member_id, rate, title)
            continue
        if rate is None:
            continue
        if existing.hourly_rate_yen is None:
            existing.hourly_rate_yen = rate
            existing.title = title
        elif rate != existing.hourly_rate_yen:
            higher = max(rate, existing.hourly_rate_yen)
            existing.warnings.append(
                f"複数の時給設定({existing.hourly_rate_yen}円/{rate}円)があるため高い方 {higher}円 を採用"
            )
            existing.hourly_rate_yen = higher
    return by_member
