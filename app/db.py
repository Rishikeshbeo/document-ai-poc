"""SQLite persistence.

Deliberately tiny: four tables, no ORM, no migrations. A POC should be
readable in one sitting.

GDPR note: no document bytes and no full document text are ever stored.
The `hints` table holds only a field path, a page number, a rectangle and a
short text snippet. `runs` holds counters for the demo dashboard.
"""

import json
import os
import sqlite3
import secrets
import time
from contextlib import contextmanager

DB_PATH = os.environ.get("DOCAI_DB", os.path.join(os.path.dirname(__file__), "..", "docai.sqlite3"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    api_key       TEXT NOT NULL UNIQUE,
    target_schema TEXT NOT NULL,
    iframe_origin TEXT NOT NULL DEFAULT '*',
    -- The client this application belongs to, and the id every correction is
    -- filed under. Registered here so an embed need not carry it: one client,
    -- one application, one pool of learning.
    company_id    TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS hints (
    id              TEXT PRIMARY KEY,
    app_id          TEXT NOT NULL,
    company_id      TEXT NOT NULL,
    fingerprint     TEXT NOT NULL,
    field_path      TEXT NOT NULL,
    page            INTEGER NOT NULL,
    bbox            TEXT NOT NULL,
    snippet         TEXT,
    corrected_value TEXT,
    -- The text that sits immediately before the value, e.g. "Auftragsnummer:".
    -- A rectangle is precise but needs a rendered page with coordinates, which
    -- a text file, a plain-text preview and some scans do not have. An anchor
    -- needs only text, so a correction can be recorded on any document we can
    -- read at all.
    anchor          TEXT,
    -- How the value relates to the anchor: "right_of" or "below" for a label
    -- positioned beside or above it, "line" for the same extracted line.
    anchor_rel      TEXT,
    -- The template's own words, so a document whose fingerprint has shifted —
    -- OCR reading one word differently is enough — can still be recognised by
    -- overlap rather than only by an exact hash.
    layout_tokens   TEXT,
    hit_count       INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hints_lookup
    ON hints (app_id, company_id, fingerprint);

CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    app_id      TEXT NOT NULL,
    company_id  TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    mode        TEXT NOT NULL,
    hints_used  INTEGER NOT NULL DEFAULT 0,
    provider    TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def new_id(prefix):
    return f"{prefix}_{secrets.token_hex(6)}"


# --- applications -----------------------------------------------------------

def create_application(name, target_schema, iframe_origin="*", company_id=""):
    app_id = new_id("app")
    key = "dk_live_" + secrets.token_urlsafe(24)
    with conn() as c:
        c.execute(
            "INSERT INTO applications (id, name, api_key, target_schema, iframe_origin,"
            " company_id, created_at) VALUES (?,?,?,?,?,?,?)",
            (app_id, name, key, json.dumps(target_schema), iframe_origin,
             company_id, time.time()),
        )
    return get_application(app_id)


def get_application(app_id):
    with conn() as c:
        row = c.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
    return _app(row)


def get_application_by_key(api_key):
    with conn() as c:
        row = c.execute("SELECT * FROM applications WHERE api_key=?", (api_key,)).fetchone()
    return _app(row)


def list_applications():
    with conn() as c:
        rows = c.execute("SELECT * FROM applications ORDER BY created_at").fetchall()
    return [_app(r) for r in rows]


def update_application(app_id, name=None, target_schema=None, iframe_origin=None,
                       company_id=None):
    app = get_application(app_id)
    if not app:
        return None
    with conn() as c:
        c.execute(
            "UPDATE applications SET name=?, target_schema=?, iframe_origin=?,"
            " company_id=? WHERE id=?",
            (
                name if name is not None else app["name"],
                json.dumps(target_schema) if target_schema is not None else json.dumps(app["target_schema"]),
                iframe_origin if iframe_origin is not None else app["iframe_origin"],
                company_id if company_id is not None else app.get("company_id", ""),
                app_id,
            ),
        )
    return get_application(app_id)


def delete_application(app_id):
    """Remove an application and everything filed under it.

    Its corrections and run counters go too. Leaving them would be orphaned
    rows that no longer belong to any client, which is both untidy and the
    wrong answer if the reason for deleting is that the client left.
    """
    app = get_application(app_id)
    if not app:
        return None
    with conn() as c:
        hints = c.execute("DELETE FROM hints WHERE app_id=?", (app_id,)).rowcount
        runs = c.execute("DELETE FROM runs WHERE app_id=?", (app_id,)).rowcount
        c.execute("DELETE FROM applications WHERE id=?", (app_id,))
    return {"deleted": app_id, "name": app["name"],
            "hints_deleted": hints, "runs_deleted": runs}


def reissue_api_key(app_id):
    """Mint a new key for an application and return it once.

    The key is shown at registration and never listed again, which is right for
    a credential and useless the first time somebody loses one. Reissuing keeps
    that property: there is still no way to read the current key, only to
    replace it. The old key stops working immediately.
    """
    app = get_application(app_id)
    if not app:
        return None
    key = "dk_live_" + secrets.token_urlsafe(24)
    with conn() as c:
        c.execute("UPDATE applications SET api_key=? WHERE id=?", (key, app_id))
    fresh = get_application(app_id)
    fresh["api_key"] = key
    return fresh


def _app(row):
    if row is None:
        return None
    d = dict(row)
    d["target_schema"] = json.loads(d["target_schema"])
    return d


# --- hints ------------------------------------------------------------------

def save_hint(app_id, company_id, fingerprint, field_path, page, bbox, snippet,
              corrected_value, anchor=None, anchor_rel=None, layout_tokens=None):
    """Upsert on (app, company, fingerprint, field_path) — one hint per field."""
    with conn() as c:
        c.execute(
            "DELETE FROM hints WHERE app_id=? AND company_id=? AND fingerprint=? AND field_path=?",
            (app_id, company_id, fingerprint, field_path),
        )
        c.execute(
            "INSERT INTO hints (id, app_id, company_id, fingerprint, field_path, page, bbox,"
            " snippet, corrected_value, anchor, anchor_rel, layout_tokens,"
            " hit_count, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
            (
                new_id("hint"), app_id, company_id, fingerprint, field_path, page,
                json.dumps(bbox or []), snippet, corrected_value, anchor,
                anchor_rel, json.dumps(layout_tokens or []), time.time(),
            ),
        )


def get_hints(app_id, company_id, fingerprint):
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM hints WHERE app_id=? AND company_id=? AND fingerprint=?",
            (app_id, company_id, fingerprint),
        ).fetchall()
    return [_hint(r) for r in rows]


def get_anchor_hints(app_id, company_id, exclude_fingerprint):
    """This company's corrections from *other* layouts that carry a text anchor.

    The fingerprint is an exact hash, so one changed word in the top third of
    page one — a branch name, a reprint stamp, a word OCR read differently —
    produces a different layout and the correction stops applying. That is the
    difference between "it learns" and "it learns sometimes".

    An anchor does not depend on the layout: if the label is on the page, the
    value can be found. Rectangles are deliberately not carried over, because a
    coordinate from another layout means nothing. Most-used first, so the
    correction a company relies on wins.
    """
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM hints WHERE app_id=? AND company_id=? AND fingerprint!=?"
            " AND anchor IS NOT NULL AND TRIM(anchor) != ''"
            " ORDER BY hit_count DESC, created_at DESC",
            (app_id, company_id, exclude_fingerprint),
        ).fetchall()
    return [_hint(r) for r in rows]


def list_hints(app_id=None, company_id=None):
    q = "SELECT * FROM hints WHERE 1=1"
    args = []
    if app_id:
        q += " AND app_id=?"
        args.append(app_id)
    if company_id:
        q += " AND company_id=?"
        args.append(company_id)
    q += " ORDER BY created_at DESC"
    with conn() as c:
        rows = c.execute(q, args).fetchall()
    return [_hint(r) for r in rows]


def bump_hits(hint_ids):
    if not hint_ids:
        return
    with conn() as c:
        c.executemany("UPDATE hints SET hit_count = hit_count + 1 WHERE id=?", [(i,) for i in hint_ids])


def delete_hint(hint_id):
    with conn() as c:
        c.execute("DELETE FROM hints WHERE id=?", (hint_id,))


def delete_company_data(app_id, company_id):
    """GDPR erasure: everything we hold for one company under one application."""
    with conn() as c:
        h = c.execute("DELETE FROM hints WHERE app_id=? AND company_id=?", (app_id, company_id)).rowcount
        r = c.execute("DELETE FROM runs WHERE app_id=? AND company_id=?", (app_id, company_id)).rowcount
    return {"hints_deleted": h, "runs_deleted": r}


def get_company_hints(app_id, company_id):
    """Every correction this company has made under this application."""
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM hints WHERE app_id=? AND company_id=?"
            " ORDER BY hit_count DESC, created_at DESC",
            (app_id, company_id),
        ).fetchall()
    return [_hint(r) for r in rows]


def _hint(row):
    d = dict(row)
    d["bbox"] = json.loads(d["bbox"])
    try:
        d["layout_tokens"] = json.loads(d.get("layout_tokens") or "[]")
    except (TypeError, ValueError):
        d["layout_tokens"] = []
    return d


# --- settings ---------------------------------------------------------------

def get_setting(key, default=None):
    with conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    with conn() as c:
        c.execute("INSERT INTO settings (key, value) VALUES (?,?)"
                  " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    return value


# --- runs -------------------------------------------------------------------

def log_run(app_id, company_id, fingerprint, mode, hints_used, provider):
    rid = new_id("run")
    with conn() as c:
        c.execute(
            "INSERT INTO runs (id, app_id, company_id, fingerprint, mode, hints_used, provider, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (rid, app_id, company_id, fingerprint, mode, hints_used, provider, time.time()),
        )
    return rid


def list_runs(limit=25):
    with conn() as c:
        rows = c.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# --- bootstrap --------------------------------------------------------------

DEMO_SCHEMA = {
    "type": "object",
    "title": "Incoming invoice",
    "properties": {
        "invoice_number": {"type": "string", "description": "The invoice number issued by the supplier"},
        "invoice_date":   {"type": "string", "description": "Invoice date in YYYY-MM-DD"},
        "due_date":       {"type": "string", "description": "Payment due date in YYYY-MM-DD"},
        "supplier_name":  {"type": "string", "description": "Legal name of the company that issued the invoice"},
        "supplier_vat_id": {"type": "string", "description": "VAT identification number of the supplier"},
        "customer_reference": {"type": "string", "description": "Our own order or customer reference on the invoice"},
        "net_amount":     {"type": "number", "description": "Total net amount before tax"},
        "tax_amount":     {"type": "number", "description": "Total tax amount"},
        "total_amount":   {"type": "number", "description": "Gross total to be paid"},
        "currency":       {"type": "string", "description": "ISO 4217 currency code, e.g. EUR"},
        "iban":           {"type": "string", "description": "Bank account the payment should go to"},
    },
    "required": ["invoice_number", "total_amount", "supplier_name"],
}

DELIVERY_NOTE_SCHEMA = {
    "type": "object",
    "title": "Delivery note",
    "properties": {
        "delivery_note_number": {"type": "string", "description": "Number of the delivery note"},
        "delivery_date": {"type": "string", "description": "Date of delivery in YYYY-MM-DD"},
        "shipper_name": {"type": "string", "description": "Company that shipped the goods"},
        "order_number": {"type": "string", "description": "Related purchase order number"},
        "package_count": {"type": "number", "description": "Number of packages delivered"},
    },
    "required": ["delivery_note_number"],
}


def init(seed=True):
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)
        # The one migration this prototype has. CREATE TABLE IF NOT EXISTS
        # leaves an older hints table alone, so a database made before anchors
        # existed needs the column adding by hand.
        cols = {r["name"] for r in c.execute("PRAGMA table_info(hints)")}
        for column in ("anchor", "anchor_rel", "layout_tokens"):
            if column not in cols:
                c.execute(f"ALTER TABLE hints ADD COLUMN {column} TEXT")
        app_cols = {r["name"] for r in c.execute("PRAGMA table_info(applications)")}
        if "company_id" not in app_cols:
            c.execute("ALTER TABLE applications ADD COLUMN company_id TEXT NOT NULL DEFAULT ''")
    if seed and not list_applications():
        with conn() as c:
            c.execute(
                "INSERT INTO applications (id, name, api_key, target_schema, iframe_origin,"
                " company_id, created_at) VALUES (?,?,?,?,?,?,?)",
                ("app_demo", "Finance — supplier invoices", "dk_test_demo_key",
                 json.dumps(DEMO_SCHEMA), "*", "acme-gmbh", time.time()),
            )
        # Two seeded examples, named the way real ones should be: the team that
        # uses it, then the document it reads. Same engine, different target
        # structure, no code between them — which is the point of the pair.
        create_application("Logistics — delivery notes", DELIVERY_NOTE_SCHEMA,
                           company_id="acme-gmbh")
