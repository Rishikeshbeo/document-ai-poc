# Document AI — prototype

A document goes in, structured JSON comes out, already shaped the way the
calling application expects. No model of ours is trained. Processing runs in
the EU. Corrections made by one user help everyone at the same company.

Roughly 3,100 lines of our own, no build step, no framework on the frontend,
and nothing fetched from a CDN at runtime.

---

## Run it

```bash
pip install -r requirements.txt
python make_samples.py                  # five demo invoices
uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000>. It works with no API key — see *Providers* below.

The superadmin area is behind a password. The demo default is deliberately
trivial, printed to the log on start:

```
superadmin /admin — user1 / 1234   (set DOCAI_ADMIN_PASSWORD to choose your own)
```

`DOCAI_ADMIN_PASSWORD` overrides it and `DOCAI_ADMIN_USER` the name. The
default is a door that is shut, not one that is locked — set a real password
before the service is reachable by anyone but you.

Nothing is loaded from a CDN — no fonts, no scripts. The panel makes no
third-party request, so it works on a closed network and sends no end user's IP
address anywhere.

---

## The two ideas worth explaining

### The target structure is the extraction instruction

The superadmin registers an application and gives it a JSON Schema. That schema
is passed straight to the model with structured output enforced, so the field
names and descriptions *are* the prompt. A new customer means a new schema and
nothing else: no mapping code, no per-product logic, nothing to train.

### Learning lives in retrieval, not in weights

When a user corrects a field, we store how to find it again:

```
company_id · application · layout fingerprint · field path
             · page + rectangle   (when the document has coordinates)
             · text anchor        (the label the value sat behind)
             · short snippet
```

The next document with the same fingerprint is read at that rectangle, or after
that anchor, and the result goes to the model as strong evidence. Accuracy
climbs per company with nothing fine-tuned and no document retained.

Two ways of remembering, because one is not enough. A rectangle is precise but
needs a rendered page with coordinates — a text file, a text preview and some
scans have none, and marking used to be simply unavailable there. An anchor
needs only text, so any document we can read at all can be corrected. The
rectangle is tried first and the anchor is the fallback; a drawn correction
stores both.

The fingerprint is the supplier's VAT ID or IBAN when one is present, otherwise
a hash of the non-numeric words in the top third of page one — the fixed labels
of a template, which stay put while amounts and dates change.

---

## Providers

| | |
|---|---|
| `MISTRAL_API_KEY` set | Mistral (French company, EU processing) with `response_format: json_schema`, plus OCR for scans |
| not set | A local label-matching heuristic, text-based files only |

The offline provider is not the product. It exists so the prototype runs on any
laptop with no key and no network, and so the correction loop can be
demonstrated end to end offline. Both providers read the same stored hints, so
switching between them changes accuracy, not behaviour.

```bash
export MISTRAL_API_KEY=...
export MISTRAL_MODEL=mistral-large-latest
export MISTRAL_OCR_MODEL=mistral-ocr-latest   # scans and photos
```

Both are read at import time, so export them before starting uvicorn. Nothing
in the app loads a `.env` file.

### Scans and photos

A PDF with no text layer, or a `.png`/`.jpg`/`.webp`/`.tiff`, goes to Mistral's
OCR endpoint and comes back as the same positioned words the PDF path
produces, so fingerprinting and learned regions work on a scan exactly as they
do on a digital file. A scanned and a digital invoice from the same supplier
share a fingerprint, so a region marked on one is read on the other.

OCR returns paragraph-level boxes, never per-word ones, so the words are
reconstructed within each block (see `pages_from_ocr`). For a one-line block —
a header, a reference, a total, which is what people mark — that is close to
exact; inside a tall paragraph it is an estimate. Block boxes need OCR 4 or
newer; on an older model the text still extracts but `regions_available` comes
back false and the panel hides correction rather than saving a rectangle it
cannot honour.

There is no offline OCR. Without a key a scan returns 415 saying so.

### Rotated pages

A page carrying `/Rotate` — the norm in scanned batches — cannot be read
straight out of pdfplumber. On a quarter turn it reports the unrotated page box
while placing words in the rotated one, and on a half turn it returns the
characters of every word reversed: `Testwerk` comes back as `krewtseT`. Either
way a marked region lands somewhere other than where it was drawn.

So rotation is stripped with `pypdf` before extraction and reapplied to the
coordinates afterwards, which puts them in the same space the browser renders.
Browser and server then agree to within 0.3% on 0°, 90°, 180°, 270°, landscape,
Letter and multi-page files.

`pypdf` is optional. Without it, and on any file whose words fall outside the
page box whatever the cause, the text layer is refused and the document is
routed to OCR rather than read wrongly — a region that can never be matched
again is worse than no region.

---

## A sample application you can copy

`example_host/` is a separate application that embeds the panel, on its own
origin, with no dependencies:

```bash
python example_host/server.py          # http://localhost:8090
```

`index.html` is a plain page: the panel is an `<iframe>` in the markup with the
API key in its `src`, and a `message` listener fills a fixed form from what
comes back. No build step, no framework, and nothing on its own server — the
`server.py` beside it only hands the file over, so any static host would do.

```html
<iframe src="http://localhost:8077/embed?key=YOUR_API_KEY"></iframe>
```

```js
window.addEventListener('message', e => {
  if (e.origin !== 'http://localhost:8077') return;
  if (e.data.type === 'docai:result') fillForm(e.data.json);
});
```

The company comes from the application's registration, so it need not be in the
URL. A key in page source can be read by anyone who opens the page and used to
open a session for any company under that application — for a pilot, mint a
token on your server instead and pass `?token=…`; `server.py` still shows that
arrangement.

---

## Running it on another machine

Three things deliberately do not travel with the repository, and each one
produces a different symptom if it is missing.

**The Mistral key.** Not in the repo. Without it the service falls back to the
offline reader: digital PDFs still work but much less well, and a scan is
refused with a 415. Export `MISTRAL_API_KEY` before starting the service.

**The database.** `docai.sqlite3` is ignored, so a fresh machine starts with the
two seeded demo applications and nothing else. The API key written into
`example_host/index.html` belongs to an application that does not exist there,
and the panel will say *"That API key was not accepted."* Register an
application in `/admin`, copy the key it shows once, and paste it into the
iframe `src`.

**Learned corrections.** They live in that same database, so a new machine
starts knowing nothing. That is the intended behaviour, not a fault.

In full:

```bash
pip install -r requirements.txt
python make_samples.py

export MISTRAL_API_KEY=...                  # else offline, and no scans
uvicorn app.main:app --reload --port 8077   # terminal 1
python example_host/server.py               # terminal 2 — http://localhost:8090
```

Then open `/admin` (the password is printed on startup), register an
application, and put its key in `example_host/index.html`.

The form is built from the JSON the panel returns, not from a list of field
names written into the page. Give the same page an application registered for
delivery notes instead of invoices and it renders five different rows without
being touched — which is the point of the service, and would be quietly undone
by hardcoding an invoice form.

Prefer this over `/host` when showing the integration: `/host` is served by the
service itself, so it is same-origin and proves less. The sample runs on a
different origin, which is the arrangement a customer actually has.

### Locking it to one origin

Register an application whose `iframe_origin` is the host's origin, and hand
the sample that key:

```bash
curl -u user1:1234 -X POST http://localhost:8077/api/admin/applications \
  -H 'Content-Type: application/json' \
  -d '{"name":"Ledger — accounts payable","iframe_origin":"http://localhost:8090",
       "target_schema": { ... }}'

DOCAI_KEY=dk_live_… python example_host/server.py
```

The panel then serves `Content-Security-Policy: frame-ancestors
http://localhost:8090` and posts results to that origin only. Any other page
that tries to frame it is refused by the browser:

```
Framing 'http://localhost:8077/' violates the following Content Security
Policy directive: "frame-ancestors http://localhost:8090"
```

Left at the default `*`, anybody may embed the panel and receive its data.
That is fine for the demo and wrong for a customer. The origin is not on the
admin form — it is set when the application is registered, as above — and an
edit through the form preserves whatever it was.

---

## Integration — the whole thing

The host app already knows who is signed in, so the panel never asks anyone to
log in. One server-to-server call, one iframe.

```js
// on your server
const { token } = await fetch('https://docai.example/api/session', {
  method: 'POST',
  headers: { 'X-API-Key': DOCAI_KEY, 'Content-Type': 'application/json' },
  body: JSON.stringify({ company_id: user.companyId })
}).then(r => r.json());
```

```html
<!-- in your page -->
<div id="docai"></div>
<script src="https://docai.example/embed.js"></script>
<script>
  DocAI.mount('#docai', {
    token: TOKEN,
    onAccept: m => fillForm(m.json)   // user pressed "Use this data"
  });
</script>
```

`embed.js` builds the iframe, checks every message against the service origin
it was itself loaded from, and resizes the frame to fit the panel. It takes no
configuration beyond the token and has no dependencies.

| `mount(target, opts)` | |
|---|---|
| `token` | from `POST /api/session`. Or pass `embedUrl` instead |
| `onReady` | panel has a session; `{company_id, application}` |
| `onResult` | a document was read; `{json, run_id}` |
| `onCorrected` | user fixed a value; the full `{json, field_path, value}` with the correction already applied. Wire this if your form should update the moment a value is corrected rather than waiting for *Use this data* |
| `onAccept` | user pressed *Use this data*; `{json}` |
| `onSave` | a correction was stored; `{saved, regions, company_id}` |
| `onError` | `{message}` |
| `height`, `autoResize`, `className` | frame sizing, `autoResize` on by default |

Returns `{iframe, origin, on(event, fn), destroy()}`. The raw
`window.addEventListener('message', …)` contract still works unchanged if you
would rather not load the script — the wire events are `docai:ready`,
`docai:result`, `docai:corrected`, `docai:accepted`, `docai:save`, `docai:error`,
`docai:resize`.

A correction is applied to the target JSON as soon as the user confirms it, so
`docai:accepted` always carries the corrected values — numbers coerced to
numbers — whether or not the correction was also saved for the company.

The API key never reaches the browser. The token expires in 30 minutes and
carries the company ID, which is how learning is scoped.

### Origins

Register the host's origin on the application (`iframe_origin`, e.g.
`https://erp.example`, no path). Two things then hold:

- the panel serves `Content-Security-Policy: frame-ancestors <origin>`, so no
  other page can frame it;
- the panel posts results to that origin only, instead of `*`.

Left at the default `*`, anyone may embed the panel and receive its data. That
is fine for the demo and wrong for a pilot.

---

## Endpoints

| | |
|---|---|
| `POST /api/session` | API key → short-lived token and embed URL |
| `POST /api/extract` | file + mode → fields with confidence, coordinates, and the target JSON |
| `POST /api/feedback` | store corrected regions for the company |
| `DELETE /api/company-data` | erase everything held for one company |
| `/api/admin/*` | applications, learned regions, activity — Basic auth |

Full schema at `/api/docs`. Uploads are capped at 15 MB, rejected on
`Content-Length` before the body is read.

---

## Demo script

Use the **Vosswerk** pair. It is the only one that defeats a good model, so it
is the only one where the learning story is visible rather than asserted.

1. Open `/host` — a pretend ERP with the panel embedded, signed in as `acme-gmbh`.
2. Drop `samples/invoice_vosswerk_1.pdf`. Everything comes back — and
   `customer_reference` reads `AB-55710`, confidently, at 82%. **That is the
   wrong answer.** `AB-55710` is the *supplier's* own Auftragsnummer, the only
   labelled order number on the page. Ours is `A-77/2026`, one of three bare
   codes stacked in the top right with no labels at all. From the text alone the
   model's answer is the reasonable one; only the position tells them apart.
3. Switch to **Correct**, press *Mark* next to the reference, drag a box around
   `A-77/2026`. The value prefills from the document. Save it.
4. Drop `samples/invoice_vosswerk_2.pdf` — a later invoice from the same
   supplier, all values changed. The reference now reads `A-91/2026` — invoice
   two's own reference, not the one that was marked — flagged *learned* at 93%.
   Nothing was trained; the remembered rectangle was read.
5. In `/admin` → *What it has learned*, show the single stored row: a field name
   and four numbers. No document, no document text.
6. In the demo controls on `/host`, switch the company to `bravo-ag` and read the
   same invoice. It says `AB-56003` again — learning does not cross customers.

The Helios pair is the gentler version of the same thing: its reference is
unlabelled, which defeats the offline heuristic but not a real model. Use it
when demoing with no API key, where step 2 shows an empty field rather than a
wrong one.

`python test_loop.py` runs both paths headless and asserts each step, including
that the superadmin area stays shut without a password. Point it at a running
server with `DOCAI_BASE`, and give it `DOCAI_ADMIN_PASSWORD` to check the door
opens as well as closes.

---

## GDPR position

- Documents are processed in memory and never written to disk. There is no
  document store to breach and no retention policy to argue about.

  This is enforced rather than hoped for: the web framework spools an upload to
  a temporary file once it passes a threshold, which defaults to 1 MB — below
  our own 15 MB cap, so large documents *did* touch the disk. `MAX_UPLOAD` now
  raises that threshold above the cap, and a middleware turns away anything
  bigger by `Content-Length` before the body is read at all. Both halves are
  needed: without the second, the first would be an invitation to buffer
  without limit.
- Nothing is fetched from a third party at page load. The fonts and the PDF
  viewer are served from this app, so no end user's IP address reaches a US
  server — hotlinking Google Fonts, which the panel used to do, is itself a
  GDPR breach under LG München I 3 O 17493/20.
- What persists per correction: a field path, a page number, a rectangle, and a
  short text snippet the user typed. Visible and deletable in the admin area.
- `DELETE /api/company-data?company_id=…` erases everything for one company.
- Mistral is a French company processing in the EU; send calls with
  zero-retention enabled and put the DPA in place before any customer pilot.
- Tokens carry an opaque `user_ref` supplied by the host, never a name.
- The superadmin area requires a password, and has no default that is blank.

---

## What this prototype is not

Deliberately out of scope, and each is a known piece of work:

- **Scans without a key.** OCR needs the Mistral provider. The offline path
  will never do it, and word-level coordinates on a scan are reconstructed
  from paragraph blocks rather than measured.
- **Repeating structures.** Line items, tables, multi-page contracts. The schema
  supports one level of nesting and the UI shows flat fields.
- **Security hardening.** The superadmin area is behind HTTP Basic with one
  shared password — enough that it is not open, not enough to be a real admin
  account model: no users, no roles, no audit trail, no lockout. The HMAC secret
  still comes from an env var with a default, and there is no rate limiting.
  (`iframe_origin` *is* enforced, both as `frame-ancestors` and as the
  `postMessage` target.)
- **Operations.** SQLite, single process, no migrations, no monitoring.
- **The offline heuristic on foreign templates.** It now reads German compound
  labels, but on the Vosswerk invoice it still takes the supplier name as
  `Vosswerk Präzisionstechnik GmbH 4417-22`, because a code printed level with
  the company name lands on the same extracted line. The real provider gets it
  right, and chasing this in the fallback would be fitting to one sample.

None of these blocks the demo. All of them block production.

---

## Layout

```
app/
  main.py        API and routes, upload limits
  db.py          SQLite, five tables
  document.py    PDF → positioned words, fingerprinting, region reads
  providers.py   Mistral and the offline fallback
  security.py    the no-login token handshake, superadmin password
  static/        index · admin · embed · host
    embed.js     the drop-in loader host applications include
    vendor/      pdf.js and the two fonts, so nothing is fetched at runtime
make_samples.py  demo invoices — helios, vosswerk, nordlys
test_loop.py     end-to-end assertion of both demo paths
```
