# Document AI — prototype

A document goes in, structured JSON comes out, already shaped the way the
calling application expects. No model of ours is trained. Processing runs in
the EU. Corrections made by one user help everyone at the same company.

Roughly 3,100 lines of our own, no build step, no framework on the frontend,
and nothing fetched from a CDN at runtime.

---

## Run it

### With Docker

```bash
echo "MISTRAL_API_KEY=your-key" > .env
docker compose up --build
```

- the sample application — <http://localhost:8090>
- superadmin — <http://localhost:8077/admin>

Two containers from one image: the service and the sample host application.
`compose` reads `.env` beside it, so no key is typed on the command line and
none is baked into the image. The database lives on a named volume, so
registered clients and everything they have taught it survive a rebuild.

It runs with no key at all — see *Providers* — but scans need one.

### Without Docker

```bash
pip install -r requirements.txt
python make_samples.py                     # seven demo invoices
export MISTRAL_API_KEY=...                 # optional; scans need it
uvicorn app.main:app --reload --port 8077  # terminal 1
python example_host/server.py              # terminal 2
```

The superadmin area is behind a password. The demo default is deliberately
trivial, printed to the log on start:

```
superadmin /admin — user1 / 1234   (set DOCAI_ADMIN_PASSWORD to choose your own)
```

`DOCAI_ADMIN_PASSWORD` overrides it and `DOCAI_ADMIN_USER` the name. The
default is a door that is shut, not one that is locked — set a real password
before the service is reachable by anyone but you.

### Registering a client

Three fields: a **name**, the **company** the client belongs to, and the
**target JSON structure** its application receives. Saving mints an API key,
shown once — the panel then displays the one-line iframe to hand over, and the
token handshake for when the client has a server.

Because the company is on the registration, an embed carries only the key; every
correction its users make is filed under that company and shared across it.

Lost a key? **Reissue the API key** replaces it — the old one stops working at
once. **Delete** removes the application, its key and everything it learned.

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

The next document of that kind is read at that rectangle, or beside that label,
and **the value found there replaces the model's answer for that field**. This
is the part that makes a correction a correction: handing it to the model as
evidence is not enough, because the model has its own confident reading and
often keeps it — so the field would revert on the next upload and the user
would mark it again.

Three ways of finding it, tried in order of precision:

| | needs | used when |
|---|---|---|
| the rectangle | a page with coordinates | the same layout |
| a spatial anchor — the label beside or above the value | only text | tables and forms, where reading order separates label from value |
| the same-line anchor | only text | `Label: value` documents |

A rectangle is exact but needs coordinates, which a text file and some scans do
not have. An anchor needs only text, so anything readable at all can be
corrected. A drawn correction stores both.

### Recognising the document again

The fingerprint is the sender's VAT ID or IBAN when there is one, otherwise a
hash of the non-numeric words in the top third of page one — a template's fixed
labels, which stay put while amounts and dates change.

That hash is exact, and on a scan it moves: OCR reading one word differently
between uploads is enough to change it, and the correction would silently stop
applying. So the template's words are stored alongside the correction, and when
the hash misses, the closest layout this company has taught us wins if it shares
**62%** of its words. Below that it is a different template and nothing is
applied.

A label-shaped anchor — one ending in `:` or short enough to be a caption —
also carries to the company's *other* layouts, so a correction survives a
supplier redesigning their header. A long anchor that merely happened to precede
the value does not travel, or it would match something coincidental elsewhere.

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

All three are read at import time, so set them before the service starts.
The service itself loads no `.env` file — that is deliberate, so a key cannot be
committed by accident. Under Docker, `compose` reads `.env` and passes them in
as environment variables, which amounts to the same thing.

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
two seeded demo applications and nothing else. The key in
`example_host/index.html` belongs to *this* machine's database, so on another
one the panel refuses it: *"That API key does not match a registered
application."* Register an application in `/admin`, copy the key it shows once,
and replace the one in the iframe `src`.

`dk_test_demo_key` is seeded on every fresh database if you want the sample to
work before registering anything.

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

Prefer this over `/host` when showing the integration: `/host` is served by the
service itself, so it is same-origin and proves less. The sample runs on a
different origin, which is the arrangement a customer actually has.

### Locking it to one origin

Register an application whose `iframe_origin` is the host's origin, and hand
the sample that key:

```bash
curl -u user1:1234 -X POST http://localhost:8077/api/admin/applications \
  -H 'Content-Type: application/json' \
  -d '{"name":"Acme — purchase invoices","company_id":"acme-gmbh",
       "iframe_origin":"http://localhost:8090","target_schema": { ... }}'
```

Then put the key it returns into the iframe `src` in `example_host/index.html`.

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
| `/api/admin/applications` | list · register · update · `DELETE` one, with everything it learned |
| `/api/admin/applications/{id}/key` | `POST` reissues the key, shown once |
| `/api/admin/hints`, `/api/admin/runs` | stored corrections and run counters |

Everything under `/api/admin/*` is behind Basic auth.

Full schema at `/api/docs`. Uploads are capped at 15 MB, rejected on
`Content-Length` before the body is read.

---

## Demo script

Use the **Vosswerk** pair. It is the only one that defeats a good model, so it
is the only one where the learning story is visible rather than asserted.

1. Open <http://localhost:8090> — the sample application with the panel embedded.
2. Drop `samples/invoice_vosswerk_1.pdf`. Everything comes back — and
   `customer_reference` reads `AB-55710`, confidently, at 82%. **That is the
   wrong answer.** `AB-55710` is the *supplier's* own Auftragsnummer, the only
   labelled order number on the page. Ours is `A-77/2026`, one of three bare
   codes stacked in the top right with no labels at all. From the text alone the
   model's answer is the reasonable one; only the position tells them apart.
3. Switch to **Correct** and press **Fix** on that field. Type the right value,
   or drag a box around `A-77/2026` on the page and it prefills from the
   document. Press **Set** — that stores it; there is no second step.
4. Drop `samples/invoice_vosswerk_2.pdf` — a later invoice from the same
   supplier, all values changed. The reference now reads `A-91/2026` — invoice
   two's own reference, not the one that was marked — flagged *learned* at 93%.
   Nothing was trained; the remembered rectangle was read.
5. Upload `invoice_vosswerk_1.pdf` again — the corrected value holds, because
   the box is re-read rather than the old answer replayed.
6. Register a second application under a different company, point the page at
   its key, and read the same invoice. It says `AB-55710` again — learning does
   not cross customers.

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
- What persists per correction: a field path, a page number, a rectangle, the
  label that locates the value, the template's header words, and a short
  snippet. No document, and no document text.
- There is no longer a screen listing what has been learned — it was removed as
  clutter. The rows are still readable and deletable through
  `GET`/`DELETE /api/admin/hints`, but *"show me everything you hold about this
  company"* currently has no answer in the interface, and a data protection
  officer will ask for one.
- `DELETE /api/company-data?company_id=…` erases everything for one company.
- Mistral is a French company processing in the EU; send calls with
  zero-retention enabled and put the DPA in place before any customer pilot.
- `api.mistral.ai` sits behind Cloudflare, a US company, which terminates TLS at
  whichever edge location is nearest the caller — from India that is Delhi, from
  an EU server an EU city. So document content is decrypted outside our control
  for one hop. Nothing in this repository calls Cloudflare; it is Mistral's
  infrastructure. Ask them whether their edge is pinned to the EU, and name it in
  the DPA: *"our model runs in France"* does not answer where TLS terminates.
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
- **Operations.** SQLite, single process, one ad-hoc migration, no monitoring.
- **Undoing a correction.** Pressing Set stores it for the whole company
  immediately, and there is no button anywhere to take it back — only re-marking
  the field or a `DELETE` against the API.
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
  main.py            API and routes, upload limits, layout matching
  db.py              SQLite, five tables, one migration
  document.py        PDF → positioned words, rotation, fingerprint, anchors
  providers.py       Mistral (extraction + OCR) and the offline fallback
  security.py        the no-login token handshake, superadmin password
  static/            index · admin · embed · host
    embed.js         the drop-in loader, for hosts that want it
    vendor/          pdf.js and two fonts, so nothing is fetched at runtime
example_host/
  index.html         a plain page: iframe in the markup, form filled by message
  server.py          hands that file over; also shows the token arrangement
Dockerfile           one image, two roles
docker-compose.yml   the service on 8077, the sample app on 8090
make_samples.py      demo invoices — helios, vosswerk, meier, nordlys
test_loop.py         eight end-to-end assertions, both providers
diagnose.py          why a given document does not work
```
