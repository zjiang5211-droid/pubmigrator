"""Publisher (.pub) conversion engine.

Uses LibreOffice headless (libmspub import filter) to convert .pub files
to archival / preview / editable formats, then verifies output quality
with poppler-utils so we can show honest quality badges instead of
silently serving 0-byte or garbage files.

Verified working output formats (LibreOffice 24.2, Draw document):
  pdf  - archive copy            (draw_pdf_Export)
  png  - per-document preview    (draw_png_Export, first page)
  svg  - editable vector         (draw_svg_Export)
  odg  - editable in LibreOffice Draw (draw8)

NOT supported: docx/pptx export directly from a Draw document.
LibreOffice has no Draw->DOCX export filter ("no export filter" error),
so we do not pretend to offer it. SVG/ODG cover the "keep editing" need.
"""

import os
import shutil
import subprocess
import tempfile
import threading
import uuid

LO_BIN = os.environ.get("LIBREOFFICE_BIN", "libreoffice")
LO_TIMEOUT = int(os.environ.get("LO_TIMEOUT", "120"))

# Serialize LibreOffice invocations: parallel headless instances sharing
# a profile dir are flaky, so the MVP converts one file at a time.
_lo_lock = threading.Lock()

FORMATS = {
    "pdf": {"ext": "pdf", "label": "PDF (archive)", "mime": "application/pdf"},
    "png": {"ext": "png", "label": "PNG (preview)", "mime": "image/png"},
    "svg": {"ext": "svg", "label": "SVG (editable vector)", "mime": "image/svg+xml"},
    "odg": {"ext": "odg", "label": "ODG (edit in LibreOffice Draw)", "mime": "application/vnd.oasis.opendocument.graphics"},
}


def convert_pub(input_path, output_dir, fmt="pdf"):
    """Convert one .pub file. Returns (output_path or None, error string or None)."""
    if fmt not in FORMATS:
        return None, f"unsupported format: {fmt}"
    ext = FORMATS[fmt]["ext"]
    os.makedirs(output_dir, exist_ok=True)

    # Unique LibreOffice profile per invocation avoids profile-lock races.
    profile_dir = tempfile.mkdtemp(prefix="lo-profile-")
    env = dict(os.environ, )
    # LibreOffice reads UserInstallation from -env:UserInstallation; pass via env var trick below.
    try:
        with _lo_lock:
            cmd = [
                LO_BIN, "--headless",
                f"-env:UserInstallation=file://{profile_dir}",
                "--convert-to", ext,
                "--outdir", output_dir,
                input_path,
            ]
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True,
                    timeout=LO_TIMEOUT, env=env,
                )
            except subprocess.TimeoutExpired:
                return None, "conversion timed out"

            base = os.path.splitext(os.path.basename(input_path))[0]
            # Sanitize: LO keeps the input basename for the output.
            expected = os.path.join(output_dir, base + "." + ext)
            if os.path.exists(expected) and os.path.getsize(expected) > 0:
                return expected, None
            # Fallback: pick up whatever LO actually produced for this input.
            for name in os.listdir(output_dir):
                if name.startswith(base) and name.lower().endswith("." + ext):
                    cand = os.path.join(output_dir, name)
                    if os.path.getsize(cand) > 0:
                        return cand, None
            err = (proc.stderr or proc.stdout or "")[-500:]
            return None, f"no usable output produced. {err}".strip()
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)


def pdf_quality(pdf_path):
    """Inspect a converted PDF. Returns dict with status badge info.

    Status levels (mirrors the honest-badge concept):
      ok      - PDF has pages and extractable text      -> "Text intact"
      review  - PDF exists but little/no text           -> "Needs visual check"
      failed  - no file / 0 bytes / unreadable          -> "Conversion failed"
    """
    result = {"status": "failed", "pages": 0, "words": 0, "size": 0}
    try:
        if not pdf_path or not os.path.exists(pdf_path):
            return result
        size = os.path.getsize(pdf_path)
        result["size"] = size
        if size == 0:
            return result

        info = subprocess.run(
            ["pdfinfo", pdf_path], capture_output=True, text=True, timeout=30
        )
        for line in info.stdout.splitlines():
            if line.startswith("Pages:"):
                result["pages"] = int(line.split(":", 1)[1].strip())
                break
        if result["pages"] == 0:
            return result

        txt = subprocess.run(
            ["pdftotext", "-layout", pdf_path, "-"],
            capture_output=True, text=True, timeout=60,
        )
        words = len(txt.stdout.split())
        result["words"] = words
        # 5+ words = real text content survived. Below that, treat as
        # suspicious (scanned image, broken layout) and ask for a visual check.
        result["status"] = "ok" if words >= 5 else "review"
        return result
    except Exception:
        return result


BADGE_COPY = {
    "ok": {
        "label": "Text intact",
        "note": "Text extracted cleanly. Complex tables or multi-column layouts may still shift — spot-check before deleting originals.",
    },
    "review": {
        "label": "Needs visual check",
        "note": "Little extractable text found. This can mean scanned images, complex layouts, or partial conversion — open the file and compare before deleting the original .pub.",
    },
    "failed": {
        "label": "Conversion failed",
        "note": "No usable output. The file may be corrupted, password-protected, or use Publisher features the converter cannot read.",
    },
}


def new_workdir():
    """Create an isolated working directory for one request. Caller cleans up."""
    d = os.path.join(tempfile.gettempdir(), "pubmigr-" + uuid.uuid4().hex[:12])
    os.makedirs(os.path.join(d, "in"), exist_ok=True)
    os.makedirs(os.path.join(d, "out"), exist_ok=True)
    return d


# OLE2 compound-document magic every genuine .pub file starts with.
OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")


def looks_like_pub(path):
    """True if the file has the OLE2 signature of a real Publisher document."""
    try:
        with open(path, "rb") as fh:
            return fh.read(8) == OLE_SIGNATURE
    except OSError:
        return False


def cleanup_workdir(d):
    shutil.rmtree(d, ignore_errors=True)
