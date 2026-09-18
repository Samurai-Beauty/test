#!/usr/bin/env python3
"""B案（大きな一枚QR）を印刷データとして書き出す。

    python3 build_card_b.py --password '<暗号化キー>'

final/ に PDF / PNG / JPEG（300・600dpi）を出力し、
最後に印刷解像度の画像から両方のQRが読めるか検証する。
"""
import argparse
import base64
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

import cv2
import pymupdf
from PIL import Image

ROOT = Path(__file__).parent
FINAL = ROOT / "final"
CHROMIUM = "/opt/pw-browsers/chromium"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# カードで実際に使う文字だけをサブセットで取得し、base64 で埋め込む。
# 印刷時にネットワークへ出ないので、フォントが置き換わる事故が起きない。
FONT_JOBS = [
    ("Playfair+Display:wght@400;500", "IROHA D'ECORTKYFREWi-Fi5GHz2.4PASSWORD20120111"),
    ("Noto+Sans+JP:wght@300;400", "IROHADECOR 5G2.4GHzでつながらない機器はこちら"),
]


def fetch(url: str) -> bytes:
    r = subprocess.run(["curl", "-sS", "-m", "30", "-A", UA, url], capture_output=True)
    if r.returncode != 0:
        sys.exit(f"取得に失敗: {url}\n{r.stderr.decode()}")
    return r.stdout


def embedded_fonts() -> str:
    faces = []
    for family, text in FONT_JOBS:
        css = fetch("https://fonts.googleapis.com/css2?family=" + family
                    + "&text=" + urllib.parse.quote(text)).decode("utf-8")
        blocks = re.findall(r"@font-face\s*\{.*?\}", css, re.S)
        if not blocks:
            sys.exit(f"@font-face が見つかりません: {family}")
        for b in blocks:
            url = re.search(r"url\((https://fonts\.gstatic\.com[^)]+)\)", b).group(1)
            b64 = base64.b64encode(fetch(url)).decode("ascii")
            faces.append(b.replace(url, f"data:font/woff2;base64,{b64}")
                          .replace("font-display: swap;", "font-display: block;"))
    return "\n".join(faces)


def inline_mark() -> str:
    """マークの SVG を実寸の viewBox に切り詰めて返す（PDF でベクターのまま残る）。"""
    svg = (ROOT / "assets" / "iroha-mark.svg").read_text(encoding="utf-8")
    nums = [float(n) for n in
            re.findall(r"-?\d+\.?\d*", " ".join(re.findall(r'd="([^"]+)"', svg)))]
    xs, ys = nums[0::2], nums[1::2]
    pad = 8
    x0, y0 = min(xs) - pad, min(ys) - pad
    w, h = max(xs) - x0 + pad, max(ys) - y0 + pad
    svg = svg.replace('viewBox="0 0 1000 1000" width="1000" height="1000"',
                      f'viewBox="{x0:.1f} {y0:.1f} {w:.1f} {h:.1f}"')
    svg = svg.replace('id="silver"', 'id="silverMark"').replace('url(#silver)', 'url(#silverMark)')
    return svg.strip().replace("<svg ", '<svg class="mark" ')


def build_html(password: str) -> str:
    fonts = embedded_fonts()
    mark = inline_mark()
    qr5 = base64.b64encode((ROOT / "out" / "wifi-5g.png").read_bytes()).decode()
    qr24 = base64.b64encode((ROOT / "out" / "wifi-24g.png").read_bytes()).decode()
    tpl = (ROOT / "card-b.template.html").read_text(encoding="utf-8")
    return (tpl.replace("{{FONTS}}", fonts).replace("{{MARK}}", mark)
               .replace("{{QR5}}", qr5).replace("{{QR24}}", qr24)
               .replace("{{PASSWORD}}", password))


def render(html_path: Path, pdf_path: Path) -> None:
    subprocess.run([CHROMIUM, "--headless", "--disable-gpu", "--no-sandbox",
                    "--hide-scrollbars", "--virtual-time-budget=8000",
                    f"--print-to-pdf={pdf_path}", "--no-pdf-header-footer",
                    f"file://{html_path}"], capture_output=True, check=True)


def rasterize(pdf_path: Path) -> list[Path]:
    page = pymupdf.open(pdf_path)[0]
    made = []
    for dpi in (300, 600):
        png = FINAL / f"{pdf_path.stem}@{dpi}dpi.png"
        page.get_pixmap(dpi=dpi, alpha=False).save(png)
        im = Image.open(png)
        im.save(png, dpi=(dpi, dpi))
        jpg = png.with_suffix(".jpg")
        im.convert("RGB").save(jpg, quality=95, subsampling=0, dpi=(dpi, dpi))
        made += [png, jpg]
        print(f"  {png.name}  {im.size[0]}x{im.size[1]}px")
    return made


def verify(png: Path, password: str) -> None:
    """印刷と同じ解像度の画像から、両方のQRが読めることを確かめる。"""
    want = {f"WIFI:T:WPA;S:IROHADECOR 5G;P:{password};;",
            f"WIFI:T:WPA;S:IROHADECOR 2.4G;P:{password};;"}
    _, decoded, _, _ = cv2.QRCodeDetector().detectAndDecodeMulti(cv2.imread(str(png)))
    missing = want - {d for d in decoded if d}
    if missing:
        sys.exit(f"QRの読み取り検証に失敗しました: {missing}")
    print(f"  デコード検証 OK（{png.name} から2枚とも読み取り）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--password", required=True,
                    help="カードに印刷する暗号化キー")
    args = ap.parse_args()

    FINAL.mkdir(exist_ok=True)
    html = FINAL / "card-b.html"
    html.write_text(build_html(args.password), encoding="utf-8")

    pdf = FINAL / "iroha-wifi-card-B.pdf"
    render(html, pdf)
    print(f"  {pdf.name}")
    made = rasterize(pdf)
    verify(FINAL / f"{pdf.stem}@300dpi.png", args.password)
    print(f"\n{len(made) + 1} ファイルを {FINAL}/ に出力しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
