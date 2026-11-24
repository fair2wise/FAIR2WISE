"""Side-by-side images of two screenshot runs: python compare.py <before_dir> <after_dir> <out_dir> (needs Pillow)."""
import os
import sys

from PIL import Image, ImageChops, ImageDraw

before, after, out = sys.argv[1:4]
os.makedirs(out, exist_ok=True)
for name in sorted(os.listdir(before)):
    if not name.endswith(".png") or not os.path.exists(f"{after}/{name}"):
        continue
    x = Image.open(f"{before}/{name}").convert("RGB")
    y = Image.open(f"{after}/{name}").convert("RGB")
    w, h = x.size
    sheet = Image.new("RGB", (w * 2 + 10, h + 24), "white")
    sheet.paste(x, (0, 24))
    sheet.paste(y, (w + 10, 24))
    draw = ImageDraw.Draw(sheet)
    draw.text((10, 6), f"BEFORE {before}", fill="black")
    draw.text((w + 20, 6), f"AFTER {after}", fill="black")
    sheet.save(f"{out}/{name}")
    changed = ImageChops.difference(x, y).getbbox()
    print(name, "same" if changed is None else f"differs in {changed}")
