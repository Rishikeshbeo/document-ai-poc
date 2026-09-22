"""Turning an uploaded file into pages of positioned words.

Coordinates are normalised to 0..1 of the page box, so a rectangle drawn in the
browser at any zoom level means the same thing on the server, and a hint
recorded on an A4 page still applies to a Letter-sized reprint.

Nothing here is written to disk. The caller works on bytes in memory.
"""

import hashlib
import io
import re

import pdfplumber

try:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, NumberObject
except ImportError:      # pragma: no cover
    # Optional. Without it a rotated page is refused and sent to OCR rather
    # than read wrongly — a missing extra should cost a feature, not the
    # service.
    PdfReader = None

try:
    from PIL import Image, ImageOps
except ImportError:      # pragma: no cover
    # Arrives with pdfplumber, so it is here in practice. Without it a photo
    # taken in portrait is read on its side — see `upright`.
    Image = None

VAT_RE = re.compile(
    r"\b(?:DE|ATU?|FR|NL|BE|IT|ES|PL|SE|DK|FI|IE|PT|CZ|HU|RO|LU|NO|CH|BG|EE|LV|LT"
    r"|SI|SK|HR|CY|MT|EL|GR|GB)\s?[0-9A-Z]{8,12}\b"
)
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[0-9A-Z]{4}){2,7}(?:\s?[0-9A-Z]{1,3})?\b")


class Page(dict):
    """{page, width, height, words: [...], text}"""


class NeedsOCR(Exception):
    """The file carries no text layer, so only the OCR path can read it.

    Raised rather than returned because it is not an error in itself — the
    caller decides whether an OCR provider is configured to handle it.
    """

    def __init__(self, mime):
        super().__init__(f"{mime} has no extractable text layer")
        self.mime = mime


IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)
IMAGE_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
    ".tif": "image/tiff", ".tiff": "image/tiff",
}


def sniff(data: bytes, filename: str) -> str:
    """Content type from magic bytes first, extension second."""
    if data[:4] == b"%PDF":
        return "application/pdf"
    for magic, mime in IMAGE_MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    name = (filename or "").lower()
    for ext, mime in IMAGE_EXT.items():
        if name.endswith(ext):
            return mime
    if name.endswith(".pdf"):
        return "application/pdf"
    if name.endswith((".txt", ".md", ".csv")):
        return "text/plain"
    return "application/octet-stream"


def load(data: bytes, filename: str):
    """Return a list of Page.

    Raises NeedsOCR when the bytes are readable but carry no text layer — a
    scan or a photo. Raises ValueError when the format is not supported at all.
    """
    mime = sniff(data, filename)
    if mime == "application/pdf":
        return _load_pdf(data)
    if mime.startswith("image/"):
        raise NeedsOCR(mime)
    if mime == "text/plain":
        return _load_text(data.decode("utf-8", "replace"))
    raise ValueError("This build reads PDF, plain text, and images.")


EXIF_ORIENTATION = 0x0112


def upright(data: bytes, filename: str = "") -> bytes:
    """Bake a photo's EXIF orientation into its pixels.

    A phone stores the sensor's pixels the way it read them and writes an
    Orientation tag saying which way up they go. Browsers apply that tag; OCR
    services read the stored grid and ignore it. So a photo of an invoice taken
    in portrait is read from a landscape grid, and every word box comes back a
    quarter turn from where the reader sees it: the panel draws its rectangles
    in the wrong corner, a rectangle the user drags is read somewhere else
    entirely, and a saved correction can never be found again.

    This is the same failure `/Rotate` causes on a PDF, and it is handled the
    same way — normalise before reading, so one grid means one thing to the
    browser and to the server.

    The format is kept, so a JPEG stays a JPEG: the bytes go straight to OCR
    and nothing is written to disk. Anything unreadable here is returned
    untouched, which is the behaviour before this existed.
    """
    if Image is None:
        return data
    if not sniff(data, filename).startswith("image/"):
        return data
    try:
        with Image.open(io.BytesIO(data)) as im:
            if (im.getexif() or {}).get(EXIF_ORIENTATION, 1) in (1, None):
                return data              # already upright; no re-encode
            fmt = (im.format or "PNG").upper()
            fixed = ImageOps.exif_transpose(im)
            buf = io.BytesIO()
            # 95 rather than the default 75: this is an invoice being read, and
            # a re-encode that softens small print costs accuracy downstream.
            extra = {"quality": 95} if fmt in ("JPEG", "WEBP") else {}
            fixed.save(buf, format=fmt, **extra)
            return buf.getvalue()
    except Exception:
        return data


SLACK = 0.02   # a glyph may overhang the page box by a hair; nothing overhangs by more


def _flatten_rotation(data: bytes):
    """Strip /Rotate from every page, returning the new bytes and what was removed.

    pdfplumber cannot be relied on for a rotated page: on a quarter turn this
    version reports the unrotated page box while placing words in the rotated
    one, and on a half turn it returns the characters of every word reversed —
    "Testwerk" comes back as "krewtseT". Reading the page unrotated avoids all
    of it, and the display orientation is reapplied to the coordinates
    afterwards, which is arithmetic we control.

    Rotated pages are the norm in scanned batches, so this is not an edge case.
    """
    if PdfReader is None:
        return data, []
    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter()
    rotations = []
    for page in reader.pages:
        rotations.append(int(page.get("/Rotate") or 0) % 360)
        page[NameObject("/Rotate")] = NumberObject(0)
        writer.add_page(page)
    if not any(rotations):
        return data, rotations          # untouched, so nothing can be lost
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue(), rotations


def _rotate_box(box, rotation):
    """Map a 0..1 box on the unrotated page into the view the reader is shown."""
    x0, y0, x1, y1 = box
    if rotation == 90:
        corners = [(1 - y0, x0), (1 - y1, x1)]
    elif rotation == 180:
        corners = [(1 - x0, 1 - y0), (1 - x1, 1 - y1)]
    elif rotation == 270:
        corners = [(y0, 1 - x0), (y1, 1 - x1)]
    else:
        return box
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def _load_pdf(data: bytes):
    try:
        flat, rotations = _flatten_rotation(data)
    except Exception:
        flat, rotations = data, []      # unreadable structure: behave as before

    pages = []
    untrusted = False
    with pdfplumber.open(io.BytesIO(flat)) as pdf:
        for i, p in enumerate(pdf.pages, start=1):
            rotation = rotations[i - 1] if i - 1 < len(rotations) else 0
            w, h = float(p.width), float(p.height)
            words = []
            for word in p.extract_words(use_text_flow=False, keep_blank_chars=False):
                box = _rotate_box((float(word["x0"]) / w, float(word["top"]) / h,
                                   float(word["x1"]) / w, float(word["bottom"]) / h),
                                  rotation)
                if min(box) < -SLACK or max(box) > 1 + SLACK:
                    # Words landing outside the page box mean the geometry is
                    # not what we think it is, whatever the cause. A region
                    # saved against coordinates like these could never be
                    # matched again, so refuse the text layer rather than
                    # record something unusable.
                    untrusted = True
                    break
                words.append({
                    "text": word["text"],
                    "x0": round(box[0], 5),
                    "y0": round(box[1], 5),
                    "x1": round(box[2], 5),
                    "y1": round(box[3], 5),
                })
            # A quarter turn swaps what the reader sees, and the browser renders
            # the same way, so the page box has to agree.
            dw, dh = (h, w) if rotation in (90, 270) else (w, h)
            pages.append(Page(page=i, width=dw, height=dh, words=words,
                              text=p.extract_text() or ""))
    if not pages:
        raise ValueError("That PDF has no readable pages.")
    if untrusted or not any(pg["words"] for pg in pages):
        raise NeedsOCR("application/pdf")
    return pages


def _load_text(text: str):
    """Plain text gets synthetic coordinates so the same code path works."""
    lines = text.splitlines() or [""]
    n = max(len(lines), 1)
    words = []
    for row, line in enumerate(lines):
        col = 0
        for token in line.split():
            start = line.index(token, col)
            col = start + len(token)
            words.append({
                "text": token,
                "x0": round(start / 90, 5),
                "y0": round(row / n, 5),
                "x1": round(col / 90, 5),
                "y1": round((row + 0.8) / n, 5),
            })
    return [Page(page=1, width=612.0, height=792.0, words=words, text=text)]


# --- OCR results ------------------------------------------------------------

MD_NOISE = re.compile(r"[#*`>_|]+")


def pages_from_ocr(payload: dict):
    """Turn an OCR response into the same Page objects the PDF path produces.

    Everything downstream — fingerprinting, learned regions, value location —
    works on positioned words, so a scan has to arrive in that shape or the
    correction loop quietly stops working on it.

    OCR gives a rectangle and the text for each paragraph block, never
    per-word coordinates, so the words here are reconstructed: the block's
    lines are laid out down its height and each line's tokens spread across
    its width in proportion to their length. In a one-line block — a header, a
    reference, a total, which is what people actually mark — that is close to
    exact. In a tall paragraph it is an estimate, accurate enough to say which
    block a drawn rectangle refers to but not where a word sits inside it.
    """
    out = []
    for i, page in enumerate(payload.get("pages") or [], start=1):
        dims = page.get("dimensions") or {}
        w = float(dims.get("width") or 0) or 1.0
        h = float(dims.get("height") or 0) or 1.0
        markdown = page.get("markdown") or ""

        words = []
        for block in page.get("blocks") or []:
            words.extend(_words_from_block(block, w, h))

        out.append(Page(page=int(page.get("index", i - 1)) + 1,
                        width=w, height=h, words=words, text=markdown))

    if not out:
        raise ValueError("The OCR service returned no pages for that file.")
    return out


def _words_from_block(block, w, h):
    content = (block.get("content") or "").strip()
    if not content:
        return []
    try:
        x0, y0 = float(block["top_left_x"]) / w, float(block["top_left_y"]) / h
        x1, y1 = float(block["bottom_right_x"]) / w, float(block["bottom_right_y"]) / h
    except (KeyError, TypeError, ValueError):
        return []
    x0, x1 = sorted((_clamp(x0), _clamp(x1)))
    y0, y1 = sorted((_clamp(y0), _clamp(y1)))

    lines = [l for l in MD_NOISE.sub(" ", content).splitlines() if l.strip()]
    if not lines:
        return []

    words = []
    for row, line in enumerate(lines):
        ly0 = y0 + (y1 - y0) * row / len(lines)
        ly1 = y0 + (y1 - y0) * (row + 1) / len(lines)
        span = max(len(line), 1)
        col = 0
        for token in line.split():
            start = line.index(token, col)
            col = start + len(token)
            words.append({
                "text": token,
                "x0": round(x0 + (x1 - x0) * start / span, 5),
                "y0": round(ly0, 5),
                "x1": round(x0 + (x1 - x0) * col / span, 5),
                "y1": round(ly1, 5),
            })
    return words


def _clamp(v):
    return 0.0 if v < 0 else (1.0 if v > 1 else v)


def full_text(pages, limit=12000):
    return "\n\n".join(p["text"] for p in pages)[:limit]


# --- fingerprint ------------------------------------------------------------

def layout_tokens(pages, limit=40):
    """The fixed words at the top of page one — a template's own labels.

    The same words the layout fingerprint hashes, kept as a list so two
    documents can be compared for similarity instead of only for equality. On
    a scan, OCR reads a word differently from one upload to the next often
    enough that an exact hash stops matching; the words themselves mostly
    survive, so overlap still identifies the template.
    """
    head = pages[0]
    return sorted({
        w["text"].lower()
        for w in head["words"]
        if w["y0"] < 0.34 and len(w["text"]) > 3 and not any(ch.isdigit() for ch in w["text"])
    })[:limit]


def layout_similarity(a, b):
    """How alike two token lists are, 0..1 — shared words over all words seen."""
    first, second = set(a or []), set(b or [])
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def fingerprint(pages):
    """A stable ID for 'documents that look like this one, from this sender'.

    Strong signal first: a VAT ID or IBAN identifies the issuer outright.
    Otherwise fall back to a layout signature — the non-numeric words in the
    top third of page one, which are the fixed labels of the template while
    amounts and dates change from document to document.
    """
    head = pages[0]
    text = head["text"]

    vat = VAT_RE.search(text.replace(" ", " "))
    if vat:
        return "vat:" + _hash(vat.group(0).replace(" ", "").upper())

    iban = IBAN_RE.search(text)
    if iban:
        return "iban:" + _hash(iban.group(0).replace(" ", "").upper())

    tokens = sorted({
        w["text"].lower()
        for w in head["words"]
        if w["y0"] < 0.34 and len(w["text"]) > 3 and not any(ch.isdigit() for ch in w["text"])
    })
    return "layout:" + _hash("|".join(tokens[:40]))


def _hash(s):
    return hashlib.sha1(s.encode()).hexdigest()[:16]


# --- regions ----------------------------------------------------------------

def text_in_bbox(pages, page_no, bbox, pad=0.004):
    """Every word whose centre falls inside the rectangle, in reading order."""
    page = next((p for p in pages if p["page"] == page_no), None)
    if page is None:
        return ""
    x0, y0, x1, y1 = bbox
    x0, y0, x1, y1 = min(x0, x1) - pad, min(y0, y1) - pad, max(x0, x1) + pad, max(y0, y1) + pad
    hits = [
        w for w in page["words"]
        if x0 <= (w["x0"] + w["x1"]) / 2 <= x1 and y0 <= (w["y0"] + w["y1"]) / 2 <= y1
    ]
    hits.sort(key=lambda w: (round(w["y0"], 2), w["x0"]))
    return " ".join(w["text"] for w in hits).strip()


def _squash(s):
    return re.sub(r"\s+", " ", s or "").strip().lower()


def value_near(pages, anchor, relation, gap=0.055):
    """Read the value sitting to the right of, or below, `anchor`.

    A label and its value are related by position, not by being on the same
    extracted line. In a table the heading is above its column; in a form the
    caption is to the left of the box. Reading order flattens both of those
    into lines that put the two far apart, which is why a line-based anchor
    fails on exactly the documents people actually send.

    Works off the same normalised word boxes a drawn rectangle uses, so it
    needs no coordinates of its own — only that the label still appears.
    """
    found = locate(anchor, pages)
    if not found:
        return ""
    page = next((p for p in pages if p["page"] == found["page"]), None)
    if page is None:
        return ""
    ax0, ay0, ax1, ay1 = found["bbox"]

    if relation == "right_of":
        band = max(0.012, (ay1 - ay0) * 0.7)
        centre = (ay0 + ay1) / 2
        row = [w for w in page["words"]
               if w["x0"] >= ax1 - 0.004
               and abs((w["y0"] + w["y1"]) / 2 - centre) <= band]
        row.sort(key=lambda w: w["x0"])
        return _join_until_gap(row, gap)

    if relation == "below":
        span = max(0.02, (ax1 - ax0) * 0.6)
        centre = (ax0 + ax1) / 2
        under = [w for w in page["words"]
                 if w["y0"] >= ay1 - 0.004
                 and abs((w["x0"] + w["x1"]) / 2 - centre) <= span]
        if not under:
            return ""
        # The nearest row beneath the label, not everything in the column.
        top = min(w["y0"] for w in under)
        row = [w for w in under if w["y0"] - top <= 0.012]
        row.sort(key=lambda w: w["x0"])
        return " ".join(w["text"] for w in row).strip()

    return ""


def _join_until_gap(words, gap):
    """Words in a row up to the first wide blank — a table's next column."""
    out = []
    for i, word in enumerate(words):
        if i and word["x0"] - words[i - 1]["x1"] > gap:
            break
        out.append(word["text"])
    return " ".join(out).strip()


def text_after_anchor(pages, anchor, limit=90):
    """The text that follows `anchor` in the document, or "".

    The anchor is whatever sat in front of the value when a colleague corrected
    it — usually a label like "Auftragsnummer:". Matching on text rather than
    geometry is what lets a correction work on a document with no usable
    coordinates: a text file, a plain-text preview, a scan whose OCR gives no
    boxes. It is less precise than a rectangle, which is why the rectangle is
    tried first when there is one.

    Stops at a line break, so a label at the end of a line cannot swallow the
    rest of the page.
    """
    needle = _squash(anchor)
    if not needle:
        return ""
    # Case-insensitive and tolerant of runs of whitespace, but matched against
    # the original line so the value keeps the casing the document uses —
    # "ZZ-4471", not "zz-4471".
    pattern = re.compile(r"\s+".join(re.escape(w) for w in needle.split()), re.IGNORECASE)
    for page in pages:
        for line in (page["text"] or "").splitlines():
            found = pattern.search(line)
            if not found:
                continue
            tail = line[found.end():].strip(" :#\t-–—")
            if tail:
                return tail[:limit].strip()
    # The label may sit on its own line with the value on the next one.
    for page in pages:
        lines = [l for l in (page["text"] or "").splitlines()]
        for i, line in enumerate(lines[:-1]):
            if needle in _squash(line) and _squash(lines[i + 1]):
                return lines[i + 1].strip()[:limit]
    return ""


def locate(value, pages):
    """Find where a value sits on the page, so the UI can highlight it.

    Matches the token sequence of the value against the word stream.
    Returns {page, bbox} or None.
    """
    if value in (None, ""):
        return None
    needle = [t for t in re.split(r"\s+", str(value).strip()) if t]
    if not needle:
        return None
    norm = lambda s: re.sub(r"[^\w.,/-]", "", str(s)).lower()
    target = [norm(t) for t in needle]

    for page in pages:
        words = page["words"]
        for i in range(len(words)):
            if norm(words[i]["text"]) != target[0]:
                continue
            span = words[i:i + len(target)]
            if len(span) < len(target):
                continue
            if [norm(w["text"]) for w in span] == target:
                return {
                    "page": page["page"],
                    "bbox": [
                        round(min(w["x0"] for w in span), 5),
                        round(min(w["y0"] for w in span), 5),
                        round(max(w["x1"] for w in span), 5),
                        round(max(w["y1"] for w in span), 5),
                    ],
                }
    # Fall back to a single-token substring match (handles "1.234,00 EUR" vs "1.234,00")
    flat = norm("".join(target))
    for page in pages:
        for w in page["words"]:
            if flat and flat in norm(w["text"]):
                return {"page": page["page"],
                        "bbox": [w["x0"], w["y0"], w["x1"], w["y1"]]}
    return None
