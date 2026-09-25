"""Page text as positioned cells, built the same way from a PDF text layer or from OCR.

A *cell* is a run of words on one visual line with no large gap inside it ("Invoice No:",
"1.234,56 €"). Field rules work on lines of cells, so they do not care where the words came from.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
import pdfplumber
import pypdfium2 as pdfium


@dataclass
class Cell:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    size: float  # font size (text layer) or box height (OCR)
    bold: bool = False

    @property
    def mid_y(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass
class Line:
    cells: list[Cell] = field(default_factory=list)

    @property
    def top(self) -> float:
        return min(c.top for c in self.cells)

    @property
    def text(self) -> str:
        return "  ".join(c.text for c in self.cells)


@dataclass
class PageText:
    lines: list[Line]
    width: float
    height: float
    source: str  # "text" | "ocr"
    ocr_scores: list[float] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


def _group_lines(cells: list[Cell]) -> list[Line]:
    if not cells:
        return []
    heights = [c.bottom - c.top for c in cells]
    tol = max(2.0, statistics.median(heights) * 0.45)
    lines: list[Line] = []
    for cell in sorted(cells, key=lambda c: c.mid_y):
        if lines and abs(cell.mid_y - statistics.fmean(c.mid_y for c in lines[-1].cells)) <= tol:
            lines[-1].cells.append(cell)
        else:
            lines.append(Line([cell]))
    for line in lines:
        line.cells.sort(key=lambda c: c.x0)
    return lines


def _merge_words(words: list[Cell], gap_factor: float = 0.9) -> list[Cell]:
    """Join words on one line into cells: a gap wider than ~one space-width ends a cell."""
    cells: list[Cell] = []
    for w in sorted(words, key=lambda c: c.x0):
        if cells:
            prev = cells[-1]
            gap = w.x0 - prev.x1
            if gap <= w.size * gap_factor and abs(prev.size - w.size) < 1.5:
                prev.text = f"{prev.text} {w.text}"
                prev.x1 = w.x1
                prev.top = min(prev.top, w.top)
                prev.bottom = max(prev.bottom, w.bottom)
                prev.bold = prev.bold and w.bold
                continue
        cells.append(Cell(w.text, w.x0, w.x1, w.top, w.bottom, w.size, w.bold))
    return cells


def has_text_layer(path: str, min_chars: int = 40) -> bool:
    with pdfplumber.open(path) as pdf:
        return len((pdf.pages[0].extract_text() or "").strip()) >= min_chars


def from_text_layer(path: str) -> PageText:
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        words = page.extract_words(extra_attrs=["size", "fontname"], keep_blank_chars=False)
        cells = [
            Cell(w["text"], w["x0"], w["x1"], w["top"], w["bottom"], round(w["size"], 1), "Bold" in w["fontname"])
            for w in words
        ]
        rough = _group_lines(cells)
        lines = _group_lines([c for line in rough for c in _merge_words(line.cells)])
        return PageText(lines, page.width, page.height, "text")


@lru_cache(maxsize=1)
def _ocr_engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def _deskew(boxes: list) -> float:
    """Median baseline angle (radians) of wide text boxes."""
    angles = []
    for box in boxes:
        (x0, y0), (x1, y1) = box[0], box[1]
        if x1 - x0 > 60:
            angles.append(math.atan2(y1 - y0, x1 - x0))
    return statistics.median(angles) if angles else 0.0


def from_ocr(path: str, dpi: int = 150) -> PageText:
    page = pdfium.PdfDocument(path)[0]
    img = page.render(scale=dpi / 72).to_pil().convert("RGB")
    result, _ = _ocr_engine()(np.asarray(img))
    result = result or []
    angle = _deskew([r[0] for r in result])
    cx, cy = img.width / 2, img.height / 2
    cos, sin = math.cos(-angle), math.sin(-angle)

    def unrotate(x: float, y: float) -> tuple[float, float]:
        dx, dy = x - cx, y - cy
        return cx + dx * cos - dy * sin, cy + dx * sin + dy * cos

    scale = 72 / dpi  # report coordinates in PDF points, like the text layer
    cells, scores = [], []
    for box, text, score in result:
        pts = [unrotate(x, y) for x, y in box]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        cells.append(Cell(text, min(xs) * scale, max(xs) * scale, min(ys) * scale, max(ys) * scale,
                          (max(ys) - min(ys)) * scale))
        scores.append(float(score))
    return PageText(_group_lines(cells), img.width * scale, img.height * scale, "ocr", scores)


def read_page(path: str, cache_dir: str | None = None) -> PageText:
    """OCR is the slow step (~6 s/page); cache_dir keeps its output keyed by file content."""
    if has_text_layer(path):
        return from_text_layer(path)
    if cache_dir is None:
        return from_ocr(path)
    import hashlib
    import pickle
    from pathlib import Path

    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    cached = Path(cache_dir) / f"{digest}.ocr.pkl"
    if cached.exists():
        return pickle.loads(cached.read_bytes())
    page = from_ocr(path)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(pickle.dumps(page))
    return page
