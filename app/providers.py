"""Where the actual reading happens.

Two providers, same interface:

    extract(schema, pages, hints) -> {field_path: {"value":..., "confidence":0..1}}

`MistralProvider` is the real one — Mistral is a French company processing in
the EU, which satisfies the hard constraint. No model of ours is trained;
we send a JSON Schema and let structured output do the mapping.

`OfflineProvider` is a label-proximity heuristic so the prototype runs on a
laptop with no API key and no network. It is not the product — it exists so
the demo never depends on a key being present, and so the learning loop can be
shown end to end offline.

Both consume the same company hints, which is the point: learning lives in
retrieval, never in weights.
"""

import base64
import json
import os
import re

import httpx

from . import document

MISTRAL_KEY = os.environ.get("MISTRAL_API_KEY", "")
MISTRAL_MODEL = os.environ.get("MISTRAL_MODEL", "mistral-large-latest")
MISTRAL_OCR_MODEL = os.environ.get("MISTRAL_OCR_MODEL", "mistral-ocr-latest")
MISTRAL_BASE = "https://api.mistral.ai/v1"


def get_provider():
    return MistralProvider() if MISTRAL_KEY else OfflineProvider()


# --- OCR --------------------------------------------------------------------

def ocr_available():
    """Scans need a real OCR service. There is no offline equivalent."""
    return bool(MISTRAL_KEY)


def ocr(data: bytes, filename: str):
    """Read a scan or a photo into pages of positioned words.

    Block-level boxes are what make the correction loop survive OCR, so ask
    for them. They need OCR 4 or newer; on an older model the response simply
    carries no blocks and the caller finds pages with text but no words.

    A photo is turned the right way up first. The coordinates that come back
    are in the grid of whatever was sent, so sending the grid the reader is
    shown is the whole of what makes a drawn rectangle mean the same thing at
    both ends.
    """
    data = document.upright(data, filename)
    mime = document.sniff(data, filename)
    encoded = base64.b64encode(data).decode()
    if mime.startswith("image/"):
        doc = {"type": "image_url", "image_url": f"data:{mime};base64,{encoded}"}
    else:
        doc = {"type": "document_url",
               "document_url": f"data:application/pdf;base64,{encoded}"}

    r = httpx.post(
        f"{MISTRAL_BASE}/ocr",
        headers={"Authorization": f"Bearer {MISTRAL_KEY}"},
        json={
            "model": MISTRAL_OCR_MODEL,
            "document": doc,
            "include_blocks": True,
            "include_image_base64": False,
        },
        timeout=180,
    )
    r.raise_for_status()
    return document.pages_from_ocr(r.json())


# --- shared -----------------------------------------------------------------

def _fields(schema):
    """Flatten a JSON Schema into [(path, spec)] — one level of nesting is plenty here."""
    out = []
    for name, spec in (schema.get("properties") or {}).items():
        if spec.get("type") == "object" and spec.get("properties"):
            for sub, subspec in spec["properties"].items():
                out.append((f"{name}.{sub}", subspec))
        else:
            out.append((name, spec))
    return out


def _coerce(value, spec):
    if value in (None, ""):
        return None
    if spec.get("type") == "number":
        s = re.sub(r"[^\d.,-]", "", str(value))
        if s.count(",") and s.count("."):                       # 1.234,56 or 1,234.56
            s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") \
                else s.replace(",", "")
        elif s.count(","):
            s = s.replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return None
    return str(value).strip()


def read_hint(hint, pages):
    """What a stored correction says the value is on *this* document.

    Two ways of remembering, tried in order of precision:

      1. the rectangle, when there is one and the page has coordinates;
      2. the text anchor — the label the value sat behind — which needs only
         text and therefore works on documents a rectangle cannot describe.

    Returns (value, how) where `how` is "region", "anchor" or "" for neither.
    """
    if hint.get("bbox"):
        here = _strip_label(document.text_in_bbox(pages, hint["page"], hint["bbox"]))
        if here:
            return here, "region"

    anchor = hint.get("anchor")
    if anchor:
        relation = hint.get("anchor_rel") or "line"
        # A label beside or above its value is how tables and forms are laid
        # out, and reading order tells you nothing about it — so position is
        # tried before falling back to the same-line reading.
        if relation in ("right_of", "below"):
            near = document.value_near(pages, anchor, relation)
            if near:
                return near, relation
        after = document.text_after_anchor(pages, anchor)
        if after:
            return after, "anchor"
        # A label recorded as beside-or-above may sit inline on another
        # document, and the reverse; try the other reading rather than give up.
        for other in ("right_of", "below"):
            if other != relation:
                near = document.value_near(pages, anchor, other)
                if near:
                    return near, other
    return "", ""


def _hint_block(hints, pages):
    """Turn stored corrections into prompt context."""
    lines = []
    for h in hints:
        value, how = read_hint(h, pages)
        if how == "region":
            where = f'in region {[round(v, 3) for v in h["bbox"]]} on page {h["page"]}'
        elif how == "right_of":
            where = f'immediately right of "{h["anchor"]}"'
        elif how == "below":
            where = f'directly beneath "{h["anchor"]}"'
        elif how == "anchor":
            where = f'just after "{h["anchor"]}"'
        else:
            where = "on this document type"
        if how and how != "region" and h.get("cross_layout"):
            where += " on a similar document"
        lines.append(
            f'- {h["field_path"]}: a colleague marked this {where}. '
            f'Text found there on the current document: "{value or "(empty)"}". '
            f'Previously corrected to: "{h.get("corrected_value") or "—"}".'
        )
    return "\n".join(lines)


# --- Mistral ----------------------------------------------------------------

class MistralProvider:
    name = "mistral"

    def extract(self, schema, pages, hints):
        text = document.full_text(pages)
        system = (
            "You read business documents and return data that matches a given JSON Schema "
            "exactly. Return only values that appear in the document. If a field is not "
            "present, return null for it rather than guessing. Never invent a value."
        )
        user = [f"Document text:\n\n{text}\n"]
        if hints:
            user.append(
                "\nColleagues at this company have previously corrected this document type. "
                "Treat the region text below as strong evidence:\n" + _hint_block(hints, pages)
            )
        user.append("\nReturn one JSON object matching the schema.")

        body = {
            "model": MISTRAL_MODEL,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": "\n".join(user)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "target_structure",
                    "schema": _strict(schema),
                    "strict": True,
                },
            },
        }
        r = httpx.post(
            f"{MISTRAL_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {MISTRAL_KEY}"},
            json=body,
            timeout=90,
        )
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"]
        data = json.loads(raw)

        result = {}
        by_hint = {h["field_path"]: h for h in hints}
        for path, spec in _fields(schema):
            val = data.get(path) if "." not in path else \
                (data.get(path.split(".")[0]) or {}).get(path.split(".")[1])

            # A correction decides the field. Passing it to the model as
            # evidence is not enough: the model has its own confident reading
            # and often keeps it, so the user marks a field, sees it change,
            # uploads the same document again and finds the old value back.
            # Whoever marked it knows where the value is; the offline reader
            # has always treated it that way and this one now does too.
            hint = by_hint.get(path)
            if hint:
                remembered, how = read_hint(hint, pages)
                if remembered:
                    result[path] = {
                        "value": _coerce(remembered, spec),
                        "confidence": 0.95 if how == "region"
                        else (0.8 if hint.get("cross_layout") else 0.9),
                    }
                    continue

            result[path] = {
                "value": _coerce(val, spec),
                # Hinted but unreadable here: the model's answer, and a score
                # that says it is not the remembered one.
                "confidence": 0.82 if val not in (None, "") else 0.0,
            }
        return result


def _strict(schema):
    """Mistral's strict mode wants every property listed as required and no extras."""
    s = json.loads(json.dumps(schema))
    props = s.get("properties") or {}
    s["required"] = list(props.keys())
    s["additionalProperties"] = False
    return s


# --- Offline heuristic ------------------------------------------------------

# Words in a field description that say nothing about which label to look for.
STOPWORDS = {
    "the", "of", "for", "and", "this", "that", "our", "its", "a", "an", "in", "on",
    "to", "be", "is", "should", "eg", "iso", "yyyy", "mm", "dd", "issued", "company",
    "legal", "related", "identification", "which", "own",
}

# Small synonym table. Not domain knowledge about invoices — just the everyday
# alternatives for words that appear in field names across all document types.
SYNONYMS = {
    "number": {"no", "nr", "num", "nº"},
    "id": {"no", "nr", "ident"},
    "amount": {"sum", "value"},
    "total": {"gross", "sum", "gesamt"},
    "net": {"subtotal", "netto"},
    "tax": {"vat", "mwst", "ust", "gst"},
    "date": {"dated", "datum"},
    "due": {"payable", "fällig", "faellig"},
    "supplier": {"vendor", "seller", "issuer", "lieferant"},
    "shipper": {"sender", "carrier"},
    "reference": {"ref", "order", "po"},
    "currency": {"ccy"},
    "count": {"qty", "quantity", "pieces"},
}

PAIR_RE = re.compile(r"^\s*(?P<label>[^:#\n]{2,44}?)\s*[:#]\s*(?P<value>\S.*)$")


class OfflineProvider:
    """Label-proximity matching, no network, no key.

    Finds "Label: value" pairs in the text and assigns each one to whichever
    schema field its label matches best, globally rather than field by field —
    so "Invoice Date" and "Due Date" cannot both be claimed by the same field.

    This exists so the prototype runs anywhere. The real provider is Mistral.
    """

    name = "offline-heuristic"

    def extract(self, schema, pages, hints):
        text = document.full_text(pages)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        pairs = []
        for i, line in enumerate(lines):
            m = PAIR_RE.match(line)
            if m:
                pairs.append({"label": m.group("label").strip(),
                              "value": m.group("value").strip(), "line": i})

        by_hint = {h["field_path"]: h for h in hints}
        specs = _fields(schema)
        result = {}

        # 1. Learned corrections win outright — rectangle, else text anchor.
        remaining = []
        for path, spec in specs:
            hint = by_hint.get(path)
            if not hint:
                remaining.append((path, spec))
                continue
            here, how = read_hint(hint, pages)
            if here:
                # A rectangle on this very layout is the strongest evidence; an
                # anchor carried in from another layout is the weakest.
                score = 0.95 if how == "region" else (0.8 if hint.get("cross_layout") else 0.9)
                # Positional readings are as trustworthy as the label is.
                result[path] = {"value": _coerce(here, spec), "confidence": score}
            elif hint.get("corrected_value") and hint["corrected_value"] in text:
                # The remembered rectangle is empty but the old value is still on
                # the page — the layout shifted. Usable, but say so with the score.
                result[path] = {"value": _coerce(hint["corrected_value"], spec), "confidence": 0.65}
            else:
                # Never hand back a value copied from a different document.
                result[path] = {"value": None, "confidence": 0.0}
            continue

        # 2. Regex-shaped fields.
        still = []
        for path, spec in remaining:
            value, conf = self._by_shape(path, spec, lines, text)
            if value is not None:
                result[path] = {"value": _coerce(value, spec), "confidence": conf}
            else:
                still.append((path, spec))

        # 3. Global best match between fields and label/value pairs.
        scored = []
        for path, spec in still:
            wanted = self._terms(path, spec)
            for pi, pair in enumerate(pairs):
                s = _score(wanted, pair["label"])
                if s > 0:
                    scored.append((s, path, pi))
        scored.sort(key=lambda t: (-t[0], t[2]))
        used_fields, used_pairs = set(), set()
        for s, path, pi in scored:
            if path in used_fields or pi in used_pairs:
                continue
            used_fields.add(path)
            used_pairs.add(pi)
            spec = dict(still)[path]
            result[path] = {"value": _coerce(_first_value(pairs[pi]["value"], spec), spec),
                            "confidence": 0.85 if s >= 1.0 else 0.6}

        for path, spec in still:
            result.setdefault(path, {"value": None, "confidence": 0.0})
        return result

    def _by_shape(self, path, spec, lines, text):
        """Fields whose format identifies them on sight."""
        tokens = _tokens(path)
        if tokens & {"vat", "ustid", "taxid"} and "id" in " ".join(tokens) + path:
            m = document.VAT_RE.search(text)
            if m:
                return m.group(0), 0.88
        if "iban" in tokens:
            m = document.IBAN_RE.search(text)
            if m:
                return m.group(0).strip(), 0.88
        if "currency" in tokens:
            m = re.search(r"\b(EUR|USD|GBP|CHF|SEK|NOK|DKK|PLN|CZK)\b", text)
            if m:
                return m.group(1), 0.8
        if tokens & {"supplier", "shipper", "issuer", "vendor", "counterparty"} and "name" in tokens:
            for l in lines[:3]:
                if len(l) > 3 and not PAIR_RE.match(l) and not l.isupper():
                    return l, 0.55
        return None, 0.0

    def _terms(self, path, spec):
        """The words that would plausibly appear in this field's label."""
        base = _tokens(path)
        terms = set(base)
        for t in base:
            terms |= SYNONYMS.get(t, set())
        for w in re.findall(r"[a-zA-Zäöüß]{3,}", (spec.get("description") or "").lower()):
            if w not in STOPWORDS and w not in terms:
                terms.add(w)
        return {"core": base, "all": terms}


def _tokens(path):
    return {t for t in re.split(r"[_.\s]+", path.lower()) if t and t not in STOPWORDS}


def _in_label(alts, label_tokens):
    """Whole-token match, or a term sitting inside a compound word.

    German labels arrive welded together — "Nettobetrag", "Gesamtbetrag",
    "Rechnungsnummer" — so a token-set intersection alone misses them. Four
    characters is the floor, which keeps "no" and "id" from matching half the
    page.
    """
    if alts & label_tokens:
        return True
    return any(len(a) >= 4 and any(a in t for t in label_tokens) for a in alts)


def _score(wanted, label):
    """1.0 when every word of the field name is in the label; less when partial."""
    label_tokens = {t for t in re.split(r"[^a-zA-Z0-9äöüß]+", label.lower()) if t}
    core = wanted["core"]
    if not core:
        return 0.0
    hits = 0
    for t in core:
        alts = {t} | SYNONYMS.get(t, set())
        if _in_label(alts, label_tokens):
            hits += 1
    if hits == 0:
        return 0.0
    coverage = hits / len(core)
    # Prefer a tight label: "Invoice No" over "Invoice No and other nonsense".
    tightness = hits / max(len(label_tokens), 1)
    return round(coverage + 0.25 * tightness, 4)


def _first_value(chunk, spec):
    chunk = chunk.strip(" :#\t")
    if spec.get("type") == "number":
        m = re.search(r"-?\d[\d.,]*", chunk)
        return m.group(0) if m else chunk
    return chunk.strip()


def _strip_label(region_text):
    """A drawn rectangle often catches the label too. Drop a leading 'Something:'."""
    if not region_text:
        return region_text
    return re.sub(r"^[A-Za-zÄÖÜäöüß .\-/]{2,30}[:#]\s*", "", region_text).strip() or region_text
