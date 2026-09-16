#!/usr/bin/env python3
"""IROHA D'ECOR のシンボルマーク（扇状の5枚葉）を SVG として書き出す。

添付ロゴの画像ファイルが手元に無いため、見た目を再現した代替マーク。
本番用には assets/logo.png に正規ロゴを置き、generate.py に --logo で渡す。
"""
import math
from pathlib import Path

ASSETS = Path(__file__).parent / "assets"

# 正立した1枚の葉の輪郭（原点=根元、上向き、長さL・半幅W の正規化座標）
OUTLINE = [
    # (制御点1, 制御点2, 終点) を w,l の比率で
    ((0.24, 0.32), (0.86, 0.56), (1.00, 0.74)),     # 外側へ膨らむ
    ((0.99, 0.80), (0.74, 0.815), (0.55, 0.862)),   # 肩のくびれ
    ((0.34, 0.912), (0.14, 0.958), (0.00, 1.00)),   # 先端へ
]


def bezier(p0, p1, p2, p3, n):
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        yield (
            u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0],
            u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1],
        )


def half_outline(steps=26):
    """右半分の (x, y) を比率で返す。y は根元0→先端1。"""
    pts = [(0.0, 0.0)]
    cur = (0.0, 0.0)
    for c1, c2, end in OUTLINE:
        pts.extend(bezier(cur, c1, c2, end, steps))
        cur = end
    return pts


def petal(origin, length, width, spread_deg, bend=1.18):
    """根元では垂直、先端に向かって spread_deg まで開く湾曲した葉を作る。"""
    ox, oy = origin
    spread = math.radians(spread_deg)
    right = half_outline()
    ratios = right + [(-x, y) for x, y in reversed(right[:-1])]

    out = []
    for rx, ry in ratios:
        r = ry * length                    # 根元からの距離
        a = spread * (ry ** bend)          # 先端ほど大きく開く
        x = rx * width
        # 半径方向 r・接線方向 x をまとめて角度 a で回す
        out.append((
            ox + r * math.sin(a) + x * math.cos(a),
            oy - r * math.cos(a) + x * math.sin(a),
        ))
    return out


def build(size=1000):
    ox, oy = size / 2, size * 0.90
    # (開き角, 長さ, 半幅) — 外側から中央の順に描いて中央を最前面に
    specs = [
        (-70, 0.505, 0.108),
        (70, 0.505, 0.108),
        (-38, 0.560, 0.120),
        (38, 0.560, 0.120),
        (0, 0.615, 0.130),
    ]
    paths = []
    for spread, ln, w in specs:
        pts = petal((ox, oy), ln * size, w * size, spread)
        d = "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in pts) + " Z"
        paths.append(d)

    body = "\n".join(
        f'    <path d="{d}" fill="url(#silver)" stroke="#ffffff" '
        f'stroke-width="{size * 0.016:.2f}" stroke-linejoin="round"/>'
        for d in paths
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}">
  <defs>
    <linearGradient id="silver" x1="0.12" y1="0.05" x2="0.88" y2="0.95">
      <stop offset="0%" stop-color="#d7d7d7"/>
      <stop offset="38%" stop-color="#a9a9a9"/>
      <stop offset="72%" stop-color="#7d7d7d"/>
      <stop offset="100%" stop-color="#565656"/>
    </linearGradient>
  </defs>
  <g>
{body}
  </g>
</svg>
'''


if __name__ == "__main__":
    ASSETS.mkdir(parents=True, exist_ok=True)
    target = ASSETS / "iroha-mark.svg"
    target.write_text(build(), encoding="utf-8")
    print(f"wrote {target}")
