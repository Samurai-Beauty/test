# IROHA D'ECOR — Wi-Fi 接続QRコード

店内掲示用の Wi-Fi 接続QRコードと案内カードを生成します。

## 生成

```
pip3 install qrcode pillow opencv-python-headless
python3 generate.py --logo assets/logo.png --password '<暗号化キー>'
```

出力は `out/`（gitignore 済み）。

| ファイル | 内容 |
|---|---|
| `wifi-5g.png` / `wifi-24g.png` | ロゴ入りQRコード（1200px / 誤り訂正 H） |
| `wifi-5g-plain.png` / `wifi-24g-plain.png` | ロゴなしの予備 |
| `card.html` | A6（148×105mm）の印刷用カード。ブラウザで開いて印刷 |

生成時に OpenCV でデコード検証を行い、読み取れない場合はエラーで停止します。

## ロゴについて

`assets/iroha-mark.svg` / `.png` は正規ロゴが手元に無かったため作成した**代替マーク**です。
印刷物に使う前に、正規ロゴを `assets/logo.png`（背景透過）として置き、
`--logo assets/logo.png` を指定して生成し直してください。
`make_mark.py` は代替マークを作り直すときだけ使います。

## ネットワーク設定

SSID は `generate.py` の `NETWORKS` に定義。暗号化キーはリポジトリに残さないため、
実行時に `--password` で渡します。
