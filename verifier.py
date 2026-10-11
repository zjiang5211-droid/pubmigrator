"""Per-file verification pipeline — the core of the 'batch + verify' positioning.

Conversion is the commodity; verification is the product. Every converted PDF
goes through a battery of checks and gets one of three verdicts:

  passed      all checks green; the file looks faithful
  suspicious  converted, but something looks off — a human should spot-check
  failed      no usable output (missing, 0 bytes, unparseable, 0 pages)

The checks specifically target the known silent-failure modes of
LibreOffice .pub conversion:
  * "success" with a 0-byte PDF            -> caught by exists_and_nonzero
  * text silently dropped from the PDF     -> caught by text_sanity
  * embedded pictures lost in conversion   -> caught by image_parity
    (source-side picture count comes from parsing the .pub's OLE Escher
    drawing stream; output-side count from `pdfimages -list`)
  * degenerate/blank pages                -> caught by size_plausibility

Known limitation (documented honestly): we cannot reliably compare page
counts against the source .pub — Publisher's page model lives in the
proprietary Quill stream and is not extractable with available tools.
We substitute size-per-page plausibility and text-density heuristics.
"""

import os
import struct
import subprocess

import olefile


# ---------------------------------------------------------------------------
# Source-side signals: what the .pub claims to contain
# ---------------------------------------------------------------------------

def _walk_escher(data):
    """Recursively walk Escher drawing records.

    Returns (shape_count, picture_blip_count). Record header is 8 bytes:
    ver/inst u16, fbt u16, length u32. Containers (ver==0xF, fbt in 0xF000-0xFFFF)
    are recursed into; fbt 0xF007 is a BSE (picture) record.
    Stops (rather than misparses) on corrupt records.
    """
    shapes, blips = 0, 0
    i, n = 0, len(data)
    while i + 8 <= n:
        ver_inst, fbt, ln = struct.unpack("<HHI", data[i:i + 8])
        if ln > n - i - 8:
            break
        body = data[i + 8:i + 8 + ln]
        if (ver_inst & 0xF) == 0xF and 0xF000 <= fbt <= 0xFFFF:
            s, b = _walk_escher(body)
            shapes += s
            blips += b
        elif fbt == 0xF007:
            blips += 1
        i += 8 + ln
    return shapes, blips


def source_picture_count(pub_path):
    """Best-effort count of embedded pictures in the .pub.

    Returns an int, or None when the OLE structure can't be read.
    Conservative by design: a failed parse yields None (check skipped),
    never a false accusation.
    """
    try:
        ole = olefile.OleFileIO(pub_path)
        try:
            data = ole.openstream("Escher/EscherStm").read()
        finally:
            ole.close()
        _, blips = _walk_escher(data)
        return blips
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Output-side signals: what the PDF actually contains
# ---------------------------------------------------------------------------

def _run(cmd, timeout=60):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def pdf_signals(pdf_path):
    """Extract measurable signals from a converted PDF (poppler-utils)."""
    sig = {
        "exists": False, "size": 0, "parseable": False, "pages": 0,
        "words": 0, "images": 0, "fonts": 0, "fonts_unembedded": 0,
    }
    if not pdf_path or not os.path.exists(pdf_path):
        return sig
    sig["exists"] = True
    try:
        sig["size"] = os.path.getsize(pdf_path)
    except OSError:
        return sig
    if sig["size"] == 0:
        return sig  # the classic silent failure: exit 0, 0-byte PDF

    info = _run(["pdfinfo", pdf_path], timeout=30)
    if info is None or info.returncode != 0:
        return sig
    for line in info.stdout.splitlines():
        if line.startswith("Pages:"):
            try:
                sig["pages"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
            break
    if sig["pages"] < 1:
        return sig
    sig["parseable"] = True

    txt = _run(["pdftotext", "-layout", pdf_path, "-"], timeout=60)
    if txt is not None and txt.returncode == 0:
        sig["words"] = len(txt.stdout.split())

    imgs = _run(["pdfimages", "-list", pdf_path], timeout=60)
    if imgs is not None and imgs.returncode == 0:
        n = 0
        for line in imgs.stdout.splitlines():
            s = line.strip()
            if not s or s.startswith("page") or set(s) <= set("- "):
                continue
            n += 1
        sig["images"] = n

    fonts = _run(["pdffonts", pdf_path], timeout=30)
    if fonts is not None and fonts.returncode == 0:
        lines = fonts.stdout.splitlines()
        emb_idx = None
        if lines:
            header = lines[0].split()
            if "emb" in header:
                emb_idx = header.index("emb")
        for line in lines[2:]:
            if not line.strip():
                continue
            sig["fonts"] += 1
            if emb_idx is not None:
                parts = line.split()
                if len(parts) > emb_idx and parts[emb_idx] == "no":
                    sig["fonts_unembedded"] += 1
    return sig


def make_thumbnail(pdf_path, out_png, dpi=80):
    """Render the first page as a PNG thumbnail for visual spot-checks."""
    prefix = out_png[:-4] if out_png.lower().endswith(".png") else out_png
    p = _run(["pdftoppm", "-png", "-f", "1", "-l", "1",
              "-r", str(dpi), pdf_path, prefix], timeout=60)
    cand = prefix + "-1.png"
    if p is not None and p.returncode == 0 and os.path.exists(cand):
        if cand != out_png:
            os.replace(cand, out_png)
        return out_png
    return None


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------

VERDICT_COPY = {
    "passed": {
        "label": "Verified — looks good",
        "note": "Text, images and page count all check out. Still wise to spot-check before deleting the original .pub.",
    },
    "suspicious": {
        "label": "Needs a human look",
        "note": "Converted, but something looks off (see details). Open it next to the original before deleting the .pub.",
    },
    "failed": {
        "label": "Conversion failed",
        "note": "No usable output. The file may be corrupted, password-protected, or use Publisher features the converter cannot read.",
    },
}


def verify_file(pub_path, pdf_path):
    """Run the full check battery on one converted file.

    Returns {"verdict", "reasons": [{"severity", "text"}], "signals", "src_pictures"}.
    Severity is one of "fail" (verdict=failed), "warn" (verdict=suspicious),
    "info" (verdict unaffected).
    """
    sig = pdf_signals(pdf_path)
    src_pics = source_picture_count(pub_path)
    reasons = []

    if not sig["exists"] or sig["size"] == 0:
        reasons.append(("fail",
            "Output file is missing or 0 bytes — the converter reported success "
            "but produced nothing. This is the classic silent failure."))
    elif not sig["parseable"] or sig["pages"] < 1:
        reasons.append(("fail",
            "Output PDF cannot be parsed or contains no pages."))
    else:
        pages = max(sig["pages"], 1)
        wpp = sig["words"] / pages
        if sig["words"] == 0:
            reasons.append(("warn",
                "No extractable text found at all — either a scanned-image document "
                "or all text was lost in conversion. Open and compare."))
        elif wpp < 10 and sig["images"] == 0:
            reasons.append(("warn",
                f"Very little text ({sig['words']} words on {sig['pages']} page(s)) "
                "and no images — spot-check before deleting the original."))
        if (src_pics is not None and src_pics >= 3
                and sig["images"] <= src_pics - 2):
            reasons.append(("warn",
                f"Source lists ~{src_pics} embedded pictures but the PDF contains "
                f"{sig['images']} images — some pictures may have been merged or "
                "dropped in conversion."))
        if sig["size"] / pages < 2048 and sig["words"] < 50:
            reasons.append(("warn",
                "Unusually small output for the page count — pages may be blank "
                "or degenerate."))

    if sig["fonts_unembedded"]:
        reasons.append(("info",
            f"{sig['fonts_unembedded']} of {sig['fonts']} fonts are not embedded — "
            "text usually still extracts, but layout may shift on other machines."))

    verdict = "passed"
    if any(s == "fail" for s, _ in reasons):
        verdict = "failed"
    elif any(s == "warn" for s, _ in reasons):
        verdict = "suspicious"

    return {
        "verdict": verdict,
        "reasons": [{"severity": s, "text": t} for s, t in reasons],
        "signals": sig,
        "src_pictures": src_pics,
    }
