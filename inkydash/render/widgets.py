"""A thin drawing layer over Pillow for palette-mode canvases.

Everything draws palette indices with anti-aliasing off (fontmode "1"), so the panel shows
exactly these pixels with no dithering.
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from inkydash.render.theme import BLACK, WHITE, YELLOW, Theme, font

Box = tuple[int, int, int, int]


class Canvas:
    def __init__(self, img: Image.Image, theme: Theme | None = None):
        if img.mode != "P":
            raise ValueError("Canvas draws on palette (P) images only")
        self.img = img
        self.theme = theme or Theme()
        self.d = ImageDraw.Draw(img)
        self.d.fontmode = "1"

    @property
    def size(self) -> tuple[int, int]:
        return self.img.size

    @property
    def w(self) -> int:
        return self.img.width

    @property
    def h(self) -> int:
        return self.img.height

    # ------------------------------------------------------------- text
    @staticmethod
    def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
        return font(kind, size)

    def measure(self, s: str, kind: str = "ui", size: int = 16) -> tuple[int, int]:
        left, top, right, bottom = self.d.textbbox((0, 0), s, font=font(kind, size))
        return right - left, bottom - top

    def width(self, s: str, kind: str = "ui", size: int = 16) -> int:
        return int(self.d.textlength(s, font=font(kind, size)))

    def text(self, xy: tuple[float, float], s: str, kind: str = "ui", size: int = 16, fill: int = BLACK,
             anchor: str = "la", stroke: int = 0, stroke_fill: int = BLACK) -> Box:
        f = font(kind, size)
        self.d.text(xy, s, font=f, fill=fill, anchor=anchor, stroke_width=stroke, stroke_fill=stroke_fill)
        return tuple(int(v) for v in self.d.textbbox(xy, s, font=f, anchor=anchor, stroke_width=stroke))  # type: ignore[return-value]

    def fit_size(self, s: str, kind: str, max_w: int, max_size: int, min_size: int = 10) -> int:
        size = max_size
        while size > min_size and self.width(s, kind, size) > max_w:
            size -= 1
        return size

    def truncate(self, s: str, kind: str, size: int, max_w: int) -> str:
        if self.width(s, kind, size) <= max_w:
            return s
        while s and self.width(s + "…", kind, size) > max_w:
            s = s[:-1]
        return s.rstrip() + "…"

    def text_fit(self, xy: tuple[float, float], s: str, kind: str, max_w: int, max_size: int,
                 min_size: int = 10, fill: int = BLACK, anchor: str = "la") -> Box:
        size = self.fit_size(s, kind, max_w, max_size, min_size)
        return self.text(xy, self.truncate(s, kind, size, max_w), kind, size, fill, anchor)

    def wrap(self, s: str, kind: str, size: int, max_w: int, max_lines: int = 99) -> list[str]:
        words = s.split()
        lines: list[str] = []
        cur = ""
        for w in words:
            cand = f"{cur} {w}".strip()
            if self.width(cand, kind, size) <= max_w or not cur:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = self.truncate(lines[-1] + " …", kind, size, max_w)
        return [self.truncate(line, kind, size, max_w) for line in lines]

    def paragraph(self, xy: tuple[int, int], s: str, kind: str, size: int, max_w: int,
                  max_lines: int = 99, fill: int = BLACK, spacing: float = 1.2) -> int:
        """Draws wrapped text; returns the y below it."""
        x, y = xy
        step = int(size * spacing)
        for line in self.wrap(s, kind, size, max_w, max_lines):
            self.text((x, y), line, kind, size, fill)
            y += step
        return y

    # ------------------------------------------------------------- shapes
    def rect(self, box: Box, fill: int | None = None, outline: int | None = None, width: int = 1) -> None:
        self.d.rectangle(box, fill=fill, outline=outline, width=width)

    def rounded(self, box: Box, r: int = 6, fill: int | None = None, outline: int | None = None,
                width: int = 1) -> None:
        self.d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)

    def line(self, pts: list[tuple[float, float]], fill: int = BLACK, width: int = 1) -> None:
        if len(pts) >= 2:
            self.d.line(pts, fill=fill, width=width, joint="curve")

    def hline(self, y: int, x0: int = 0, x1: int | None = None, fill: int = BLACK, width: int = 1) -> None:
        self.d.line([(x0, y), (self.w if x1 is None else x1, y)], fill=fill, width=width)

    def vline(self, x: int, y0: int = 0, y1: int | None = None, fill: int = BLACK, width: int = 1) -> None:
        self.d.line([(x, y0), (x, self.h if y1 is None else y1)], fill=fill, width=width)

    def dashed(self, pts: list[tuple[float, float]], fill: int = BLACK, width: int = 2,
               dash: float = 8, gap: float = 6) -> None:
        on, left = True, dash
        for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
            seg = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
            pos = 0.0
            while pos < seg:
                step = min(left, seg - pos)
                a = (x0 + (x1 - x0) * pos / seg, y0 + (y1 - y0) * pos / seg)
                b = (x0 + (x1 - x0) * (pos + step) / seg, y0 + (y1 - y0) * (pos + step) / seg)
                if on:
                    self.d.line([a, b], fill=fill, width=width)
                pos += step
                left -= step
                if left <= 0:
                    on = not on
                    left = dash if on else gap

    def badge(self, xy: tuple[int, int], s: str, fill: int = YELLOW, text_fill: int = BLACK,
              kind: str = "bold", size: int = 16, pad: int = 6, outline: int | None = BLACK,
              anchor: str = "la") -> Box:
        tw, th = self.measure(s, kind, size)
        w, h = tw + 2 * pad, int(size * 1.25) + pad
        x, y = xy
        if anchor[0] == "r":
            x -= w
        elif anchor[0] == "m":
            x -= w // 2
        box = (x, y, x + w, y + h)
        self.rounded(box, r=min(8, h // 2), fill=fill, outline=outline)
        self.text((x + w / 2, y + h / 2), s, kind, size, text_fill, anchor="mm")
        return box

    def banner(self, box: Box, s: str, fill: int, text_fill: int = WHITE, size: int = 20,
               kind: str = "bold") -> None:
        self.rect(box, fill=fill)
        x0, y0, x1, y1 = box
        size = self.fit_size(s, kind, x1 - x0 - 16, size, 11)
        self.text(((x0 + x1) / 2, (y0 + y1) / 2), self.truncate(s, kind, size, x1 - x0 - 16), kind, size,
                  text_fill, anchor="mm")

    def warn_mark(self, x: float, cy: float, size: float, fill: int = YELLOW) -> None:
        """Small warning triangle (the fonts have no ⚠ glyph)."""
        h = size * 0.88
        self.d.polygon([(x + size / 2, cy - h / 2), (x + size, cy + h / 2), (x, cy + h / 2)], fill=fill,
                       outline=BLACK)
        self.text((x + size / 2, cy + h * 0.14), "!", "bold", max(8, int(size * 0.7)), BLACK, anchor="mm")

    def footer(self, s: str, size: int = 12, y_pad: int = 4, fill: int = BLACK, align: str = "right",
               height: int | None = None, warn: bool = False) -> Box:
        """Small status line at the bottom. Returns its box, to mark it volatile."""
        h = height or size + 2 * y_pad
        y = self.h - h
        self.rect((0, y, self.w, self.h), fill=WHITE)
        x, anchor = (self.w - 6, "rm") if align == "right" else (6, "lm")
        if warn:
            mark = size + 2
            if align == "right":
                left = self.w - 6 - self.width(s, "ui", size) - mark - 4
            else:
                left = 6
                x += mark + 4
            self.warn_mark(left, y + h / 2, mark)
        self.text((x, y + h / 2), s, "ui", size, fill, anchor=anchor)
        return (0, y, self.w, self.h)

    def placeholder(self, title: str, detail: str = "") -> None:
        cx, cy = self.w // 2, self.h // 2
        self.text((cx, cy - 14), title, "bold", 28, BLACK, anchor="mm")
        if detail:
            for i, line in enumerate(self.wrap(detail, "ui", 16, self.w - 80, 4)):
                self.text((cx, cy + 22 + i * 20), line, "ui", 16, BLACK, anchor="mm")
