"""Sample documents for the demo.

Three templates:

  helios    two invoices, same supplier, reference unlabelled in the top right.
            A heuristic misses it; a good model usually finds it anyway.

  vosswerk  two invoices, same supplier, built to defeat a good model rather
            than a weak one. The reference our ERP wants is one of three bare
            codes stacked in the top right with no labels at all, while the
            only *labelled* order number on the page belongs to the supplier.
            Reading the text alone, the wrong answer is the reasonable one —
            position is the only thing that disambiguates, which is precisely
            what a marked region supplies. This is the pair to demo on.

  nordlys   a different supplier entirely, everything neatly labelled.

Plus one photo: the vosswerk invoice as a JPEG carrying an EXIF orientation
tag, which is what a phone produces and the only sample with no text layer.
"""

import os

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

OUT = os.path.join(os.path.dirname(__file__), "samples")
W, H = A4


def helios(path, number, date, due, ref, net, tax, total):
    c = canvas.Canvas(path, pagesize=A4)
    c.setFont("Helvetica-Bold", 17)
    c.drawString(20 * mm, H - 25 * mm, "Helios Komponenten GmbH")
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, H - 31 * mm, "Industriestrasse 14 · 40212 Düsseldorf · Germany")
    c.drawString(20 * mm, H - 35 * mm, "VAT ID DE811907980 · Registergericht Düsseldorf HRB 44219")

    # The reference sits alone in the top right with no label. Heuristics miss it;
    # a marked region finds it every time.
    c.setFont("Helvetica", 10)
    c.drawRightString(W - 20 * mm, H - 25 * mm, ref)

    c.setFont("Helvetica-Bold", 13)
    c.drawString(20 * mm, H - 50 * mm, "INVOICE")
    c.setFont("Helvetica", 10)
    rows = [
        ("Invoice No: " + number, H - 58 * mm),
        ("Invoice Date: " + date, H - 63 * mm),
        ("Due Date: " + due, H - 68 * mm),
    ]
    for text, y in rows:
        c.drawString(20 * mm, y, text)

    c.drawString(20 * mm, H - 82 * mm, "Bill to:")
    c.drawString(20 * mm, H - 87 * mm, "ACME GmbH, Hafenweg 3, 48155 Münster")

    c.setFont("Helvetica-Bold", 9)
    c.drawString(20 * mm, H - 105 * mm, "Description")
    c.drawRightString(W - 20 * mm, H - 105 * mm, "Amount")
    c.line(20 * mm, H - 107 * mm, W - 20 * mm, H - 107 * mm)
    c.setFont("Helvetica", 9)
    for i, (desc, amt) in enumerate([
        ("Linear actuator LA-220, 40 units", f"{net * 0.62:,.2f}"),
        ("Controller board HX-9, 12 units", f"{net * 0.28:,.2f}"),
        ("Freight and handling", f"{net * 0.10:,.2f}"),
    ]):
        y = H - (113 + i * 6) * mm
        c.drawString(20 * mm, y, desc)
        c.drawRightString(W - 20 * mm, y, amt)

    c.setFont("Helvetica", 10)
    c.drawString(120 * mm, H - 145 * mm, "Net Amount:")
    c.drawRightString(W - 20 * mm, H - 145 * mm, f"{net:,.2f}")
    c.drawString(120 * mm, H - 151 * mm, "VAT 19%:")
    c.drawRightString(W - 20 * mm, H - 151 * mm, f"{tax:,.2f}")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(120 * mm, H - 158 * mm, "Total Amount:")
    c.drawRightString(W - 20 * mm, H - 158 * mm, f"{total:,.2f} EUR")

    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, 40 * mm, "Payment to IBAN DE89 3704 0044 0532 0130 00 · Currency EUR")
    c.drawString(20 * mm, 35 * mm, "Payable within 30 days without deduction.")
    c.save()


def vosswerk(path, number, date, due, own_order, delivery_note, ref, net, tax, total):
    """The hard case, and the realistic one.

    Three bare codes sit stacked in the top right with no labels: the
    supplier's internal job number, a cost centre, and our order reference.
    Meanwhile "Auftragsnummer" — the one label on the page that sounds like an
    order reference — carries the supplier's own number. A model reading the
    text has every reason to answer with that, and it is wrong. Only the
    position of the third bare code identifies it, so this is the field the
    correction loop exists for.
    """
    c = canvas.Canvas(path, pagesize=A4)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * mm, H - 24 * mm, "Vosswerk Präzisionstechnik GmbH")
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, H - 30 * mm, "Am Stahlwerk 7 · 44793 Bochum · Germany")
    c.drawString(20 * mm, H - 34 * mm, "USt-IdNr. DE327449102 · HRB 9917 Bochum")

    # Three unlabelled codes, stacked. The third one is ours.
    c.setFont("Helvetica", 9)
    c.drawRightString(W - 20 * mm, H - 22 * mm, delivery_note)
    c.drawRightString(W - 20 * mm, H - 27 * mm, "KST 4120")
    c.drawRightString(W - 20 * mm, H - 32 * mm, ref)

    c.setFont("Helvetica-Bold", 13)
    c.drawString(20 * mm, H - 48 * mm, "RECHNUNG / INVOICE")
    c.setFont("Helvetica", 10)
    for text, y in [
        ("Rechnungsnummer / Invoice No: " + number, H - 57 * mm),
        ("Rechnungsdatum / Invoice Date: " + date, H - 62 * mm),
        ("Zahlbar bis / Due Date: " + due, H - 67 * mm),
        # The decoy: labelled, order-shaped, and the supplier's own.
        ("Auftragsnummer: " + own_order, H - 72 * mm),
    ]:
        c.drawString(20 * mm, y, text)

    c.drawString(20 * mm, H - 86 * mm, "Rechnungsempfänger:")
    c.drawString(20 * mm, H - 91 * mm, "ACME GmbH, Hafenweg 3, 48155 Münster")

    c.setFont("Helvetica-Bold", 9)
    c.drawString(20 * mm, H - 108 * mm, "Position")
    c.drawRightString(W - 20 * mm, H - 108 * mm, "Betrag")
    c.line(20 * mm, H - 110 * mm, W - 20 * mm, H - 110 * mm)
    c.setFont("Helvetica", 9)
    for i, (desc, amt) in enumerate([
        ("Frästeil FR-118, 120 Stück", f"{net * 0.55:,.2f}"),
        ("Härten und Schleifen", f"{net * 0.31:,.2f}"),
        ("Verpackung und Versand", f"{net * 0.14:,.2f}"),
    ]):
        y = H - (116 + i * 6) * mm
        c.drawString(20 * mm, y, desc)
        c.drawRightString(W - 20 * mm, y, amt)

    c.setFont("Helvetica", 10)
    c.drawString(120 * mm, H - 148 * mm, "Nettobetrag:")
    c.drawRightString(W - 20 * mm, H - 148 * mm, f"{net:,.2f}")
    c.drawString(120 * mm, H - 154 * mm, "USt 19%:")
    c.drawRightString(W - 20 * mm, H - 154 * mm, f"{tax:,.2f}")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(120 * mm, H - 161 * mm, "Gesamtbetrag:")
    c.drawRightString(W - 20 * mm, H - 161 * mm, f"{total:,.2f} EUR")

    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, 40 * mm, "Zahlung an IBAN DE21 4306 0967 1234 5678 00 · Währung EUR")
    c.drawString(20 * mm, 35 * mm, "Zahlbar innerhalb 14 Tagen ohne Abzug.")
    c.save()


def meier(path, ref, header_extra=""):
    """A small supplier with no VAT ID and no IBAN, so the layout fingerprint
    falls back to the words in the top third of the page.

    The pair differs by one header line, which is enough to produce a different
    fingerprint — a branch name, a reprint stamp or a word OCR read differently
    does the same thing. It is the case that used to look like "the correction
    did not stick", and it is why a label anchor carries across layouts.
    """
    c = canvas.Canvas(path, pagesize=A4)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(20 * mm, H - 25 * mm, "Kleinbetrieb Meier")
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, H - 31 * mm, "Dorfstrasse 4, 91054 Erlangen")
    if header_extra:
        c.drawString(20 * mm, H - 36 * mm, header_extra)
    c.setFont("Helvetica", 10)
    c.drawString(20 * mm, H - 55 * mm, "Rechnung")
    c.drawString(20 * mm, H - 62 * mm, "Unsere Referenz: " + ref)
    c.drawString(20 * mm, H - 69 * mm, "Summe: 300.00 EUR")
    c.save()


def nordlys(path):
    c = canvas.Canvas(path, pagesize=A4)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * mm, H - 24 * mm, "Nordlys Logistikk AS")
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, H - 30 * mm, "Havnegata 8, 5003 Bergen, Norway")
    c.setFont("Helvetica", 10)
    for text, y in [
        ("Faktura / Invoice", H - 45 * mm),
        ("Invoice No: NL-2026-00871", H - 55 * mm),
        ("Invoice Date: 2026-08-02", H - 60 * mm),
        ("Due Date: 2026-09-01", H - 65 * mm),
        ("Customer Reference: PO-77214", H - 70 * mm),
        ("VAT ID NO974760673", H - 75 * mm),
    ]:
        c.drawString(20 * mm, y, text)
    c.drawString(20 * mm, H - 95 * mm, "Net Amount: 4,180.00")
    c.drawString(20 * mm, H - 101 * mm, "VAT 25%: 1,045.00")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, H - 108 * mm, "Total Amount: 5,225.00 EUR")
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, 40 * mm, "IBAN NO93 8601 1117 947")
    c.save()


def photo(pdf_path, out_path, orientation=6):
    """The same invoice as a phone photo: no text layer, and turned on its side.

    Written the way a camera writes it — the pixels stored as the sensor read
    them, with an EXIF tag saying which way up they go. Browsers apply that tag
    and OCR services ignore it, so this is the file that proves the service
    normalises orientation before reading rather than after: mark a field on
    the PDF and the rectangle has to be read out of this one too.

    Needs Pillow and pypdfium2, which arrive with pdfplumber. Skipped rather
    than fatal, so `make_samples.py` still runs on reportlab alone.
    """
    try:
        import pypdfium2
        from PIL import Image
    except ImportError:
        print("  (skipped the photo sample: needs Pillow and pypdfium2)")
        return
    page = pypdfium2.PdfDocument(pdf_path)[0]
    # 150 dpi — a legible phone snap, not an archival scan.
    image = page.render(scale=150 / 72).to_pil().convert("RGB")
    turns = {6: 90, 3: 180, 8: 270}.get(orientation, 0)
    exif = Image.Exif()
    exif[0x0112] = orientation
    image.rotate(turns, expand=True).save(out_path, quality=88, exif=exif)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    helios(f"{OUT}/invoice_helios_1.pdf", "HK-2026-04417", "2026-07-14", "2026-08-13",
           "PO-90233 / Kostenstelle 4120", 12480.00, 2371.20, 14851.20)
    helios(f"{OUT}/invoice_helios_2.pdf", "HK-2026-04902", "2026-08-21", "2026-09-20",
           "PO-91188 / Kostenstelle 4120", 8360.00, 1588.40, 9948.40)
    vosswerk(f"{OUT}/invoice_vosswerk_1.pdf", "VW-R-2026-1188", "2026-08-05", "2026-08-19",
             own_order="AB-55710", delivery_note="4417-22", ref="A-77/2026",
             net=9640.00, tax=1831.60, total=11471.60)
    vosswerk(f"{OUT}/invoice_vosswerk_2.pdf", "VW-R-2026-1245", "2026-09-02", "2026-09-16",
             own_order="AB-56003", delivery_note="4502-09", ref="A-91/2026",
             net=7180.00, tax=1364.20, total=8544.20)
    meier(f"{OUT}/invoice_meier_1.pdf", "MM-001")
    meier(f"{OUT}/invoice_meier_2.pdf", "MM-002", header_extra="Filiale Nord")
    nordlys(f"{OUT}/invoice_nordlys.pdf")
    photo(f"{OUT}/invoice_vosswerk_1.pdf", f"{OUT}/invoice_vosswerk_1_photo.jpg")
    print("wrote", sorted(os.listdir(OUT)))
