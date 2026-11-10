"""T30: build icon.ico (app, ✂️) and icons/gif.ico (✌️) from the Fluent Emoji 3D PNGs.

Dev-only (needs Pillow); the .ico files are committed, the build doesn't run this.
    python tools/make_icons.py
"""
from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def frames(source: Path) -> list[Image.Image]:
    image = Image.open(source).convert("RGBA")
    out = []
    for size in SIZES:
        frame = image.resize((size, size), Image.Resampling.LANCZOS)
        if size <= 32:
            # downscaling a 3D render softens it; a light unsharp keeps small sizes readable
            frame = frame.filter(ImageFilter.UnsharpMask(radius=0.6, percent=60, threshold=0))
        out.append(frame)
    return out


def write_ico(source: Path, target: Path) -> None:
    images = frames(source)
    largest = images[-1]
    largest.save(target, format="ICO", sizes=[im.size for im in images], append_images=images[:-1])
    print(f"{target.relative_to(ROOT)}: {', '.join(str(s) for s in SIZES)}")


if __name__ == "__main__":
    write_ico(ROOT / "icons" / "scissors_3d.png", ROOT / "icon.ico")
    write_ico(ROOT / "icons" / "victory_hand_3d.png", ROOT / "icons" / "gif.ico")
