"""The shared photo pipeline (DESIGN.md §6.2): decode → fit → enhance.

Used on the Pi for unprepared photos and by `inky-dash prepare` on the PC, which adds a
smart crop window and previews on top. One implementation, two places.
"""

from __future__ import annotations

from typing import BinaryIO

from PIL import Image, ImageEnhance, ImageOps

Box = tuple[float, float, float, float]

try:  # optional HEIC support
    from pillow_heif import register_heif_opener  # type: ignore[import-not-found]

    register_heif_opener()
except ImportError:
    pass


def load(fp: BinaryIO | str, target: tuple[int, int]) -> Image.Image:
    """Decode at reduced scale for JPEGs (Image.draft), apply EXIF rotation, return RGB."""
    img = Image.open(fp)
    if img.format == "JPEG":
        # draft() picks the largest 1/2, 1/4 or 1/8 scale that is still ≥ the requested size
        tw, th = target
        img.draft("RGB", (max(tw, th), max(tw, th)))
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return img.convert("RGB")


def choose_fit(img_size: tuple[int, int], target: tuple[int, int], fit: str) -> str:
    if fit != "auto":
        return fit
    ia = img_size[0] / img_size[1]
    ta = target[0] / target[1]
    return "cover" if max(ia, ta) / min(ia, ta) <= 1.25 else "contain"


def cover(img: Image.Image, target: tuple[int, int], focus: Box | None = None) -> Image.Image:
    """Crop to fill. `focus` (x0, y0, x1, y1 in 0..1) is a region to keep in view (faces);
    without it the crop is centred horizontally with an upper-third bias vertically."""
    tw, th = target
    w, h = img.size
    scale = max(tw / w, th / h)
    cw, ch = tw / scale, th / scale  # crop size in source pixels
    if focus:
        fx = (focus[0] + focus[2]) / 2 * w
        fy = (focus[1] + focus[3]) / 2 * h
    else:
        fx, fy = w / 2, h * 0.4
    x0 = min(max(fx - cw / 2, 0), w - cw)
    y0 = min(max(fy - ch / 2, 0), h - ch)
    return img.resize(target, Image.Resampling.LANCZOS, box=(x0, y0, x0 + cw, y0 + ch))


def contain(img: Image.Image, target: tuple[int, int], background: tuple[int, int, int] = (255, 255, 255)
            ) -> Image.Image:
    fitted = ImageOps.contain(img, target, Image.Resampling.LANCZOS)
    out = Image.new("RGB", target, background)
    out.paste(fitted, ((target[0] - fitted.width) // 2, (target[1] - fitted.height) // 2))
    return out


def enhance(img: Image.Image, contrast: float = 1.15, saturation: float = 1.25, brightness: float = 1.05,
            gamma: float = 0.95) -> Image.Image:
    """E-paper has no backlight: lift contrast, colour and midtones a little."""
    if contrast != 1:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if saturation != 1:
        img = ImageEnhance.Color(img).enhance(saturation)
    if brightness != 1:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    if gamma != 1:
        lut = [min(255, round(255 * (i / 255) ** gamma)) for i in range(256)]
        img = img.point(lut * 3)
    return img


def prepare(fp: BinaryIO | str, target: tuple[int, int], fit: str = "auto", do_enhance: bool = True,
            focus: Box | None = None, background: tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    img = load(fp, target)
    mode = choose_fit(img.size, target, fit)
    out = cover(img, target, focus) if mode == "cover" else contain(img, target, background)
    return enhance(out) if do_enhance else out
