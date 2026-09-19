"""Export the existing Huisplan artwork at PWA icon sizes (requires Pillow)."""
from pathlib import Path

from PIL import Image, ImageOps


root = Path(__file__).resolve().parents[1] / "rit_tank"
logo = Image.open(root / "huisplan-logo.png").convert("RGBA")
logo = logo.crop(logo.getbbox())
for size in (180, 192, 512):
    # Keep the mark within the central 80% for maskable launcher icons.
    mark = ImageOps.contain(logo, (int(size * .76), int(size * .76)), Image.Resampling.LANCZOS)
    icon = Image.new("RGB", (size, size), "#ffffff")
    icon.paste(mark, ((size - mark.width) // 2, (size - mark.height) // 2), mark)
    icon.save(root / f"huisplan-icon-{size}.png", optimize=True)
