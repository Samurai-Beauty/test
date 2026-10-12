# IROHA D'ECOR — Wi-Fi 接続QRコード

店内掲示用の Wi-Fi 接続QRコードと案内カードを生成します。

## 生成

```
pip3 install qrcode pillow opencv-python-headless
python3 generate.py --logo assets/logo.png --password '<暗号化キー>'
python3 build_card_b.py --password '<暗号化キー>'      # B案の印刷データ
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

## 採用案（B｜大きな一枚QR）

`build_card_b.py` が `final/` に印刷データを書き出します（gitignore 済み）。

| ファイル | 用途 |
|---|---|
| `iroha-wifi-card-B.pdf` | A6（105×148mm）。文字とマークはベクター。入稿・印刷用 |
| `...@300dpi.png` / `.jpg` | 1242×1750px。家庭用プリンタ・コンビニ印刷 |
| `...@600dpi.png` / `.jpg` | 2484×3500px。大きめに印刷する場合 |

Playfair Display と Noto Sans JP は使用文字だけをサブセット化して埋め込んでいるため、
印刷時にネットワーク接続は不要で、フォントが置き換わりません。
出力後、300dpi 画像から2枚のQRが読めることを自動検証します。
