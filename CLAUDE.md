# Samurai System — プロジェクト概要

## 未完成・保留中の機能

### 給与計算自動化システム（`payroll-automation/`）
- ブランチ: `claude/payroll-system-design-build-zdy2sp`
- 内容: Square勤怠 → 集計（残業・深夜・休日・月60h超）→ 承認 → MF給与インポートCSV → 検算 のPython CLI
- 設計書・構築書: `payroll-automation/docs/`、運用手順: `payroll-automation/README.md`
- TBD-1〜6（締め日・丸め・MF列仕様・法定休日など）は `payroll-automation/config/` に外部化済み。確定したらconfigのみ修正
- 実データ疎通には読み取り専用Squareトークン（TIMECARDS_READ / EMPLOYEES_READ）を `.env` に設定

### GBP クチコミ自動返信ボット（PR #1）
- ブランチ: `claude/show-reply-bot-code-qrmb2`
- ファイル: `reply_bot.py`
- 内容: Selenium で Google ビジネスプロフィールの口コミ画面を操作し、Claude API（Opus 4.7）で日本語返信文を自動生成
- 対象: 西新宿本店・新宿三丁目・渋谷東店
- ステータス: Draft PR — 開発継続予定
- 次回セッションで続きを実装すること

## 概要
Samurai Beauty（美容院）スタッフ向け社内管理システム。
単一ファイル（`index.html`）で完結。GitHub Pages でホスティング。

**URL**: `https://samurai-beauty.github.io/test/`

## 技術スタック
- フロントエンド: 素のHTML/CSS/JavaScript（フレームワークなし）
- ストレージ: Supabase（`ifiamddyhbbrseglqesg.supabase.co`）+ localStorage（キャッシュ）
- ホスティング: GitHub Pages（`Samurai-Beauty/test` リポジトリ）

## Supabase テーブル
- `sales_data` — 売上・流入分析データ（`store_key` + `data_json` + `uploaded_at` + `uploaded_by`）
- `reservations` — 予約データ
- `suggestions` — ご意見箱

## 店舗構成
```javascript
const STORE_MEMBERS = {
  honsha:       ['辰巳大地', '齋藤香織', 'アシスタント', '宇田晃平', 'LISHチーム'],
  nishishinjuku: [...],  // 西新宿小滝橋通り店
  sanchome:      [...],  // 新宿三丁目店
  shibuya:       [...],  // 渋谷東店
};
```

## 主要機能
| タブ | 説明 |
|---|---|
| ホーム | ダッシュボード・お知らせ |
| 売上管理 | CSV アップロード → Supabase 保存 |
| シフト管理 | シフト提出・管理・閲覧・交換 |
| スタイリスト管理 | メンバー管理（本社のみ） |
| 流入分析 | LINE友達数・新規客数・サブスク契約数（本社・FCオーナーのみ） |
| ご意見箱 | 匿名投稿・返信 |
| フォト | 施術写真ギャラリー |

## 流入分析
- **LINE友達**（全店舗合計）: `inflow_line_${y}_${m}` → `{ line_total, line_add }`
- **店舗別**: `inflow_${storeKey}_${y}_${m}` → `{ new_client, subscribe }`
- エルメMCP（bot_id: `aRo9dx`, form_id: `201419`）から月次取得
- Claude.ai（エルメ接続済み）でプロンプトを実行 → JSON をシステムに貼り付けてインポート

## エルメ連携 GAS スクリプト
`samurai_karte_sync_gas.js` — Google Apps Script で使用
必要なスクリプトプロパティ:
- `ANTHROPIC_API_KEY`
- `ELME_OAUTH_TOKEN`
- `SUPABASE_SERVICE_KEY`
- `ERROR_EMAIL`

## シフト締め切り設定
```javascript
const SHIFT_DEADLINE_OVERRIDES = {
  '2026-6': new Date(2026, 4, 3, 23, 59, 59, 999),
};
const SHIFT_DEADLINE_USER_OVERRIDES = {
  '2026-6': { '三浦さら': new Date(2026, 4, 5, 23, 59, 59, 999) },
};
```

## 権限ロール
- `isMgr`: 本社 + FCオーナー（管理機能にアクセス可）
- `isFCOwner()`: FCオーナー判定（自店舗のみ編集可）
- 本社は全店舗を閲覧・編集可能

## 開発フロー
1. Claude Code セッションで `claude/` ブランチに変更をコミット・プッシュ
2. GitHub で PR を作成 → `main` にマージ
3. GitHub Pages が自動更新される

## ログイン
- デフォルトパスワード: `Samurai.2023`
- パスワードはコード内 `DEFAULT_PASSWORDS` に定義（本番前にSupabase認証への移行推奨）
