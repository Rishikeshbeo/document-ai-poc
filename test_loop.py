"""End-to-end check of the demo path.

1. Read invoice 1 for the first company    -> customer_reference should be missed
2. Mark the region, save the correction
3. Read invoice 2 (same supplier, new values) -> reference should now be right
4. Read invoice 2 as a second company      -> reference missed again (no leakage)
5. A different supplier                    -> no fingerprint collision
6. The Vosswerk pair                       -> the same loop on the hard template,
                                              where the wrong answer is the
                                              plausible one
7. The superadmin area                     -> closed without the password

Set DOCAI_ADMIN_PASSWORD to the running server's password (or leave it unset
and paste the one it printed) so step 7 can check the door opens too.
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("DOCAI_BASE", "http://localhost:8077")
KEY = "dk_test_demo_key"
ADMIN_USER = os.environ.get("DOCAI_ADMIN_USER", "user1")
ADMIN_PASSWORD = os.environ.get("DOCAI_ADMIN_PASSWORD", "1234")

# The two companies the whole run is about: one that teaches the system and one
# that must never see what the first taught it. Named once, so every line that
# reports a result names the company it actually used.
COMPANY = os.environ.get("DOCAI_COMPANY", "acme-gmbh")
OTHER_COMPANY = os.environ.get("DOCAI_OTHER_COMPANY", "bravo-ag")


def req(path, method="GET", body=None, headers=None, files=None):
    hdrs = headers or {}
    data = None
    if files:
        boundary = "----docai"
        parts = []
        for k, v in (body or {}).items():
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
        for k, (fn, content) in files.items():
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fn}\"\r\n"
                f"Content-Type: application/pdf\r\n\r\n".encode() + content + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        data = b"".join(parts)
        hdrs["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(r) as resp:
        return json.loads(resp.read())


def session(company):
    return req("/api/session", "POST", {"company_id": company}, {"X-API-Key": KEY})["token"]


def read(token, path):
    with open(path, "rb") as f:
        content = f.read()
    return req("/api/extract", "POST", {"mode": "normal"},
               {"Authorization": "Bearer " + token},
               {"file": (path.split("/")[-1], content)})


def show(title, res):
    print(f"\n--- {title} ---")
    print(f"layout {res['fingerprint']}  hints applied: {res['hints_applied']}  via {res['provider']}")
    for f in res["fields"]:
        flag = " <-- LEARNED" if f["learned"] else ""
        print(f"  {f['path']:<22} {str(f['value'])[:44]:<46} {f['confidence']:.2f}{flag}")


ok = True
# Start from a clean slate so the run is repeatable.
for co in (COMPANY, OTHER_COMPANY):
    req(f"/api/company-data?company_id={co}", "DELETE", None, {"X-API-Key": KEY})
acme = session(COMPANY)

r1 = read(acme, "samples/invoice_helios_1.pdf")
show(f"1. {COMPANY} reads Helios invoice #1", r1)
ref1 = next(f for f in r1["fields"] if f["path"] == "customer_reference")
if ref1["value"] and "90233" in str(ref1["value"]):
    print("  note: reference already found without a hint")

# The region the user would draw around the unlabelled reference, top right.
box = [0.62, 0.068, 0.97, 0.094]
req("/api/feedback", "POST",
    {"fingerprint": r1["fingerprint"],
     "corrections": [{"field_path": "customer_reference", "page": 1,
                      "bbox": box, "value": "PO-90233 / Kostenstelle 4120"}]},
    {"Authorization": "Bearer " + acme})
print(f"\n2. correction saved for company {COMPANY}")

r2 = read(acme, "samples/invoice_helios_2.pdf")
show(f"3. {COMPANY} reads Helios invoice #2 (new values, same template)", r2)
ref2 = next(f for f in r2["fields"] if f["path"] == "customer_reference")
if "91188" in str(ref2["value"] or ""):
    print("  PASS: the new invoice's own reference was read from the learned region")
else:
    print(f"  FAIL: expected PO-91188, got {ref2['value']!r}")
    ok = False
if r2["fingerprint"] != r1["fingerprint"]:
    print("  FAIL: same supplier produced a different layout fingerprint")
    ok = False

bravo = session(OTHER_COMPANY)
r3 = read(bravo, "samples/invoice_helios_2.pdf")
show(f"4. {OTHER_COMPANY} reads the same invoice", r3)
if r3["hints_applied"] == 0:
    print("  PASS: no corrections leaked to another company")
else:
    print("  FAIL: another company inherited the correction")
    ok = False

r4 = read(acme, "samples/invoice_nordlys.pdf")
show("5. a different supplier entirely", r4)
if r4["fingerprint"] != r1["fingerprint"] and r4["hints_applied"] == 0:
    print("  PASS: different supplier, different layout, no hints misapplied")
else:
    print("  FAIL: fingerprint collision across suppliers")
    ok = False

# --- the hard template ------------------------------------------------------
# Vosswerk hides our reference among three unlabelled codes and puts a labelled
# order number belonging to the supplier right in the body. A model reading the
# text answers with the supplier's number, which is wrong and not a blank —
# the case the correction loop actually has to earn its keep on.

v1 = read(acme, "samples/invoice_vosswerk_1.pdf")
show(f"6. {COMPANY} reads Vosswerk invoice #1 (the hard one)", v1)
vref1 = next(f for f in v1["fields"] if f["path"] == "customer_reference")
if vref1["value"] == "A-77/2026":
    print("  note: the provider found it unaided — no correction needed on this run")
else:
    print(f"  as expected: got {vref1['value']!r}, not 'A-77/2026'")

# The box a user would drag around the third bare code, top right.
vbox = [0.8290, 0.0933, 0.9108, 0.1160]
req("/api/feedback", "POST",
    {"fingerprint": v1["fingerprint"],
     "corrections": [{"field_path": "customer_reference", "page": 1,
                      "bbox": vbox, "value": "A-77/2026"}]},
    {"Authorization": "Bearer " + acme})
print("\n7. correction saved on the Vosswerk layout")

v2 = read(acme, "samples/invoice_vosswerk_2.pdf")
show(f"8. {COMPANY} reads Vosswerk invoice #2 (new values, same template)", v2)
vref2 = next(f for f in v2["fields"] if f["path"] == "customer_reference")
if vref2["value"] == "A-91/2026":
    print("  PASS: invoice #2's own reference, read from the learned region")
else:
    print(f"  FAIL: expected 'A-91/2026', got {vref2['value']!r}")
    ok = False
if v2["fingerprint"] != v1["fingerprint"]:
    print("  FAIL: same Vosswerk template produced a different fingerprint")
    ok = False
if v2["fingerprint"] == r1["fingerprint"]:
    print("  FAIL: Vosswerk collided with Helios")
    ok = False

# --- a correction that has to survive a changed layout ----------------------
# The Meier pair has no VAT ID, so its fingerprint comes from the words at the
# top of the page — and the second one has an extra header line, so the two do
# not match. A rectangle cannot help there. A label anchor can.

m1 = read(acme, "samples/invoice_meier_1.pdf")
show(f"10. {COMPANY} reads Meier invoice #1", m1)
req("/api/feedback", "POST",
    {"fingerprint": m1["fingerprint"],
     "corrections": [{"field_path": "customer_reference",
                      "anchor": "Unsere Referenz:", "value": "MM-001"}]},
    {"Authorization": "Bearer " + acme})
print("\n11. correction saved as a label anchor, no rectangle")

m2 = read(acme, "samples/invoice_meier_2.pdf")
show(f"12. {COMPANY} reads Meier invoice #2 (different fingerprint)", m2)
mref = next(f for f in m2["fields"] if f["path"] == "customer_reference")
if m2["fingerprint"] == m1["fingerprint"]:
    print("  note: the two Meier layouts fingerprinted the same after all")
elif mref["value"] == "MM-002":
    print("  PASS: the correction carried to a layout it was never saved on")
else:
    print(f"  FAIL: expected 'MM-002' on the changed layout, got {mref['value']!r}")
    ok = False

bravo2 = session(OTHER_COMPANY)
m3 = read(bravo2, "samples/invoice_meier_2.pdf")
if m3["hints_applied"] == 0:
    print("  PASS: carrying across layouts does not carry across companies")
else:
    print("  FAIL: another company inherited the anchor")
    ok = False

# --- the superadmin door ----------------------------------------------------

def status(path, headers=None):
    r = urllib.request.Request(BASE + path, headers=headers or {})
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


print("\n--- 9. the superadmin area ---")
if status("/api/admin/applications") == 401 and status("/admin") == 401:
    print("  PASS: closed without credentials")
else:
    print("  FAIL: the superadmin area answered without a password")
    ok = False

if ADMIN_PASSWORD:
    import base64
    cred = base64.b64encode(f"{ADMIN_USER}:{ADMIN_PASSWORD}".encode()).decode()
    auth = {"Authorization": "Basic " + cred}
    bad = base64.b64encode(f"{ADMIN_USER}:definitely-not-it".encode()).decode()
    if status("/api/admin/applications", auth) == 200 and \
       status("/api/admin/applications", {"Authorization": "Basic " + bad}) == 401:
        print("  PASS: opens with the right password, refuses the wrong one")
    else:
        print("  FAIL: the password check does not behave")
        ok = False
else:
    print("  skipped the positive check — set DOCAI_ADMIN_PASSWORD to include it")

print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
sys.exit(0 if ok else 1)
