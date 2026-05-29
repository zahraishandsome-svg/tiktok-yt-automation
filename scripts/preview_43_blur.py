"""
Preview script — generates a synthetic 4:3 blurred-fill frame so you can
approve the blur strength BEFORE any real video is touched.

Creates a synthetic 9:16 "TikTok-style" test image (gradient + text blocks)
then applies the exact same blur logic that video_converter.py will use for
real videos, and saves the result as preview_43_blur.jpg next to this script.

Usage: python scripts/preview_43_blur.py [--sigma N]
  --sigma N   Gaussian blur radius (default: 25, same as production)
"""

import sys
import argparse
from pathlib import Path
from PIL import Image, ImageFilter, ImageDraw, ImageFont

OUT_DIR = Path(__file__).parent
OUT_FILE = OUT_DIR / "preview_43_blur.jpg"

# ── Output frame size ────────────────────────────────────────────────────────
OUT_W = 1440   # 4:3 horizontal
OUT_H = 1080


def make_synthetic_vertical(width: int = 720, height: int = 1280) -> Image.Image:
    """
    Create a synthetic 9:16 vertical image that looks like a TikTok video still.
    Gradient background + coloured blocks to give the blur something interesting.
    """
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)

    # Gradient sky-to-earth background
    for y in range(height):
        t = y / height
        r = int(30  + t * 120)
        g = int(80  + t * 100)
        b = int(200 - t * 150)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # A "face / subject" area — centred oval, warm skin tone
    face_cx, face_cy = width // 2, height // 3
    draw.ellipse(
        [face_cx - 140, face_cy - 180, face_cx + 140, face_cy + 180],
        fill=(220, 160, 110),
    )

    # Hair
    draw.ellipse(
        [face_cx - 140, face_cy - 230, face_cx + 140, face_cy - 130],
        fill=(60, 30, 10),
    )

    # Shoulders / clothing block
    draw.rectangle(
        [face_cx - 200, face_cy + 150, face_cx + 200, height - 100],
        fill=(50, 50, 160),
    )

    # Text overlay strip at bottom (simulates caption area)
    draw.rectangle([0, height - 180, width, height], fill=(0, 0, 0, 200))
    try:
        font = ImageFont.load_default(size=28)
    except TypeError:
        font = ImageFont.load_default()
    draw.text((20, height - 160), "@tiktok_creator  ♬ Trending Sound", fill="white", font=font)
    draw.text((20, height - 100), "day in my life #grwm #ootd #fashion", fill=(200, 200, 200), font=font)

    # Simulated "like/comment" UI on the right side
    for i, (emoji_text, y_pos) in enumerate([("♥ 120K", height // 2 - 60),
                                              ("💬 3.2K", height // 2 + 20),
                                              ("↗ 8.1K", height // 2 + 100)]):
        draw.rectangle([width - 80, y_pos, width - 10, y_pos + 60], fill=(0, 0, 0))
        draw.text((width - 75, y_pos + 10), emoji_text, fill="white", font=font)

    return img


def apply_43_blurred_fill(src: Image.Image, sigma: int = 25) -> Image.Image:
    """
    Exact Python equivalent of the ffmpeg pipeline:
      1. Scale src to fill 1440×1080 (crop to fit, then blur)
      2. Gaussian blur the background layer
      3. Scale src to fit inside 1440×1080 (letter-box, no crop)
      4. Paste centred on top of the blurred background
    """
    src_w, src_h = src.size

    # ── Background: scale-to-fill (cover) + blur ──────────────────────────────
    fill_scale = max(OUT_W / src_w, OUT_H / src_h)
    fill_w = int(src_w * fill_scale)
    fill_h = int(src_h * fill_scale)
    bg = src.resize((fill_w, fill_h), Image.LANCZOS)

    # Crop to exact output size (centred)
    left = (fill_w - OUT_W) // 2
    top  = (fill_h - OUT_H) // 2
    bg   = bg.crop((left, top, left + OUT_W, top + OUT_H))

    # Apply Gaussian blur — sigma maps to Pillow's radius (sigma ≈ radius * 0.6)
    pillow_radius = max(1, int(sigma * 0.6))
    bg = bg.filter(ImageFilter.GaussianBlur(radius=pillow_radius))

    # ── Foreground: scale-to-fit (contain) ───────────────────────────────────
    fit_scale = min(OUT_W / src_w, OUT_H / src_h)
    fit_w = int(src_w * fit_scale)
    fit_h = int(src_h * fit_scale)
    fg = src.resize((fit_w, fit_h), Image.LANCZOS)

    # ── Composite ─────────────────────────────────────────────────────────────
    out = bg.copy()
    paste_x = (OUT_W - fit_w) // 2
    paste_y = (OUT_H - fit_h) // 2
    out.paste(fg, (paste_x, paste_y))

    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sigma", type=int, default=25,
                        help="Gaussian blur sigma (default 25)")
    args = parser.parse_args()

    print(f"Generating synthetic 9:16 source image...")
    src = make_synthetic_vertical()
    src_path = OUT_DIR / "preview_source_916.jpg"
    src.save(str(src_path), quality=90)
    print(f"  Source saved: {src_path}")

    print(f"Applying 4:3 blurred-fill (sigma={args.sigma})...")
    result = apply_43_blurred_fill(src, sigma=args.sigma)
    result.save(str(OUT_FILE), quality=92)
    print(f"  Result saved: {OUT_FILE}")
    print()
    print(f"Frame size: {OUT_W}x{OUT_H} (4:3 = 1440x1080)")
    print(f"Blur sigma: {args.sigma}")
    print()
    print("Open preview_43_blur.jpg to approve the blur strength.")
    print("When happy, tell me to proceed and I'll implement the full pipeline.")


if __name__ == "__main__":
    main()
