"""生成应用图标 app.ico（宝石绿圆角方块 + 白色对勾）。

只用 Pillow 画几何图形，不依赖任何外部素材。
"""
import os

from PIL import Image, ImageDraw

GREEN = (0, 139, 69, 255)        # #008B45
GREEN_DARK = (0, 105, 52, 255)   # #006934
WHITE = (255, 255, 255, 255)

SIZE = 512          # 先画大图再缩，边缘更干净
SS = 4              # 超采样倍数


def make(size=SIZE, ss=SS):
    n = size * ss
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角方块（留一点边距，避免顶到画布边缘）
    pad = int(n * 0.045)
    radius = int(n * 0.22)
    d.rounded_rectangle((pad, pad, n - pad, n - pad), radius=radius, fill=GREEN)

    # 白色对勾
    w = int(n * 0.105)                       # 线宽
    pts = [(int(n * 0.27), int(n * 0.52)),
           (int(n * 0.44), int(n * 0.69)),
           (int(n * 0.74), int(n * 0.34))]
    d.line(pts, fill=WHITE, width=w, joint="curve")
    # 两端补圆头
    for x, y in (pts[0], pts[-1]):
        d.ellipse((x - w // 2, y - w // 2, x + w // 2, y + w // 2), fill=WHITE)

    return img.resize((size, size), Image.LANCZOS)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(os.path.dirname(here), "app.ico")
    big = make()
    big.save(out, format="ICO",
             sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print("已生成:", out, os.path.getsize(out), "bytes")
    # 顺便存一张 png 方便预览
    png = os.path.join(here, "_icon-preview.png")
    big.save(png)
    print("预览:", png)


if __name__ == "__main__":
    main()
