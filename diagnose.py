"""Why does this document not work?

    python diagnose.py path/to/your.pdf

Runs the same stages the service does and reports where it stops. Every
"marking does not work" report so far has been one of these five, and they have
nothing to do with each other — so the point of this is to say which.

Nothing is uploaded and nothing is stored; it reads the file and prints.
"""

import json
import os
import sys

from app import document, providers


def main(path):
    name = os.path.basename(path)
    print(f"\n{name}\n" + "=" * len(name))

    data = open(path, "rb").read()
    mime = document.sniff(data, name)
    print(f"\n1. FILE       {len(data) / 1024:.0f} KB · sniffed as {mime}")
    if len(data) > 15 * 1024 * 1024:
        print("   STOP: over the 15 MB cap, the service refuses it before reading.")
        return

    # A photo whose EXIF says it is turned is the one case where the file reads
    # perfectly and every coordinate is still wrong, so say so here rather than
    # leaving it to be discovered as "the box landed in the wrong place".
    if mime.startswith("image/") and document.upright(data, name) is not data:
        print("   Turned by its EXIF orientation tag. The service uprights it before")
        print("   reading, so the boxes match what the browser shows.")

    # --- stage 2: is there a text layer at all? -----------------------------
    try:
        pages = document.load(data, name)
    except document.NeedsOCR:
        print("\n2. TEXT       none — this is a scan or a photo.")
        if providers.ocr_available():
            print("   The service would send it to Mistral OCR and read it. Word positions")
            print("   come back reconstructed from paragraph blocks, so drawing a box is")
            print("   approximate inside a tall paragraph and near-exact on a single line.")
            print("   Not called from here: this tool uploads nothing. Upload it to the")
            print("   panel to see the words themselves.")
        else:
            print("   STOP: no MISTRAL_API_KEY, so the service returns 415 and there is")
            print("   no document to mark at all. This is the most common cause.")
        return
    except ValueError as e:
        print(f"\n2. TEXT       STOP: {e}")
        return

    words = sum(len(p["words"]) for p in pages)
    print(f"\n2. TEXT       {len(pages)} page(s), {words} positioned words")
    if not words:
        print("   STOP: no coordinates, so a drawn rectangle has nothing to read.")
        return

    # --- stage 3: the fingerprint, which is the lookup key ------------------
    fp = document.fingerprint(pages)
    kind = fp.split(":")[0]
    print(f"\n3. LAYOUT KEY {fp}")
    if kind in ("vat", "iban"):
        print(f"   Taken from the {kind.upper()} on the page — stable across documents")
        print("   from this sender, which is what you want.")
    else:
        print("   No VAT ID or IBAN found, so the key is a hash of the words in the top")
        print("   third of page one. Any change up there — a branch name, a reprint")
        print("   stamp, a word OCR reads differently — produces a different key, and a")
        print("   correction saved on one will only reach the other through a label")
        print("   anchor. Marking still works; it just may not carry.")

    # --- stage 4: does the document suit the app's target structure? --------
    schema = json.loads(os.environ.get("DOCAI_SCHEMA_JSON", "null")) if \
        os.environ.get("DOCAI_SCHEMA_JSON") else None
    if schema is None:
        from app import db
        app_row = db.get_application_by_key(os.environ.get("DOCAI_KEY", "dk_test_demo_key"))
        schema = app_row["target_schema"] if app_row else {}
    fields = [p for p, _ in providers._fields(schema)]
    print(f"\n4. TARGET     the application asks for {len(fields)} fields")
    print("   " + ", ".join(fields[:10]) + (" …" if len(fields) > 10 else ""))
    print("   If this document is not that kind of document, the fields come back")
    print("   empty and nothing is wrong with the reading — the application is simply")
    print("   pointed at the wrong target structure.")

    # --- stage 5: can a correction be anchored? -----------------------------
    print("\n5. ANCHORS    can a corrected value be remembered by its label?")
    labelled = 0
    samples = []
    for page in pages:
        for line in (page["text"] or "").splitlines():
            if ":" in line[:40] and line.split(":", 1)[1].strip():
                labelled += 1
                if len(samples) < 4:
                    samples.append(line.strip()[:64])
    print(f"   {labelled} line(s) look like \"Label: value\"")
    for s in samples:
        print(f"     · {s}")
    if labelled < 3:
        print("   FEW LABELS. This document probably puts values in table cells, where")
        print("   the heading is nowhere near the value in reading order. Drawing a box")
        print("   still works and is remembered by coordinates; typing a value may not")
        print("   be rememberable, and the panel will say so rather than store a dead")
        print("   row. This is the known weak spot.")
    else:
        print("   Enough labels for typed corrections to be remembered.")

    print("\n   Marking by rectangle: AVAILABLE (there are word coordinates)")
    print("   Marking by typing   : " +
          ("AVAILABLE" if labelled >= 3 else "LIMITED (see above)"))
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for arg in sys.argv[1:]:
        main(arg)
