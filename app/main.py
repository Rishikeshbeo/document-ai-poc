"""Document AI — POC.

Three surfaces on one small service:
  /admin   superadmin: applications, target JSON structures, learned regions
  /embed   the user panel, designed to live in an iframe, no login
  /host    an example host application that embeds the panel

Public API used by host applications:
  POST /api/session          X-API-Key  -> short-lived token + embed URL
  POST /api/extract          Bearer     -> structured JSON in the app's target shape
  POST /api/feedback         Bearer     -> store a corrected region, company-wide
  DELETE /api/company-data   X-API-Key  -> GDPR erasure for one company
"""

import json
import os
import re
import traceback

from fastapi import (APIRouter, Depends, FastAPI, File, Form, Header, HTTPException,
                     Request, UploadFile)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.formparsers import MultiPartParser

from . import db, document, providers, security

STATIC = os.path.join(os.path.dirname(__file__), "static")

MAX_UPLOAD = 15 * 1024 * 1024

# Starlette spools an upload to a temporary file once it grows past this, and a
# document written to disk is a document we would have to promise to delete.
# Lifting the threshold above our own size cap keeps every accepted file in
# memory, which is what makes "nothing is written to disk" true rather than
# nearly true. The middleware below turns away anything larger before the body
# is parsed, so this is not a licence to buffer without limit.
MultiPartParser.spool_max_size = MAX_UPLOAD + 1024 * 1024

app = FastAPI(title="Document AI (POC)", version="0.1.0", docs_url="/api/docs")
db.init()

if security.admin_is_generated():
    print(f"  superadmin /admin — {security.ADMIN_USER} / {security.admin_password()}"
          f"   (set DOCAI_ADMIN_PASSWORD to choose your own)", flush=True)


@app.middleware("http")
async def cap_request_size(request: Request, call_next):
    """Reject oversized uploads before anything reads or buffers them."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD + 512 * 1024:
        return JSONResponse({"error": "Files up to 15 MB in this build."}, status_code=413)
    return await call_next(request)


# ---------------------------------------------------------------- helpers ---

def _app_from_key(api_key: str):
    if not api_key:
        raise HTTPException(401, "Send your application API key in the X-API-Key header.")
    application = db.get_application_by_key(api_key.strip())
    if not application:
        raise HTTPException(401, "That API key does not match a registered application.")
    return application


def _session(authorization: str):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing session token.")
    try:
        return security.verify(authorization.split(" ", 1)[1].strip())
    except security.TokenError as e:
        raise HTTPException(401, str(e))


LAYOUT_MATCH = 0.62   # shared words over all words seen; below this it is a different template


def _closest_layout(company_hints, tokens):
    """The most similar layout this company has corrections for, and how similar.

    Grouped by fingerprint, because a layout is taught one field at a time and
    all of its hints travel together.
    """
    if not tokens:
        return [], 0.0
    by_layout = {}
    for hint in company_hints:
        by_layout.setdefault(hint["fingerprint"], []).append(hint)
    best, best_score = [], 0.0
    for group in by_layout.values():
        stored = next((h["layout_tokens"] for h in group if h.get("layout_tokens")), [])
        score = document.layout_similarity(tokens, stored)
        if score > best_score:
            best, best_score = group, score
    return (best, best_score) if best_score >= LAYOUT_MATCH else ([], best_score)


def _carries_over(anchor: str) -> bool:
    """Whether an anchor is safe to apply to a *different* layout.

    A real label — "Unsere Referenz:", "Auftragsnummer:" — means the same thing
    wherever it appears, so carrying it over is what makes a correction survive
    a changed header. An anchor that is merely the text that happened to precede
    the value, like a whole address line, is specific to the document it came
    from; on its own layout it still works, but let loose on another it would
    read whatever follows a coincidental match.
    """
    text = (anchor or "").strip()
    return bool(text) and (text.endswith((":", "#")) or len(text) <= 24)


def require_admin(authorization: str = Header(default="")):
    """Guards the superadmin area. 401 with a Basic challenge, so the browser
    asks for the password itself."""
    if not security.check_admin(authorization):
        raise HTTPException(
            401, "Superadmin area. Sign in with the admin password.",
            headers={"WWW-Authenticate": 'Basic realm="Document AI superadmin"'},
        )


# --------------------------------------------------------------- sessions ---

class SessionRequest(BaseModel):
    # Optional: an application registered with a company uses that one. A host
    # that serves several companies from one key may still pass its own.
    company_id: str = ""
    user_ref: str = ""


@app.post("/api/session")
def create_session(body: SessionRequest, request: Request, x_api_key: str = Header(default="")):
    """Called server-to-server by the host app. The user never sees this."""
    application = _app_from_key(x_api_key)
    company = body.company_id.strip() or (application.get("company_id") or "").strip()
    if not company:
        raise HTTPException(
            400,
            "No company for this session. Register the application with a company, "
            "or pass company_id — it is how learning is shared.",
        )
    token = security.mint(application["id"], company, body.user_ref)
    base = str(request.base_url).rstrip("/")
    return {
        "token": token,
        "expires_in": security.TTL_SECONDS,
        "embed_url": f"{base}/embed?token={token}",
        "application": {"id": application["id"], "name": application["name"]},
    }


# ---------------------------------------------------------------- extract ---

@app.post("/api/extract")
async def extract(
    file: UploadFile = File(...),
    mode: str = Form("normal"),
    authorization: str = Header(default=""),
):
    sess = _session(authorization)
    application = db.get_application(sess["app"])
    if not application:
        raise HTTPException(404, "This application no longer exists.")

    data = await file.read()
    if not data:
        raise HTTPException(400, "That file is empty.")
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "Files up to 15 MB in this build.")

    ocr_used = False
    try:
        try:
            pages = document.load(data, file.filename)
        except document.NeedsOCR:
            # A scan or a photo. Readable, but only by an OCR service.
            if not providers.ocr_available():
                raise HTTPException(
                    415,
                    "That file has no text layer — it is a scan or a photo. Reading it "
                    "needs the OCR pass, which requires MISTRAL_API_KEY to be set on "
                    "the server.",
                )
            try:
                pages = providers.ocr(data, file.filename)
            except Exception:
                traceback.print_exc()
                raise HTTPException(502, "The OCR service did not return a usable answer. Try again.")
            ocr_used = True
        except ValueError as e:
            raise HTTPException(415, str(e))
    finally:
        del data  # nothing persisted, nothing written to disk

    fp = document.fingerprint(pages)
    tokens = document.layout_tokens(pages)
    hints = db.get_hints(application["id"], sess["company"], fp)

    # The fingerprint is an exact hash, and on a scan it moves: OCR reading one
    # word differently between uploads is enough. So when it does not match,
    # look for the closest layout this company has actually taught us. Above
    # the threshold it is the same template and its rectangles still hold —
    # which is the difference between a correction sticking and a user marking
    # the same field over and over.
    if not hints:
        near, score = _closest_layout(
            db.get_company_hints(application["id"], sess["company"]), tokens)
        if near:
            hints = [{**h, "near_layout": round(score, 2)} for h in near]

    # Corrections from this company's other layouts, applied by their text
    # anchor only. Without this a correction is bound to one exact fingerprint,
    # so a document that differs by a single header word learns nothing — which
    # is most of them once real scans are involved. A field already corrected
    # for this exact layout keeps its own hint.
    covered = {h["field_path"] for h in hints}
    for other in db.get_anchor_hints(application["id"], sess["company"], fp):
        if other["field_path"] in covered or not _carries_over(other["anchor"]):
            continue
        covered.add(other["field_path"])
        # No rectangle: a coordinate from a different layout is meaningless.
        hints.append({**other, "bbox": [], "cross_layout": True})
    provider = providers.get_provider()

    try:
        raw = provider.extract(application["target_schema"], pages, hints)
    except Exception:
        traceback.print_exc()
        raise HTTPException(502, "The model provider did not return a usable answer. Try again.")

    hinted = {h["field_path"] for h in hints}
    fields, target = [], {}
    for path, spec in providers._fields(application["target_schema"]):
        got = raw.get(path) or {}
        value = got.get("value")
        loc = document.locate(value, pages) if value not in (None, "") else None
        fields.append({
            "path": path,
            "label": _label(path),
            "type": spec.get("type", "string"),
            "description": spec.get("description", ""),
            "value": value,
            "confidence": round(float(got.get("confidence") or 0), 2),
            "learned": path in hinted,
            "page": (loc or {}).get("page"),
            "bbox": (loc or {}).get("bbox"),
        })
        _assign(target, path, value)

    db.bump_hits([h["id"] for h in hints])
    run_id = db.log_run(application["id"], sess["company"], fp, mode, len(hints), provider.name)

    return {
        "run_id": run_id,
        "fingerprint": fp,
        "mode": mode,
        "provider": provider.name,
        "ocr": ocr_used,
        # Without word positions a drawn rectangle has nothing to read, so the
        # panel hides correction rather than saving a region it cannot honour.
        "regions_available": any(p["words"] for p in pages),
        # Sent back so a correction can record which template it was made on,
        # and recognised later by overlap rather than an exact hash.
        "layout_tokens": tokens,
        "hints_applied": len(hints),
        "page_count": len(pages),
        # Text and word boxes go back with the result so the panel can work out
        # where a corrected value sits and which label locates it, without a
        # second upload. It is the same document the browser already holds, and
        # it is not stored here. Capped so a 400-page file cannot balloon the
        # response; a marked value is on an early page in practice.
        "pages": [{"page": p["page"], "width": p["width"], "height": p["height"],
                   "text": p["text"][:20000],
                   "words": p["words"][:1500] if p["page"] <= 5 else []}
                  for p in pages],
        "fields": fields,
        "json": target,          # already in the application's target structure
    }


def _label(path):
    return path.split(".")[-1].replace("_", " ").capitalize()


def _assign(obj, path, value):
    parts = path.split(".")
    for p in parts[:-1]:
        obj = obj.setdefault(p, {})
    obj[parts[-1]] = value


# --------------------------------------------------------------- feedback ---

class Correction(BaseModel):
    field_path: str
    page: int = 1
    bbox: list = []     # [x0, y0, x1, y1] normalised 0..1 — omitted when the
                        # document has no coordinates to draw on
    anchor: str = ""    # the label that locates the value, used when there is
                        # no rectangle, and as a second chance when there is
    anchor_rel: str = ""  # "right_of" | "below" | "line" — where the value sits
                          # relative to that label
    value: str = ""


class Feedback(BaseModel):
    fingerprint: str
    corrections: list[Correction]
    # The template's words, so this layout can be recognised again even if its
    # fingerprint shifts. Sent by the panel; absent from older callers.
    layout_tokens: list[str] = []


@app.post("/api/feedback")
def feedback(body: Feedback, authorization: str = Header(default="")):
    """A training run. Stored against the company, so every colleague benefits."""
    sess = _session(authorization)
    if not body.corrections:
        raise HTTPException(400, "Mark at least one field before saving.")
    for c in body.corrections:
        if c.bbox and len(c.bbox) != 4:
            raise HTTPException(400, f"{c.field_path}: a region needs four coordinates.")
        if not c.bbox and not c.anchor.strip():
            raise HTTPException(
                400,
                f"{c.field_path}: send a region, or an anchor for a document with "
                "no coordinates. One of the two is what makes the correction findable again.",
            )
        db.save_hint(
            app_id=sess["app"],
            company_id=sess["company"],
            fingerprint=body.fingerprint,
            field_path=c.field_path,
            page=c.page,
            bbox=[float(v) for v in c.bbox] if c.bbox else [],
            snippet=(c.value or "")[:120],
            corrected_value=c.value or None,
            anchor=c.anchor.strip()[:120] or None,
            anchor_rel=(c.anchor_rel if c.anchor_rel in ("right_of", "below", "line") else None),
            layout_tokens=body.layout_tokens or None,
        )
    total = len(db.get_hints(sess["app"], sess["company"], body.fingerprint))
    return {"saved": len(body.corrections), "regions_for_this_layout": total,
            "company_id": sess["company"]}


@app.get("/api/session-info")
def session_info(authorization: str = Header(default="")):
    sess = _session(authorization)
    application = db.get_application(sess["app"])
    return {
        "company_id": sess["company"],
        "application": application["name"],
        # The panel posts results only to this origin. "*" means the
        # application has not registered one and anybody may embed it.
        "iframe_origin": application["iframe_origin"] or "*",
        "provider": providers.get_provider().name,
        "fields": [{"path": p, "label": _label(p), "type": s.get("type", "string"),
                    "description": s.get("description", "")}
                   for p, s in providers._fields(application["target_schema"])],
    }


@app.delete("/api/company-data")
def erase(company_id: str, x_api_key: str = Header(default="")):
    """GDPR erasure. Everything we hold for one company under one application."""
    application = _app_from_key(x_api_key)
    return db.delete_company_data(application["id"], company_id)


# ------------------------------------------------------------------ admin ---

class AppIn(BaseModel):
    name: str
    target_schema: dict
    iframe_origin: str = "*"
    company_id: str = ""


ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.\-]+(?::\d{1,5})?$")


def _clean_origin(value: str) -> str:
    """One origin, or "*". A trailing path is the usual mistake and cannot work.

    postMessage takes a single target origin, so this is deliberately not a
    list: the panel has to know which one window to hand the data to.
    """
    origin = (value or "*").strip().rstrip("/")
    if origin == "*":
        return "*"
    if not ORIGIN_RE.match(origin):
        raise HTTPException(
            400,
            f"iframe_origin must be scheme://host[:port] with no path, or \"*\". Got {value!r}.",
        )
    return origin


admin = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


@admin.get("/applications")
def admin_list():
    # The API key is a credential, not configuration. It is shown once, by the
    # create call that mints it, and never listed again.
    return [{k: v for k, v in a.items() if k != "api_key"} for a in db.list_applications()]


@admin.post("/applications")
def admin_create(body: AppIn):
    if not body.name.strip():
        raise HTTPException(400, "Give the application a name.")
    if not (body.target_schema.get("properties")):
        raise HTTPException(400, "The target structure needs at least one property.")
    return db.create_application(body.name.strip(), body.target_schema,
                                 _clean_origin(body.iframe_origin),
                                 body.company_id.strip())


@admin.put("/applications/{app_id}")
def admin_update(app_id: str, body: AppIn):
    updated = db.update_application(app_id, body.name, body.target_schema,
                                    _clean_origin(body.iframe_origin),
                                    body.company_id.strip())
    if not updated:
        raise HTTPException(404, "No such application.")
    return {k: v for k, v in updated.items() if k != "api_key"}


@admin.delete("/applications/{app_id}")
def admin_delete(app_id: str):
    """Remove a client application, its API key and everything it learned."""
    removed = db.delete_application(app_id)
    if not removed:
        raise HTTPException(404, "No such application.")
    return removed


@admin.post("/applications/{app_id}/key")
def admin_reissue_key(app_id: str):
    """Replace this client's API key and show the new one once."""
    updated = db.reissue_api_key(app_id)
    if not updated:
        raise HTTPException(404, "No such application.")
    return updated


@admin.get("/hints")
def admin_hints(app_id: str = "", company_id: str = ""):
    return db.list_hints(app_id or None, company_id or None)


@admin.delete("/hints/{hint_id}")
def admin_delete_hint(hint_id: str):
    db.delete_hint(hint_id)
    return {"deleted": hint_id}


@admin.get("/runs")
def admin_runs():
    return db.list_runs()


app.include_router(admin)


# ------------------------------------------------------------------ pages ---

@app.get("/")
def root():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/admin", dependencies=[Depends(require_admin)])
def admin_page():
    return FileResponse(os.path.join(STATIC, "admin.html"))


@app.get("/embed")
def embed_page(token: str = "", key: str = "", company_id: str = ""):
    """The panel.

    `frame-ancestors` is the half of the origin check the browser enforces for
    us: the registered origin is the only page allowed to frame the panel at
    all. The postMessage target origin inside the page is the other half —
    that one stops the data going anywhere else once it is framed.

    A host page with no backend can pass `key` + `company_id` instead of a
    token. The panel then opens its own session from inside the iframe, which
    is same-origin here and so needs no CORS and no change to this endpoint.
    It does put the API key in page source — see the note in embed.html.
    """
    allowed = "*"
    try:
        application = db.get_application(security.verify(token)["app"])
        allowed = (application or {}).get("iframe_origin") or "*"
    except (security.TokenError, KeyError, TypeError):
        pass  # No usable token: serve the page, which will show its own error.
    return FileResponse(
        os.path.join(STATIC, "embed.html"),
        headers={"Content-Security-Policy": f"frame-ancestors {allowed}"},
    )


@app.get("/embed.js")
def embed_script():
    """The drop-in loader a host application includes."""
    return FileResponse(
        os.path.join(STATIC, "embed.js"),
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/host")
def host_page():
    return FileResponse(os.path.join(STATIC, "host.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(HTTPException)
def http_error(request, exc):
    # Headers matter here: the Basic challenge on the admin area travels on the
    # 401, and without it the browser never asks for the password.
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code,
                        headers=getattr(exc, "headers", None))
