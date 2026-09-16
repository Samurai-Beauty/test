# ルーターの SSID / 暗号化キー変更

このリポジトリを操作している Claude Code（クラウド実行）からは、店舗内のルーター
（`192.168.11.1`）には到達できない。変更は **店舗内の PC 上で動くエージェント**、
または手作業で行う。

## 変更内容

| 項目 | 変更前（本体ラベル） | 変更後 |
|---|---|---|
| SSID (5GHz) | Buffalo-5G-3C20 | IROHADECOR 5G |
| SSID (2.4GHz) | Buffalo-2G-3C20 | IROHADECOR 2.4G |
| 暗号化キー | 本体ラベル記載 | （別途指示） |

## A. 手作業（最短・3分）

1. PC を現在の Wi-Fi（`Buffalo-5G-3C20`、キーはラベル）か LAN ケーブルで接続
2. ブラウザで `http://192.168.11.1`（つながらなければ `http://192.168.1.1`）
3. ユーザー名 `admin` / パスワード `password`（機種によってはラベルの setup 用パスワード）
4. 詳細設定 → 無線設定
5. 「バンドステアリング（SSID統合）」が ON なら OFF
6. 5GHz: SSID と暗号化キーを入力 → 設定
7. 2.4GHz: 同様に入力 → 設定
8. 再起動後、各端末を新 SSID へ接続し直す

## B. 店舗の PC で Claude Code に実行させる

店舗の PC（ルーターと同じネットワーク）で:

```bash
npm install -g @anthropic-ai/claude-code
mkdir router-change && cd router-change
claude
```

起動後、`PROMPT.md` の内容を貼り付ける。エージェントは LAN 内から管理画面を
ブラウザ操作で変更できる。実行前に管理画面のスクリーンショットを撮って確認させる。
