"""Font loading and number formatting for the tally renderer."""
from pathlib import Path

from PIL import ImageFont

_FONTS_DIR = Path(__file__).parent.parent / "assets" / "fonts"

_FONT_FILES = {
    ("display", False): "MPlusRounded1c-Regular.ttf",
    ("display", True):  "MPlusRounded1c-Bold.ttf",
    ("dejavu",  False): "DejaVuSans.ttf",
    ("dejavu",  True):  "DejaVuSans-Bold.ttf",
}


def load_font(size: int, *, bold: bool = False, game: bool = False) -> ImageFont.FreeTypeFont:
    family = "display" if game else "dejavu"
    filename = _FONT_FILES[(family, bold)]
    return ImageFont.truetype(str(_FONTS_DIR / filename), size)


def fmt_int(n: int) -> str:
    return f"{int(n):,}"


def fmt_blank_if_zero(n: int) -> str:
    return fmt_int(n) if n else ""
