# -*- coding: utf-8 -*-
"""生成图标：绿底圆角方块 + 白色对勾（品牌绿 #008B45）。输出多尺寸 app.ico。

用法：python tools/make_icon.py <out.ico>
"""
import sys
from PIL import Image, ImageDraw

GREEN = (0, 139, 69, 255)
WHITE = (255, 255, 255, 255)
SIZES = [256, 128, 64, 48, 40, 32, 24, 20, 16]


def render(size):
    ss = 8                      # 超采样倍数，缩下来边缘才干净
    n = size * ss
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角方块（半径 22%）
    r = int(n * 0.22)
    d.rounded_rectangle([0, 0, n - 1, n - 1], radius=r, fill=GREEN)

    # 白色对勾
    pts = [(n * 0.27, n * 0.52), (n * 0.43, n * 0.68), (n * 0.74, n * 0.34)]
    w = max(2, int(n * 0.105))
    d.line(pts, fill=WHITE, width=w, joint="curve")
    # 手动画圆头，保证线端是圆的
    for x, y in (pts[0], pts[-1]):
        rr = w / 2
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=WHITE)

    return img.resize((size, size), Image.LANCZOS)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "app.ico"
    imgs = [render(s) for s in SIZES]
    imgs[0].save(out, format="ICO", sizes=[(s, s) for s in SIZES],
                 append_images=imgs[1:])
    print("wrote", out)


if __name__ == "__main__":
    main()
