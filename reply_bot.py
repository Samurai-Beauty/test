"""
GBP（Googleビジネスプロフィール）クチコミ自動返信システム

サムライ美容室3店舗（西新宿本店 / 新宿三丁目 / 渋谷東店）のクチコミに対して、
Claude API（Opus 4.7）で自然な日本語の返信を生成し、Selenium経由でGBPに投稿する。

注意:
  - Selenium による GBP 操作は Google の利用規約に抵触する可能性があります。
    本番運用では Google Business Profile API の利用を推奨します。
  - 初回ログインは手動で行い、Chromeプロフィールにセッションを保存してください
    （--user-data-dir で参照）。2段階認証や reCAPTCHA を Selenium で突破することはしません。

必要なパッケージ:
  pip install anthropic selenium webdriver-manager python-dotenv

環境変数（.env）:
  ANTHROPIC_API_KEY=sk-ant-...
  CHROME_USER_DATA_DIR=/path/to/chrome/profile
  CHROME_PROFILE_NAME=Default
  GBP_LOCATIONS=西新宿本店:loc_id_1,新宿三丁目:loc_id_2,渋谷東店:loc_id_3
  REPLY_LOG_PATH=./replies.jsonl
  DRY_RUN=true   # true のあいだは投稿せずログのみ

使い方:
  python reply_bot.py            # 全店舗を1回処理
  python reply_bot.py --store 西新宿本店
  python reply_bot.py --loop 3600  # 1時間ごとに繰り返し
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import anthropic
from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

load_dotenv()

CLAUDE_MODEL = "claude-opus-4-7"
MAX_REPLY_TOKENS = 1024
REVIEW_FETCH_TIMEOUT = 30
SUBMIT_TIMEOUT = 20
PER_REVIEW_DELAY_SEC = 4  # 連投防止

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("reply_bot")


# ---------------------------------------------------------------------------
# サロン情報・プロンプト
# ---------------------------------------------------------------------------

SALON_BRAND = "サムライ美容室"

STORE_PROFILES: dict[str, str] = {
    "西新宿本店": (
        "西新宿小滝橋通り沿いの本店。落ち着いた大人向けの空間で、"
        "メンズカット・ビジネスマン向けのスタイリングが得意。"
    ),
    "新宿三丁目": (
        "新宿三丁目駅から徒歩圏。トレンドに敏感な20〜30代の来店が多く、"
        "デザインカラーやパーマ提案に強み。"
    ),
    "渋谷東店": (
        "渋谷東エリアの新店舗。若年層中心で、最新のヘアトレンドを"
        "取り入れたメニューを展開中。"
    ),
}

REPLY_SYSTEM_PROMPT = """あなたはサムライ美容室のオーナー兼カスタマーサポート担当です。
Googleビジネスプロフィールに投稿されたクチコミに対し、丁寧で温かみのある返信を作成します。

【返信のルール】
1. 文体は「です・ます調」で統一し、絵文字は使わない。
2. 1回の返信は150〜300文字程度。長すぎず、読みやすく。
3. 必ず冒頭で「サムライ美容室○○店」と店舗名を入れて感謝を述べる。
4. クチコミの内容に具体的に触れる(例: 担当スタイリスト名、メニュー名、コメント内容)。
   ただしクチコミに書かれていない情報を捏造しない。
5. ★3以下のネガティブな評価には、お詫びを最初に置き、改善への姿勢を示す。
   個別対応が必要な場合は店舗への連絡を案内する(電話番号・URLは出力しない)。
6. 個人情報・誹謗中傷・差別的表現が含まれていれば、返信を生成せず文字列
   "SKIP_REVIEW: <理由>" のみを返す。
7. 過度な営業文句、次回割引の約束、再来店への強い勧誘は避ける。
   さりげなく「またのお越しをお待ちしております」程度に留める。
8. 返信本文のみを出力し、前置きや説明、見出しは付けない。

【トーン】
- 誠実、簡潔、店舗の人柄が伝わる温かさ。
- 機械的・テンプレ的にならないよう、各クチコミに固有の文脈を1点以上盛り込む。
"""


# ---------------------------------------------------------------------------
# データモデル
# ---------------------------------------------------------------------------


@dataclass
class Review:
    store: str
    review_id: str
    author: str
    rating: int  # 1-5
    text: str
    posted_at: str  # ISO 8601 文字列(GBPから取得した表示時刻)


@dataclass
class ReplyResult:
    review: Review
    reply_text: str | None
    skipped_reason: str | None
    posted: bool
    timestamp: str


# ---------------------------------------------------------------------------
# Claude API: 返信生成
# ---------------------------------------------------------------------------


class ReplyGenerator:
    """Claude Opus 4.7 を使ってクチコミ返信を生成する。

    システムプロンプトは Prompt Caching でキャッシュし、店舗ごとの繰り返し
    呼び出しでコストを削減する。
    """

    def __init__(self, client: anthropic.Anthropic) -> None:
        self.client = client

    def generate(self, review: Review) -> tuple[str | None, str | None]:
        """返信本文を生成。

        Returns:
            (reply_text, skipped_reason): いずれか一方が None。
            返信を生成すべきでない場合は reply_text=None, skipped_reason=理由。
        """
        store_context = STORE_PROFILES.get(review.store, "")

        user_prompt = (
            f"【店舗】{SALON_BRAND} {review.store}\n"
            f"【店舗の特徴】{store_context}\n"
            f"【クチコミ評価】★{review.rating}\n"
            f"【投稿者名】{review.author}\n"
            f"【投稿日時】{review.posted_at}\n"
            f"【クチコミ本文】\n{review.text}\n\n"
            "上記のクチコミへの返信を、ルールに従って作成してください。"
        )

        try:
            response = self.client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=MAX_REPLY_TOKENS,
                thinking={"type": "adaptive"},
                system=[
                    {
                        "type": "text",
                        "text": REPLY_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_prompt}],
            )
        except anthropic.APIStatusError as e:
            log.error("Claude API エラー (%s): %s", e.status_code, e.message)
            return None, f"API_ERROR: {e.status_code}"
        except anthropic.APIConnectionError:
            log.error("Claude API 接続エラー")
            return None, "API_CONNECTION_ERROR"

        text = next(
            (b.text for b in response.content if b.type == "text"), ""
        ).strip()

        if text.startswith("SKIP_REVIEW:"):
            reason = text.removeprefix("SKIP_REVIEW:").strip()
            log.info("[%s] スキップ判定: %s", review.review_id, reason)
            return None, reason

        if not text:
            return None, "EMPTY_OUTPUT"

        log.debug(
            "[%s] cache_read=%d, cache_creation=%d, output=%d tokens",
            review.review_id,
            response.usage.cache_read_input_tokens or 0,
            response.usage.cache_creation_input_tokens or 0,
            response.usage.output_tokens,
        )
        return text, None


# ---------------------------------------------------------------------------
# Selenium: GBP 操作
# ---------------------------------------------------------------------------


class GBPReviewClient:
    """Selenium経由でGBPのクチコミ管理画面を操作する。

    前提:
      - 指定した Chrome プロフィールに Google アカウントでログイン済みであること。
      - 2段階認証は事前に通過させ、セッションをプロフィールに保存しておく。
    """

    REVIEWS_URL_TEMPLATE = (
        "https://business.google.com/n/{location_id}/reviews"
    )

    # GBPのDOM構造は変わりやすいため、セレクタは設定として外出し可能にする。
    # 環境変数 GBP_SELECTORS_JSON で上書き可。
    DEFAULT_SELECTORS: dict[str, str] = {
        "review_card": "[data-review-id]",
        "review_id_attr": "data-review-id",
        "author_name": "[data-author-name]",
        "rating_stars": "[aria-label*='星']",
        "review_text": "[data-review-text]",
        "posted_time": "[data-review-time]",
        "reply_button": "button[aria-label*='返信']",
        "reply_textarea": "textarea[aria-label*='返信']",
        "submit_button": "button[aria-label*='送信'], button[aria-label*='公開']",
        "reply_existing": "[data-owner-reply]",  # 返信済みカードの目印
    }

    def __init__(
        self,
        user_data_dir: str,
        profile_name: str = "Default",
        headless: bool = False,
    ) -> None:
        self.user_data_dir = user_data_dir
        self.profile_name = profile_name
        self.headless = headless
        self._driver: webdriver.Chrome | None = None
        self.selectors = self._load_selectors()

    def _load_selectors(self) -> dict[str, str]:
        override = os.environ.get("GBP_SELECTORS_JSON")
        if not override:
            return dict(self.DEFAULT_SELECTORS)
        try:
            custom = json.loads(override)
            merged = dict(self.DEFAULT_SELECTORS)
            merged.update(custom)
            return merged
        except json.JSONDecodeError as e:
            log.warning("GBP_SELECTORS_JSON のパース失敗: %s。既定値を使用", e)
            return dict(self.DEFAULT_SELECTORS)

    def __enter__(self) -> "GBPReviewClient":
        options = Options()
        options.add_argument(f"--user-data-dir={self.user_data_dir}")
        options.add_argument(f"--profile-directory={self.profile_name}")
        options.add_argument("--lang=ja-JP")
        options.add_argument("--window-size=1280,1024")
        if self.headless:
            options.add_argument("--headless=new")
        # 自動操作検知の緩和(完全な回避はせず、最低限の見栄え調整のみ)
        options.add_experimental_option(
            "excludeSwitches", ["enable-automation"]
        )
        options.add_experimental_option("useAutomationExtension", False)

        service = Service(ChromeDriverManager().install())
        self._driver = webdriver.Chrome(service=service, options=options)
        self._driver.set_page_load_timeout(60)
        return self

    def __exit__(self, *_: object) -> None:
        if self._driver is not None:
            self._driver.quit()
            self._driver = None

    @property
    def driver(self) -> webdriver.Chrome:
        if self._driver is None:
            raise RuntimeError("GBPReviewClient はコンテキストマネージャで使用してください")
        return self._driver

    def fetch_unanswered_reviews(
        self, store_name: str, location_id: str
    ) -> list[Review]:
        """指定店舗の未返信クチコミ一覧を取得する。"""
        url = self.REVIEWS_URL_TEMPLATE.format(location_id=location_id)
        log.info("[%s] クチコミページを開きます: %s", store_name, url)
        self.driver.get(url)

        try:
            WebDriverWait(self.driver, REVIEW_FETCH_TIMEOUT).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, self.selectors["review_card"])
                )
            )
        except TimeoutException:
            log.warning("[%s] クチコミカードが表示されませんでした", store_name)
            return []

        cards = self.driver.find_elements(
            By.CSS_SELECTOR, self.selectors["review_card"]
        )
        reviews: list[Review] = []
        for card in cards:
            # 既に返信済みのカードはスキップ
            existing_reply = card.find_elements(
                By.CSS_SELECTOR, self.selectors["reply_existing"]
            )
            if existing_reply:
                continue

            try:
                review = self._parse_card(card, store_name)
            except (NoSuchElementException, ValueError) as e:
                log.debug("カードのパース失敗: %s", e)
                continue
            reviews.append(review)

        log.info("[%s] 未返信クチコミ %d 件", store_name, len(reviews))
        return reviews

    def _parse_card(self, card, store_name: str) -> Review:
        review_id = card.get_attribute(self.selectors["review_id_attr"]) or ""
        if not review_id:
            raise ValueError("review_id 取得失敗")

        author = card.find_element(
            By.CSS_SELECTOR, self.selectors["author_name"]
        ).text.strip()

        rating_el = card.find_element(
            By.CSS_SELECTOR, self.selectors["rating_stars"]
        )
        rating = self._extract_rating(rating_el.get_attribute("aria-label") or "")

        text = card.find_element(
            By.CSS_SELECTOR, self.selectors["review_text"]
        ).text.strip()

        posted_at = card.find_element(
            By.CSS_SELECTOR, self.selectors["posted_time"]
        ).text.strip()

        return Review(
            store=store_name,
            review_id=review_id,
            author=author,
            rating=rating,
            text=text,
            posted_at=posted_at,
        )

    @staticmethod
    def _extract_rating(aria_label: str) -> int:
        # 例: "5つ星のうち4個" "星4個"
        for ch in aria_label:
            if ch.isdigit():
                return int(ch)
        return 0

    def post_reply(self, review: Review, reply_text: str) -> bool:
        """指定クチコミに返信を投稿する。成功なら True。"""
        try:
            card = self.driver.find_element(
                By.CSS_SELECTOR,
                f"[{self.selectors['review_id_attr']}='{review.review_id}']",
            )
        except NoSuchElementException:
            log.error("[%s] 返信対象のカードが見つかりません", review.review_id)
            return False

        try:
            reply_btn = card.find_element(
                By.CSS_SELECTOR, self.selectors["reply_button"]
            )
            reply_btn.click()

            textarea = WebDriverWait(self.driver, SUBMIT_TIMEOUT).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, self.selectors["reply_textarea"])
                )
            )
            textarea.clear()
            textarea.send_keys(reply_text)

            submit_btn = WebDriverWait(self.driver, SUBMIT_TIMEOUT).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, self.selectors["submit_button"])
                )
            )
            submit_btn.click()
        except (TimeoutException, NoSuchElementException, WebDriverException) as e:
            log.error("[%s] 返信投稿に失敗: %s", review.review_id, e)
            return False

        # 送信完了を待つ(textareaが消えるか、reply_existing が現れるか)
        try:
            WebDriverWait(self.driver, SUBMIT_TIMEOUT).until(
                EC.invisibility_of_element_located(
                    (By.CSS_SELECTOR, self.selectors["reply_textarea"])
                )
            )
        except TimeoutException:
            log.warning("[%s] 送信後の状態遷移を確認できず", review.review_id)
            return False

        return True


# ---------------------------------------------------------------------------
# 永続化
# ---------------------------------------------------------------------------


class ReplyLog:
    """JSON Lines 形式で返信履歴を追記保存する。

    重複投稿防止のため、過去に処理済みの review_id を起動時にロードする。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._processed_ids = self._load_processed_ids()

    def _load_processed_ids(self) -> set[str]:
        if not self.path.exists():
            return set()
        ids: set[str] = set()
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                rid = entry.get("review", {}).get("review_id")
                if rid:
                    ids.add(rid)
            except json.JSONDecodeError:
                continue
        log.info("過去ログから %d 件の処理済み review_id を読み込み", len(ids))
        return ids

    def is_processed(self, review_id: str) -> bool:
        return review_id in self._processed_ids

    def append(self, result: ReplyResult) -> None:
        record = {
            "review": asdict(result.review),
            "reply_text": result.reply_text,
            "skipped_reason": result.skipped_reason,
            "posted": result.posted,
            "timestamp": result.timestamp,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._processed_ids.add(result.review.review_id)


# ---------------------------------------------------------------------------
# メインフロー
# ---------------------------------------------------------------------------


def parse_locations(env_value: str) -> dict[str, str]:
    """`西新宿本店:loc_id_1,新宿三丁目:loc_id_2` を辞書に変換。"""
    locations: dict[str, str] = {}
    for pair in env_value.split(","):
        if ":" not in pair:
            continue
        name, loc_id = pair.split(":", 1)
        locations[name.strip()] = loc_id.strip()
    return locations


def process_store(
    store_name: str,
    location_id: str,
    gbp: GBPReviewClient,
    generator: ReplyGenerator,
    reply_log: ReplyLog,
    dry_run: bool,
) -> Iterator[ReplyResult]:
    reviews = gbp.fetch_unanswered_reviews(store_name, location_id)
    for review in reviews:
        if reply_log.is_processed(review.review_id):
            log.debug("[%s] 過去に処理済み。スキップ", review.review_id)
            continue

        log.info(
            "[%s] %s ★%d: %s...",
            review.review_id,
            review.author,
            review.rating,
            review.text[:40].replace("\n", " "),
        )

        reply_text, skipped = generator.generate(review)
        posted = False
        if reply_text and not dry_run:
            posted = gbp.post_reply(review, reply_text)
            time.sleep(PER_REVIEW_DELAY_SEC)
        elif reply_text and dry_run:
            log.info("[DRY_RUN] 返信案:\n%s", reply_text)

        result = ReplyResult(
            review=review,
            reply_text=reply_text,
            skipped_reason=skipped,
            posted=posted,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        reply_log.append(result)
        yield result


def run_once(
    target_store: str | None,
    locations: dict[str, str],
    gbp: GBPReviewClient,
    generator: ReplyGenerator,
    reply_log: ReplyLog,
    dry_run: bool,
) -> None:
    targets = (
        {target_store: locations[target_store]}
        if target_store
        else locations
    )
    for store_name, location_id in targets.items():
        log.info("=== %s の処理を開始 ===", store_name)
        try:
            results = list(
                process_store(
                    store_name,
                    location_id,
                    gbp,
                    generator,
                    reply_log,
                    dry_run,
                )
            )
        except WebDriverException as e:
            log.error("[%s] ブラウザ操作中にエラー: %s", store_name, e)
            continue

        posted_n = sum(1 for r in results if r.posted)
        skipped_n = sum(1 for r in results if r.skipped_reason)
        log.info(
            "=== %s 完了: 処理 %d / 投稿 %d / スキップ %d ===",
            store_name,
            len(results),
            posted_n,
            skipped_n,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="GBP クチコミ自動返信ボット")
    parser.add_argument(
        "--store", help="特定の店舗名のみ処理(例: 西新宿本店)"
    )
    parser.add_argument(
        "--loop",
        type=int,
        default=0,
        help="指定秒数間隔で繰り返し実行(0=1回のみ)",
    )
    parser.add_argument(
        "--headless", action="store_true", help="ヘッドレスモードで起動"
    )
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY が設定されていません")
        return 1

    user_data_dir = os.environ.get("CHROME_USER_DATA_DIR")
    if not user_data_dir:
        log.error("CHROME_USER_DATA_DIR が設定されていません")
        return 1

    locations_env = os.environ.get("GBP_LOCATIONS", "")
    locations = parse_locations(locations_env)
    if not locations:
        log.error("GBP_LOCATIONS が未設定または不正です")
        return 1

    if args.store and args.store not in locations:
        log.error(
            "店舗 '%s' が GBP_LOCATIONS に存在しません。利用可能: %s",
            args.store,
            list(locations.keys()),
        )
        return 1

    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    if dry_run:
        log.warning("DRY_RUN モード: 実際の投稿は行いません")

    log_path = Path(os.environ.get("REPLY_LOG_PATH", "./replies.jsonl"))
    reply_log = ReplyLog(log_path)
    profile_name = os.environ.get("CHROME_PROFILE_NAME", "Default")

    client = anthropic.Anthropic(api_key=api_key)
    generator = ReplyGenerator(client)

    while True:
        with GBPReviewClient(
            user_data_dir=user_data_dir,
            profile_name=profile_name,
            headless=args.headless,
        ) as gbp:
            run_once(args.store, locations, gbp, generator, reply_log, dry_run)

        if args.loop <= 0:
            break
        log.info("%d 秒後に再実行します", args.loop)
        time.sleep(args.loop)

    return 0


if __name__ == "__main__":
    sys.exit(main())
