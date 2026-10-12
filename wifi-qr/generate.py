#!/usr/bin/env python3
"""IROHA D'ECOR — ロゴ入り Wi-Fi 接続QRコードを生成する。

使い方:
    python3 generate.py                      # 同梱の代替マークを使う
    python3 generate.py --logo assets/logo.png   # 正規ロゴを使う（推奨）

出力は out/ 以下。生成後に必ずデコード検証を行い、読み取れなければ失敗する。
"""
import argparse
import base64
import sys
from pathlib import Path

import cv2
import numpy as np
import qrcode
from PIL import Image, ImageDraw
from qrcode.constants import ERROR_CORRECT_H

ROOT = Path(__file__).parent
OUT = ROOT / "out"

NETWORKS = [
    ("5g", "IROHADECOR 5G", "5GHz"),
    ("24g", "IROHADECOR 2.4G", "2.4GHz"),
]

FG = (43, 26, 33)        # ロゴのワードマークに合わせた濃いバーガンディ
BG = (255, 255, 255)


def wifi_payload(ssid: str, password: str, auth: str = "WPA") -> str:
    """WIFI: スキームを組み立てる。\\ ; , : " はエスケープが必要。"""
    def esc(v: str) -> str:
        for ch in ("\\", ";", ",", ":", '"'):
            v = v.replace(ch, "\\" + ch)
        return v

    return f"WIFI:T:{auth};S:{esc(ssid)};P:{esc(password)};;"


def load_logo(path: Path, box: int) -> Image.Image:
    """ロゴを box に収まるよう縮小し、余白をトリムして返す。"""
    logo = Image.open(path).convert("RGBA")

    # 透明でない / 白でない領域だけを残す
    arr = np.array(logo)
    alpha = arr[..., 3]
    non_white = (arr[..., :3] < 245).any(axis=-1)
    mask = (alpha > 8) & non_white
    if mask.any():
        ys, xs = np.where(mask)
        logo = logo.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))

    logo.thumbnail((box, box), Image.LANCZOS)
    return logo


def make_qr(payload: str, logo: Image.Image | None, px: int = 1200) -> Image.Image:
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, border=4)
    qr.add_data(payload)
    qr.make(fit=True)

    modules = qr.modules_count + qr.border * 2
    box_size = max(1, round(px / modules))
    qr.box_size = box_size

    img = qr.make_image(fill_color=FG, back_color=BG).convert("RGBA")
    size = img.size[0]

    if logo is None:
        return img

    # 中央のロゴ台座。誤り訂正 H は約30%まで復元できるが、
    # 面積比は 20% 前後に抑えておく（読み取り機の余裕を残す）。
    plate = round(size * 0.225)
    plate -= plate % box_size          # モジュール境界に合わせる
    pad = round(plate * 0.10)

    base = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(base)
    x0 = (size - plate) // 2
    draw.rounded_rectangle(
        [x0, x0, x0 + plate, x0 + plate],
        radius=round(plate * 0.16),
        fill=BG + (255,),
    )
    img.alpha_composite(base)

    mark = load_logo_box(logo, plate - pad * 2)
    mx = (size - mark.size[0]) // 2
    my = (size - mark.size[1]) // 2
    img.alpha_composite(mark, (mx, my))
    return img


def load_logo_box(logo: Image.Image, box: int) -> Image.Image:
    out = logo.copy()
    out.thumbnail((box, box), Image.LANCZOS)
    return out


def verify(img: Image.Image, expected: str) -> str:
    """生成したQRが実際にデコードできるか確認する。"""
    rgb = np.array(img.convert("RGB"))[:, :, ::-1]
    detector = cv2.QRCodeDetector()
    data, _, _ = detector.detectAndDecode(rgb)
    if data != expected:
        raise SystemExit(
            f"デコード検証に失敗しました。\n  期待値: {expected!r}\n  実測値: {data!r}"
        )
    return data


def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def build_card(cards: list[dict], logo_b64: str, password: str) -> str:
    blocks = "\n".join(
        f'''      <div class="net">
        <div class="band">{c["band"]}</div>
        <img class="qr" src="data:image/png;base64,{c["qr"]}" alt="{c["ssid"]} の接続QRコード">
        <div class="ssid">{c["ssid"]}</div>
      </div>'''
        for c in cards
    )
    return f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>IROHA D'ECOR Wi-Fi</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500&family=Noto+Sans+JP:wght@400;500&display=swap" rel="stylesheet">
<style>
  @page {{ size: 148mm 105mm; margin: 0; }}
  :root {{
    --ink: #2b1a21;
    --muted: #8d7f84;
    --line: #e3dcdd;
    --paper: #ffffff;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; background: #f4f1f0; }}
  body {{ font-family: "Noto Sans JP", sans-serif; color: var(--ink); }}
  .card {{
    width: 148mm; height: 105mm; margin: 12mm auto; background: var(--paper);
    padding: 9mm 10mm; display: flex; flex-direction: column;
    box-shadow: 0 2mm 8mm rgba(0,0,0,.12);
  }}
  .head {{ display: flex; align-items: center; gap: 4mm; padding-bottom: 4mm;
           border-bottom: .3mm solid var(--line); }}
  .head img {{ height: 13mm; }}
  .head .wordmark {{ font-family: "Playfair Display", serif; font-size: 5.4mm;
                     letter-spacing: .12em; line-height: 1.25; }}
  .head .wordmark small {{ display: block; font-size: 2.5mm; letter-spacing: .34em;
                           color: var(--muted); margin-top: .8mm; }}
  .head .title {{ margin-left: auto; text-align: right; }}
  .head .title b {{ font-family: "Playfair Display", serif; font-size: 6mm;
                    letter-spacing: .18em; font-weight: 500; }}
  .head .title span {{ display: block; font-size: 2.6mm; color: var(--muted);
                       letter-spacing: .1em; margin-top: .6mm; }}
  .nets {{ flex: 1; display: flex; gap: 6mm; padding: 5mm 0 0; }}
  .net {{ flex: 1; text-align: center; }}
  .net .band {{ font-size: 2.8mm; letter-spacing: .16em; color: var(--muted); }}
  .net .qr {{ width: 33mm; height: 33mm; display: block; margin: 1.5mm auto; }}
  .net .ssid {{ font-size: 3.2mm; font-weight: 500; letter-spacing: .04em; }}
  .pass {{ border-top: .3mm solid var(--line); margin-top: 3mm; padding-top: 3.5mm;
           display: flex; align-items: baseline; justify-content: center; gap: 3mm; }}
  .pass .label {{ font-size: 2.8mm; color: var(--muted); letter-spacing: .14em; }}
  .pass .value {{ font-family: "Playfair Display", serif; font-size: 7mm;
                  letter-spacing: .3em; }}
  .note {{ text-align: center; font-size: 2.4mm; color: var(--muted); margin-top: 2.5mm;
           line-height: 1.6; }}
  @media print {{
    html, body {{ background: #fff; }}
    .card {{ margin: 0; box-shadow: none; }}
  }}
</style>
</head>
<body>
  <div class="card">
    <div class="head">
      <img src="data:image/png;base64,{logo_b64}" alt="">
      <div class="wordmark">IROHA D'ECOR<small>TOKYO</small></div>
      <div class="title"><b>Wi-Fi</b><span>ご自由にお使いください</span></div>
    </div>
    <div class="nets">
{blocks}
    </div>
    <div class="pass">
      <span class="label">PASSWORD</span>
      <span class="value">{password}</span>
    </div>
    <div class="note">
      スマートフォンのカメラでQRコードを読み取ると接続できます。<br>
      うまくつながらない場合は、上のネットワーク名を選んでパスワードをご入力ください。
    </div>
  </div>
</body>
</html>
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logo", type=Path, default=ROOT / "assets" / "iroha-mark.png",
                    help="QRの中央に置くロゴ画像（PNG/透過推奨）")
    ap.add_argument("--password", required=True,
                    help="Wi-Fi の暗号化キー。リポジトリに残さないため実行時に指定する")
    args = ap.parse_args()

    if not args.logo.exists():
        print(f"ロゴが見つかりません: {args.logo}", file=sys.stderr)
        return 1

    OUT.mkdir(exist_ok=True)
    logo = load_logo(args.logo, 2000)

    cards = []
    for slug, ssid, band in NETWORKS:
        payload = wifi_payload(ssid, args.password)

        plain = make_qr(payload, None)
        plain_path = OUT / f"wifi-{slug}-plain.png"
        plain.save(plain_path)
        verify(plain, payload)

        img = make_qr(payload, logo)
        path = OUT / f"wifi-{slug}.png"
        img.save(path)
        verify(img, payload)

        print(f"OK  {path.name}  {ssid}  ({payload})")
        cards.append({"band": band, "ssid": ssid, "qr": b64(path)})

    card_path = OUT / "card.html"
    card_path.write_text(build_card(cards, b64(args.logo), args.password), encoding="utf-8")
    print(f"OK  {card_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
