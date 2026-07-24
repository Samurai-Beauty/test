# 給与計算自動化システム(payroll-automation)

Square の勤怠タイムカードを起点に、**勤怠集計 → 承認用集計表 → MF給与インポートCSV → 検算** までを自動化する社内ツール。対象は Samurai Beauty 3店舗(西新宿本店 / 新宿三丁目 / 渋谷東)の全従業員(アルバイト含む)。

## 基本方針(最重要)

- 本システムの役割は **集計・検算・データ生成に限定**する
- 税・社会保険・雇用保険の**確定計算は MFクラウド給与に委ねる**(料率改定リスクを自前で負わない)
- **振込の実行は必ず人間が行う**(本システムに給与確定・振込機能は存在しない)
- 各工程に**承認ゲート**があり、承認なしに次工程へ進まない(コマンドが分離されており、自動連続実行は存在しない)

詳細は [docs/給与計算自動化_設計書.md](docs/給与計算自動化_設計書.md) / [docs/給与計算自動化_構築書.md](docs/給与計算自動化_構築書.md) を参照。

## セットアップ

```bash
cd payroll-automation
uv venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
source .venv/bin/activate

# uv がない場合
python3 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'
```

### Square トークンの設定

```bash
cp .env.example .env
# .env に読み取り専用トークンを設定(スコープ: TIMECARDS_READ, EMPLOYEES_READ)
```

- **必ず読み取り専用スコープで発行**すること(本システムは search/list のみ使用し、書き込みAPIは実装自体が存在しない)
- `.env` と `output/` は `.gitignore` 済み。**コミット禁止**

## 月次運用手順(コマンド順と承認ゲート)

```
① payroll fetch --period 2026-07
      Squareから取得 → output/2026-07/raw_timecards.json
② payroll aggregate --period 2026-07
      集計 → summary.md / summary.csv / errors.md
      ┌─────────────────────────────────────┐
      │ ★承認ゲート①(サムライ社長)                    │
      │ summary.md の人別労働時間と errors.md を確認。   │
      │ 打刻漏れ等があれば Square側で修正 → ①からやり直し │
      └─────────────────────────────────────┘
③ payroll export-mf --period 2026-07
      → output/2026-07/mf_import.csv を MFクラウド給与に手動インポート
      → MF給与が税・社保・雇用保険を計算
④ MF給与から計算結果CSVをエクスポート
   payroll verify --period 2026-07 --mf-csv <計算結果CSV>
      → output/2026-07/verify_report.md(RESULT: OK / NG)
      ┌─────────────────────────────────────┐
      │ ★承認ゲート②(サムライ社長)                    │
      │ RESULT: OK(差異ゼロ)を確認。NGの間は          │
      │ 原因特定まで給与確定禁止                        │
      └─────────────────────────────────────┘
⑤ MF給与で給与確定 → Web明細自動配信(MF給与機能)
⑥ MF給与から全銀FBデータ出力
      ┌─────────────────────────────────────┐
      │ ★承認ゲート③(サムライ社長)                    │
      │ 振込総額・件数・口座を確認 → 人間が振込実行       │
      └─────────────────────────────────────┘
```

- 各コマンドは独立しており、前工程の出力ファイルがなければ実行できない
- `verify` が NG の場合は終了コード 1 を返す

## 集計ルール(config/payroll_rules.yaml)

| 分類 | 内容 | 割増率 |
|---|---|---|
| 所定内 | 日8h・週40h以内 | 1.00 |
| 時間外 | 日8h超 or 週40h超(二重計上なし) | 1.25 |
| 時間外(月60h超) | 月間の時間外が60hを超えた分 | 1.50 |
| 深夜 | 22:00–05:00 の実労働(加算) | +0.25 |
| 法定休日 | 法定休日の全労働(時間外と排他) | 1.35(深夜と重なると1.60) |

- 暦日を跨ぐ勤務は**始業日の勤務**として計上(月末の夜勤は始業月に帰属)
- 休憩の打刻不備(終了欠落・範囲外)は**控除せず**集計し警告を出す(会社不利・労働者有利側)
- 丸めは `none`(1分単位)または `up`(切り上げ=労働者有利)のみ。切り捨ては設定不可
- 退勤打刻漏れ(OPEN)は集計から除外し errors.md に掲載。タイムカードの時間帯重複は**該当者のみ集計停止**し他の従業員は続行

## 未確定事項(TBD)と設定箇所

すべて config に外部化済み。値の確定後は **configの修正のみ**でよい(コード変更不要)。

| ID | 項目 | 設定箇所 |
|---|---|---|
| TBD-1 | MF給与インポートCSVの列仕様 | `config/mf_import_mapping.yaml`(列順・表記・文字コード)/ verify のMF側列名は `src/payroll/verify.py` 冒頭の定数 |
| TBD-2 | 締め日・支払日 | `config/payroll_rules.yaml` → `period.closing_day`(99=月末) |
| TBD-3 | 丸め単位 | `config/payroll_rules.yaml` → `rounding` |
| TBD-4 | 週起算日 | `config/payroll_rules.yaml` → `workweek.start_day`(fetch がSquare設定と不一致なら警告を出す) |
| TBD-5 | 法定休日の定義 | `config/payroll_rules.yaml` → `legal_holiday.day`(現状は固定曜日方式) |
| TBD-6 | 月給者・固定残業代の有無 | 未実装(現状は時給者のみ検算対象。月給者がいる場合は verify 拡張が必要) |

## テスト

```bash
.venv/bin/pytest                                  # 全件
.venv/bin/pytest --cov=payroll --cov-report=term  # カバレッジ付き
```

- テストはすべてダミーデータ(fixture)で動作し、本番API・実在従業員データには接続しない
- 構築書のテスト仕様(A1〜A17 / V1〜V4 / F1〜F3 / E1〜E3)を全件実装済み

## 実データでの運用テスト(Phase 1 切替前)

1. 読み取り専用の Square 本番トークンを `.env` に設定
2. `payroll fetch --period <直近締め期間>` で実データ取得(標準出力は件数と期間のみ。個人名・時給は出さない)
3. 集計表を従来の手動計算と突合し、差異があれば TBD-2〜5 の設定を修正
4. **1〜2ヶ月は手動計算と並走**し、差異ゼロを確認してから正式切替

## 制限事項・注意

- 週40h判定のため fetch は締め期間前の週起算日から取得する。その際、**前期間末尾の打刻エラー(打刻漏れ等)も errors.md に表示されることがある**(安全側の仕様)
- MF給与側の総支給額に勤怠連動以外の手当・控除が含まれる場合、verify が差異を検出する。TBD-1 確定時に比較対象列を調整すること
- 賞与・年末調整はスコープ外(将来フェーズ)
