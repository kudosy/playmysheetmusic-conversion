"""Open an uploaded file and get each page ready for reading.

A PDF becomes one image per page; a photo (JPG, PNG, HEIC, WebP, TIFF) becomes
one image. Each page is then turned the right way up, straightened and given
even lighting. Nothing is invented: there is no AI upscaling, only geometry
and brightness.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image, ImageOps

try:  # HEIC / HEIF photos from iPhones
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover - the service image always has it
    pillow_heif = None

# homr works on pages 1920 pixels wide, so more than this adds time, not detail.
PDF_RENDER_WIDTH = 2560
MAX_LONG_EDGE = 4200
MAX_PAGES = 60
MAX_SKEW_DEGREES = 12.0
MAX_LIGHT_GAIN = 1.5


class UnreadableFile(ValueError):
    """The upload is not an image or PDF we can open."""


@dataclass
class Page:
    image: np.ndarray  # BGR, 8 bit
    steps: list[str] = field(default_factory=list)  # what was done, for the log


# ---------------------------------------------------------------- opening

def _is_pdf(data: bytes) -> bool:
    return data[:1024].lstrip().startswith(b"%PDF")


def _pil_to_bgr(image: Image.Image) -> np.ndarray:
    image = ImageOps.exif_transpose(image)  # honour the camera's rotation tag
    if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        image = Image.alpha_composite(white, rgba)
    if image.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        arr = np.asarray(image).astype(np.float64)
        top = arr.max() if arr.max() > 0 else 1.0
        image = Image.fromarray((arr / top * 255).astype(np.uint8))
    rgb = np.asarray(image.convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _limit_size(image: np.ndarray) -> np.ndarray:
    long_edge = max(image.shape[:2])
    if long_edge <= MAX_LONG_EDGE:
        return image
    scale = MAX_LONG_EDGE / long_edge
    size = (round(image.shape[1] * scale), round(image.shape[0] * scale))
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA)


def open_pages(data: bytes, max_pages: int = MAX_PAGES) -> list[np.ndarray]:
    """Decode an upload into page images (BGR)."""
    if not data:
        raise UnreadableFile("The file is empty.")
    if _is_pdf(data):
        import pypdfium2 as pdfium

        try:
            pdf = pdfium.PdfDocument(data)
        except Exception as error:
            raise UnreadableFile("This PDF could not be opened.") from error
        pages = []
        try:
            for index in range(min(len(pdf), max_pages)):
                page = pdf[index]
                width, _ = page.get_size()
                scale = PDF_RENDER_WIDTH / max(width, 1.0)
                bitmap = page.render(scale=scale)
                pages.append(_limit_size(_pil_to_bgr(bitmap.to_pil())))
        finally:
            pdf.close()
        if not pages:
            raise UnreadableFile("This PDF has no pages.")
        return pages
    try:
        image = Image.open(io.BytesIO(data))
        frames = getattr(image, "n_frames", 1)
        pages = []
        for index in range(min(frames, max_pages)):
            if frames > 1:
                image.seek(index)
            pages.append(_limit_size(_pil_to_bgr(image.copy())))
        return pages
    except UnreadableFile:
        raise
    except Exception as error:
        raise UnreadableFile("This file is not a picture or PDF we can open.") from error


# ---------------------------------------------------------------- measuring

def _small_gray(image: np.ndarray, long_edge: int = 1400) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    scale = long_edge / max(gray.shape)
    if scale < 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return gray


def _ink(gray: np.ndarray) -> np.ndarray:
    """Dark marks as 255 on 0, tolerant of shadows."""
    block = max(15, (min(gray.shape) // 40) | 1)
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, block, 12
    )


def _long_lines(ink: np.ndarray, horizontal: bool) -> np.ndarray:
    length = max(25, (ink.shape[1] if horizontal else ink.shape[0]) // 28)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (length, 1) if horizontal else (1, length))
    return cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel)


def _rotate(image: np.ndarray, degrees: float) -> np.ndarray:
    """Rotate about the centre (positive is anticlockwise), filling with paper."""
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), degrees, 1.0)
    return cv2.warpAffine(
        image, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def _sharpest_angle(ink: np.ndarray) -> tuple[float, float]:
    """Try small rotations and keep the one where the rows of ink are
    sharpest: that is where the staff lines are level. Returns the angle and
    how sharp the rows were."""
    scale = 700 / max(ink.shape)
    small = cv2.resize(ink, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else ink
    height, width = small.shape

    def sharpness(angle: float) -> float:
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
        turned = cv2.warpAffine(small, matrix, (width, height), flags=cv2.INTER_LINEAR)
        profile = turned.sum(axis=1, dtype=np.float64) / 255.0
        return float(np.sum(np.diff(profile) ** 2))

    best, best_score = 0.0, sharpness(0.0)
    for step, reach in ((1.0, MAX_SKEW_DEGREES), (0.25, 1.0), (0.05, 0.25)):
        centre = best
        angle = centre - reach
        while angle <= centre + reach + 1e-9:
            score = sharpness(angle)
            if score > best_score:
                best, best_score = angle, score
            angle += step
    return best, best_score


def _line_spacing(lines: np.ndarray) -> float | None:
    """Distance between neighbouring staff lines, in pixels."""
    profile = lines.sum(axis=1, dtype=np.float64)
    profile -= profile.mean()
    if not profile.any():
        return None
    corr = np.correlate(profile, profile, mode="full")[profile.size - 1 :]
    low, high = 4, min(60, corr.size - 1)
    if high <= low:
        return None
    lag = int(np.argmax(corr[low:high])) + low
    return float(lag) if corr[lag] > 0.2 * corr[0] else None


def _staves(ink: np.ndarray) -> tuple[np.ndarray, float, list[tuple[int, int, int, int]]]:
    """Find the staves on a level page. Returns the staff-line picture, the
    distance between staff lines, and a box (left, top, right, bottom) for
    each staff or group of joined staves."""
    lines = _long_lines(ink, True)
    spacing = _line_spacing(lines)
    if spacing is None:
        return lines, 0.0, []
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, ink.shape[1] // 40), int(spacing * 1.6) | 1))
    blobs = cv2.morphologyEx(lines, cv2.MORPH_CLOSE, kernel)
    count, _, stats, _ = cv2.connectedComponentsWithStats(blobs, connectivity=8)
    found = []
    for i in range(1, count):
        x, y, w, h, _ = stats[i]
        if w >= ink.shape[1] * 0.3 and h >= 3 * spacing:
            found.append((int(x), int(y), int(x + w), int(y + h)))
    return lines, spacing, found


def _upside_down(ink: np.ndarray) -> bool:
    """Every staff begins with a clef, so just inside its left end there is
    ink, while just inside its right end there is only the gap before the
    last bar line. If it is the other way round, the page is upside down."""
    lines, spacing, staves = _staves(ink)
    marks = cv2.subtract(ink, cv2.dilate(lines, np.ones((3, 1), np.uint8)))
    near, far = round(spacing * 0.8), round(spacing * 2.4)
    left_total = right_total = 0.0
    for x0, y0, x1, y1 in staves:
        left_total += float(np.count_nonzero(marks[y0:y1, x0 + near : x0 + far]))
        right_total += float(np.count_nonzero(marks[y0:y1, x1 - far : x1 - near]))
    if not staves or left_total + right_total < 60:
        return False
    return right_total > 1.25 * left_total


# ---------------------------------------------------------------- lighting

def _uneven_light(gray: np.ndarray) -> tuple[bool, np.ndarray]:
    """Estimate the paper brightness everywhere and say whether it varies
    enough (shadows, a curved page, a dim corner) to be worth correcting."""
    small = _small_gray(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), 500)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    paper = cv2.morphologyEx(small, cv2.MORPH_CLOSE, kernel)  # removes the ink
    paper = cv2.medianBlur(paper, 21)
    paper = cv2.GaussianBlur(paper, (0, 0), 8)
    low, high = np.percentile(paper, [5, 95])
    return (high - low) > 28, paper


def even_lighting(image: np.ndarray) -> tuple[np.ndarray, bool]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    needed, paper = _uneven_light(gray)
    if not needed:
        return image, False
    paper = cv2.resize(paper, (image.shape[1], image.shape[0]), interpolation=cv2.INTER_CUBIC)
    paper = np.maximum(paper.astype(np.float32), 40.0)
    target = float(np.percentile(paper, 90))
    # Lift shadows on the paper, but never by so much that a dark table, book
    # cover or finger beside the page starts to look like paper.
    gain = np.clip(target / paper, 1.0 / MAX_LIGHT_GAIN, MAX_LIGHT_GAIN)[..., None]
    out = np.clip(image.astype(np.float32) * gain, 0, 255).astype(np.uint8)
    return out, True


# ---------------------------------------------------------------- the lot

def normalize(image: np.ndarray) -> Page:
    page = Page(image=image)
    ink = _ink(_small_gray(page.image))

    # Which way do the staff lines run, and how far off level are they?
    # Try the picture as it is and turned a quarter; keep whichever shows staves.
    def staves_when_level(candidate: np.ndarray) -> tuple[float, int]:
        angle, _ = _sharpest_angle(candidate)
        level = _rotate(candidate, angle) if abs(angle) >= 0.3 else candidate
        _, _, staves = _staves(level)
        return angle, sum(x1 - x0 for x0, _, x1, _ in staves)

    angle, found = staves_when_level(ink)
    side_angle, side_found = staves_when_level(cv2.rotate(ink, cv2.ROTATE_90_CLOCKWISE))
    if side_found > 1.5 * found:
        page.image = cv2.rotate(page.image, cv2.ROTATE_90_CLOCKWISE)
        page.steps.append("turned a sideways page")
        angle, found = side_angle, side_found
    if found and 0.3 <= abs(angle) <= MAX_SKEW_DEGREES:
        page.image = _rotate(page.image, angle)
        page.steps.append(f"straightened by {angle:+.2f} degrees")
    if page.steps:
        ink = _ink(_small_gray(page.image))

    if _upside_down(ink):
        page.image = cv2.rotate(page.image, cv2.ROTATE_180)
        page.steps.append("turned an upside-down page")

    page.image, changed = even_lighting(page.image)
    if changed:
        page.steps.append("evened out the lighting")
    return page


def load_pages(data: bytes, max_pages: int = MAX_PAGES) -> list[Page]:
    return [normalize(image) for image in open_pages(data, max_pages)]
