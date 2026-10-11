"""PubMigrator — .pub migration tool for the post-Publisher world.

Positioning: "the only converter that tells you which files it broke."
Conversion is the commodity; the per-file VERIFICATION REPORT is the product.

Routes:
  GET  /                        SEO landing page + embedded converter tool
  POST /api/convert             single .pub -> converted file (free: 3/day/IP),
                                quality verdict in X-Quality headers
  POST /api/batch               ZIP of .pub files -> starts a background job,
                                returns job_id immediately (redirect to report page)
  GET  /batch/<job_id>          verification report page (polls while processing)
  GET  /api/job/<id>/report     report JSON (details gated by paid unlock)
  GET  /api/job/<id>/file/<i>  download converted file
  GET  /api/job/<id>/thumb/<i>.png   first-page thumbnail (paid)
  GET  /api/job/<id>/failures.csv    failure list CSV (paid)
  POST /api/job/<id>/retry      re-run conversion for failed files (paid)
  POST /api/v1/convert          API stub ($29/mo plan) - 402 until launched

Free vs paid:
  free   single file (3/day) -> basic verdict badge (passed/suspicious/failed)
         batch: first 5 files converted + basic badges + downloads
  $9     full verification report: per-file check details, failure reasons,
         thumbnails for suspicious files, CSV export, one-click retry
  $29    unlimited batch + API
Paid unlock is a key (BATCH_UNLOCK_KEY env); Stripe Payment Links are wired
in later — until then the paywall shows "coming soon".

Privacy: uploaded files live in a per-job temp dir, swept after 24h.
Nothing is retained beyond that.
"""

import csv
import io
import json
import os
import re
import tempfile
import threading
import time
import zipfile
from collections import defaultdict

from flask import Flask, request, jsonify, send_file, render_template, abort, Response

import config
from converter import convert_pub, looks_like_pub, new_workdir, cleanup_workdir, FORMATS
from verifier import verify_file, make_thumbnail, VERDICT_COPY

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = config.MAX_ZIP_MB * 1024 * 1024

JOBS_ROOT = os.path.join(tempfile.gettempdir(), "pubmigr-jobs")
JOB_TTL = 24 * 3600

# --- Simple in-memory rate limiting (per IP, rolling 24h). ---
_hits = defaultdict(list)


def _client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() or request.remote_addr or "unknown")


def _quota_ok(kind, limit):
    now = time.time()
    key = (_client_ip(), kind)
    window = [t for t in _hits[key] if now - t < 86400]
    _hits[key] = window
    return len(window) < limit


def _quota_hit(kind):
    _hits[(_client_ip(), kind)].append(time.time())


def _paywall_payload(reason):
    return {
        "paywall": True,
        "reason": reason,
        "onetime": {
            "price": config.PRICE_ONETIME,
            "label": "Batch Pack — 200 files, one-time + full verification report",
            "url": config.STRIPE_ONETIME_URL,
            "ready": config.STRIPE_ONETIME_URL != "#",
        },
        "subscription": {
            "price": config.PRICE_SUB,
            "label": "Unlimited batch + API",
            "url": config.STRIPE_SUB_URL,
            "ready": config.STRIPE_SUB_URL != "#",
        },
    }


def _safe_name(name):
    name = os.path.basename(name or "file")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name[:120] or "file"


def _hdr(value):
    value = value.replace("\u2014", "-").replace("\u2013", "-").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", value).encode("latin-1", "replace").decode("latin-1")


def _paid_request():
    key = config.BATCH_UNLOCK_KEY
    if not key:
        return False
    given = (request.form.get("unlock") or request.args.get("unlock") or "").strip()
    return bool(given) and given == key


# ---------------------------------------------------------------------------
# Job store
# ---------------------------------------------------------------------------

def _job_dir(job_id):
    return os.path.join(JOBS_ROOT, job_id)


def _write_report(job_id, report):
    path = os.path.join(_job_dir(job_id), "report.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(report, fh)
    os.replace(tmp, path)


def _read_report(job_id):
    path = os.path.join(_job_dir(job_id), "report.json")
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _sweep_jobs():
    now = time.time()
    try:
        names = os.listdir(JOBS_ROOT)
    except OSError:
        return
    for name in names:
        d = os.path.join(JOBS_ROOT, name)
        try:
            if now - os.path.getmtime(d) > JOB_TTL:
                import shutil
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _public_report(report):
    """Strip paid-only details for free jobs."""
    pub = {
        "job_id": report["job_id"],
        "status": report["status"],
        "paid": report["paid"],
        "created": report["created"],
        "progress": report.get("progress", {}),
        "summary": report.get("summary", {}),
        "files": [],
    }
    for f in report.get("files", []):
        if f.get("locked"):
            pub["files"].append({"name": f["name"], "locked": True})
            continue
        if f.get("verdict") is None:
            # still being processed by the background worker
            pub["files"].append({"name": f["name"], "verdict": "processing",
                                 "pages": 0, "words": 0, "images": 0, "size": 0,
                                 "download": None, "has_thumb": False})
            continue
        entry = {
            "name": f["name"],
            "verdict": f["verdict"],
            "badge": VERDICT_COPY[f["verdict"]],
            "pages": f["signals"]["pages"],
            "words": f["signals"]["words"],
            "images": f["signals"]["images"],
            "size": f["signals"]["size"],
            "download": f"/api/job/{report['job_id']}/file/{f['idx']}",
            "has_thumb": bool(f.get("thumb")),
        }
        if report["paid"]:
            entry["reasons"] = f["reasons"]
            entry["src_pictures"] = f["src_pictures"]
            entry["thumb_url"] = f"/api/job/{report['job_id']}/thumb/{f['idx']}.png" if f.get("thumb") else None
        pub["files"].append(entry)
    if report["paid"]:
        pub["csv_url"] = f"/api/job/{report['job_id']}/failures.csv"
        pub["retry_url"] = f"/api/job/{report['job_id']}/retry"
    return pub


# ---------------------------------------------------------------------------
# Background batch worker
# ---------------------------------------------------------------------------

def _process_file(job_id, idx, base, data, out_dir, thumb_dir, paid):
    """Convert + verify one file. Returns the report entry dict."""
    entry = {"idx": idx, "name": base, "verdict": "failed",
             "reasons": [], "signals": {}, "src_pictures": None,
             "download": None, "thumb": False}
    if len(data) == 0 or len(data) > config.MAX_SINGLE_MB * 1024 * 1024:
        entry["reasons"] = [{"severity": "fail", "text": "File is empty or exceeds the per-file size limit."}]
        return entry
    in_path = os.path.join(_job_dir(job_id), "in", f"f{idx}_{base}")
    with open(in_path, "wb") as fh:
        fh.write(data)
    if not looks_like_pub(in_path):
        entry["reasons"] = [{"severity": "fail", "text":
            "Not a real Publisher file — it lacks the .pub document signature (renamed or corrupted)."}]
        return entry
    out_path, err = convert_pub(in_path, out_dir, "pdf")
    if err:
        entry["reasons"] = [{"severity": "fail", "text": "Conversion failed: " + err[:200]}]
        return entry
    v = verify_file(in_path, out_path)
    entry["verdict"] = v["verdict"]
    entry["reasons"] = v["reasons"]
    entry["signals"] = v["signals"]
    entry["src_pictures"] = v["src_pictures"]
    dl_name = os.path.splitext(base)[0] + ".pdf"
    kept = os.path.join(out_dir, f"f{idx}_{dl_name}")
    if out_path != kept:
        os.replace(out_path, kept)
    entry["download"] = True
    if paid and v["verdict"] == "suspicious":
        tp = os.path.join(thumb_dir, f"{idx}.png")
        if make_thumbnail(kept, tp):
            entry["thumb"] = True
    return entry


def _run_batch(job_id, members_data, paid):
    """Background thread: convert + verify every file, updating report.json."""
    report = _read_report(job_id) or {}
    report["status"] = "processing"
    _write_report(job_id, report)
    out_dir = os.path.join(_job_dir(job_id), "out")
    thumb_dir = os.path.join(_job_dir(job_id), "thumbs")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(thumb_dir, exist_ok=True)

    for i, (base, data) in enumerate(members_data):
        entry = _process_file(job_id, i, base, data, out_dir, thumb_dir, paid)
        report = _read_report(job_id) or report
        # replace-or-append (retry may re-run individual entries later)
        files = report.get("files", [])
        replaced = False
        for j, f in enumerate(files):
            if f.get("idx") == i:
                files[j] = entry
                replaced = True
                break
        if not replaced:
            files.append(entry)
        report["files"] = files
        report["progress"] = {"done": i + 1, "total": len(members_data)}
        _write_report(job_id, report)

    report = _read_report(job_id) or report
    counts = {"passed": 0, "suspicious": 0, "failed": 0, "locked": 0}
    for f in report.get("files", []):
        v = "locked" if f.get("locked") else (f.get("verdict") or "failed")
        counts[v] = counts.get(v, 0) + 1
    report["summary"] = {"total": len(report.get("files", [])), **counts}
    report["status"] = "done"
    _write_report(job_id, report)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template(
        "index.html",
        site_url=config.SITE_URL,
        contact_email=config.CONTACT_EMAIL,
        onetime_url=config.STRIPE_ONETIME_URL,
        sub_url=config.STRIPE_SUB_URL,
        payments_ready=config.STRIPE_ONETIME_URL != "#",
    )


@app.route("/api/convert", methods=["POST"])
def api_convert():
    """Single .pub upload -> converted file download + verification verdict."""
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded."}), 400
    f = request.files["file"]
    fmt = (request.form.get("format") or "pdf").lower()
    if fmt not in FORMATS:
        return jsonify({"ok": False, "error": "Unsupported format."}), 400
    name = _safe_name(f.filename)
    if not name.lower().endswith(".pub"):
        return jsonify({"ok": False, "error": "Please upload a .pub file."}), 400
    if not _quota_ok("single", config.FREE_SINGLE_PER_DAY):
        return jsonify({"ok": False, **_paywall_payload(
            f"Free limit reached ({config.FREE_SINGLE_PER_DAY} conversions per day)."
        )}), 402

    workdir = new_workdir()
    in_path = os.path.join(workdir, "in", name)
    f.save(in_path)
    if os.path.getsize(in_path) > config.MAX_SINGLE_MB * 1024 * 1024:
        cleanup_workdir(workdir)
        return jsonify({"ok": False, "error": f"File too large (max {config.MAX_SINGLE_MB} MB)."}), 400
    if os.path.getsize(in_path) == 0:
        cleanup_workdir(workdir)
        return jsonify({"ok": False, "error": "Empty file."}), 400
    if not looks_like_pub(in_path):
        cleanup_workdir(workdir)
        return jsonify({"ok": False, "error": "This doesn't look like a real Microsoft Publisher file."}), 400

    out_path, err = convert_pub(in_path, os.path.join(workdir, "out"), fmt)
    _quota_hit("single")
    if err:
        cleanup_workdir(workdir)
        return jsonify({"ok": False, "error": "Conversion failed: " + err[:200]}), 422

    # Verification: PDF gets the full battery; other formats get a light check.
    if fmt == "pdf":
        v = verify_file(in_path, out_path)
        verdict, badge = v["verdict"], VERDICT_COPY[v["verdict"]]
    else:
        verdict, badge = ("passed", VERDICT_COPY["passed"]) if os.path.getsize(out_path) > 0 else ("failed", VERDICT_COPY["failed"])

    with open(out_path, "rb") as fh:
        data = fh.read()
    cleanup_workdir(workdir)
    dl_name = os.path.splitext(name)[0] + "." + FORMATS[fmt]["ext"]
    resp = Response(data, mimetype=FORMATS[fmt]["mime"])
    resp.headers["Content-Disposition"] = f'attachment; filename="{_hdr(dl_name)}"'
    resp.headers["X-Quality"] = verdict
    resp.headers["X-Quality-Label"] = _hdr(badge["label"])
    resp.headers["X-Quality-Note"] = _hdr(badge["note"])
    return resp


@app.route("/api/batch", methods=["POST"])
def api_batch():
    """ZIP of .pub files -> background verification job. Returns job_id."""
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No ZIP uploaded."}), 400
    f = request.files["file"]
    name = _safe_name(f.filename)
    if not name.lower().endswith(".zip"):
        return jsonify({"ok": False, "error": "Please upload a .zip file."}), 400
    if not _quota_ok("batch", config.FREE_BATCH_PER_DAY):
        return jsonify({"ok": False, **_paywall_payload(
            f"Free batch limit reached ({config.FREE_BATCH_PER_DAY} per day)."
        )}), 402

    paid = _paid_request()
    raw = f.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        return jsonify({"ok": False, "error": "Invalid ZIP file."}), 400

    members = [m for m in zf.namelist()
               if m.lower().endswith(".pub") and not m.endswith("/")]
    if not members:
        return jsonify({"ok": False, "error": "No .pub files found in the ZIP."}), 400
    if len(members) > config.MAX_BATCH_FILES:
        return jsonify({"ok": False,
                        "error": f"Too many files (max {config.MAX_BATCH_FILES} per batch)."}), 400

    members_data = []
    seen = {}
    for m in members:
        base = _safe_name(os.path.basename(m))
        # Duplicate basenames (different folders in the ZIP) get suffixed
        # so downloads, thumbnails and retry all stay unambiguous.
        if base in seen:
            seen[base] += 1
            stem, ext = os.path.splitext(base)
            base = f"{stem}_{seen[base]}{ext}"
        else:
            seen[base] = 0
        try:
            members_data.append((base, zf.read(m)))
        except Exception:
            members_data.append((base, b""))
    zf.close()
    _quota_hit("batch")
    _sweep_jobs()

    import uuid
    job_id = uuid.uuid4().hex[:16]
    jd = _job_dir(job_id)
    os.makedirs(os.path.join(jd, "in"), exist_ok=True)

    free_n = config.FREE_BATCH_FILES if not paid else len(members_data)
    initial_files = []
    todo = []
    for i, (base, data) in enumerate(members_data):
        if i < free_n:
            todo.append((base, data))
            initial_files.append({"idx": i, "name": base, "verdict": None})
        else:
            initial_files.append({"idx": i, "name": base, "locked": True,
                                  "verdict": None})
    report = {
        "job_id": job_id,
        "created": time.time(),
        "paid": paid,
        "status": "queued",
        "progress": {"done": 0, "total": len(todo)},
        "summary": {},
        "files": initial_files,
    }
    _write_report(job_id, report)

    t = threading.Thread(target=_run_batch, args=(job_id, todo, paid), daemon=True)
    t.start()

    payload = {"ok": True, "job_id": job_id, "paid": paid,
               "total": len(members_data), "processing": len(todo),
               "locked": len(members_data) - len(todo)}
    if not paid and len(members_data) > len(todo):
        payload.update(_paywall_payload(
            f"{len(todo)} of {len(members_data)} files converting free. "
            f"Unlock the remaining {len(members_data) - len(todo)} plus the full "
            "verification report."
        ))
    return jsonify(payload)


@app.route("/batch/<job_id>")
def batch_report_page(job_id):
    report = _read_report(job_id)
    if not report:
        abort(404)
    return render_template(
        "report.html",
        job_id=job_id,
        site_url=config.SITE_URL,
        contact_email=config.CONTACT_EMAIL,
        onetime_url=config.STRIPE_ONETIME_URL,
        sub_url=config.STRIPE_SUB_URL,
        payments_ready=config.STRIPE_ONETIME_URL != "#",
    )


@app.route("/api/job/<job_id>/report")
def api_job_report(job_id):
    if not re.fullmatch(r"[0-9a-f]{16}", job_id or ""):
        abort(404)
    report = _read_report(job_id)
    if not report:
        abort(404)
    return jsonify(_public_report(report))


@app.route("/api/job/<job_id>/file/<int:idx>")
def api_job_file(job_id, idx):
    if not re.fullmatch(r"[0-9a-f]{16}", job_id or ""):
        abort(404)
    report = _read_report(job_id)
    if not report:
        abort(404)
    for f in report.get("files", []):
        if f.get("idx") == idx and not f.get("locked") and f.get("download"):
            base = _safe_name(os.path.splitext(f["name"])[0]) + ".pdf"
            path = os.path.join(_job_dir(job_id), "out", f"f{idx}_{base}")
            if os.path.exists(path):
                return send_file(path, as_attachment=True, download_name=base)
    abort(404)


@app.route("/api/job/<job_id>/thumb/<int:idx>.png")
def api_job_thumb(job_id, idx):
    if not re.fullmatch(r"[0-9a-f]{16}", job_id or ""):
        abort(404)
    report = _read_report(job_id)
    if not report or not report.get("paid"):
        abort(404)
    path = os.path.join(_job_dir(job_id), "thumbs", f"{idx}.png")
    if os.path.exists(path):
        return send_file(path, mimetype="image/png")
    abort(404)


@app.route("/api/job/<job_id>/failures.csv")
def api_job_csv(job_id):
    if not re.fullmatch(r"[0-9a-f]{16}", job_id or ""):
        abort(404)
    report = _read_report(job_id)
    if not report or not report.get("paid"):
        abort(404)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["file", "verdict", "pages", "words", "images", "size_bytes", "reasons"])
    for f in report.get("files", []):
        if f.get("locked") or f.get("verdict") == "passed":
            continue
        s = f.get("signals", {})
        reasons = " | ".join(r["text"] for r in f.get("reasons", []))
        w.writerow([f["name"], f["verdict"], s.get("pages"), s.get("words"),
                    s.get("images"), s.get("size"), reasons])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=verification-failures-{job_id}.csv"})


@app.route("/api/job/<job_id>/retry", methods=["POST"])
def api_job_retry(job_id):
    """Re-run conversion+verification for failed files (paid)."""
    if not re.fullmatch(r"[0-9a-f]{16}", job_id or ""):
        abort(404)
    report = _read_report(job_id)
    if not report or not report.get("paid"):
        abort(404)
    if report.get("status") == "processing":
        return jsonify({"ok": False, "error": "Job still processing."}), 409
    # Re-read original inputs for failed entries (matched by idx, not name)
    jd = _job_dir(job_id)
    tasks = []
    for f in report.get("files", []):
        if f.get("locked") or f.get("verdict") != "failed":
            continue
        idx = f["idx"]
        in_path = os.path.join(jd, "in", f"f{idx}_{_safe_name(f['name'])}")
        if os.path.exists(in_path):
            with open(in_path, "rb") as fh:
                tasks.append((idx, f["name"], fh.read()))
    if not tasks:
        return jsonify({"ok": True, "retried": 0})
    report["status"] = "processing"
    _write_report(job_id, report)

    def _retry():
        out_dir = os.path.join(jd, "out")
        thumb_dir = os.path.join(jd, "thumbs")
        for i, (idx, base, data) in enumerate(tasks):
            entry = _process_file(job_id, idx, base, data, out_dir, thumb_dir, True)
            rep = _read_report(job_id) or report
            files = rep.get("files", [])
            for j, fe in enumerate(files):
                if fe.get("idx") == idx:
                    files[j] = entry
                    break
            rep["files"] = files
            rep["progress"] = {"done": i + 1, "total": len(tasks)}
            _write_report(job_id, rep)
        rep = _read_report(job_id) or report
        counts = {"passed": 0, "suspicious": 0, "failed": 0, "locked": 0}
        for fe in rep.get("files", []):
            v = "locked" if fe.get("locked") else fe.get("verdict", "failed")
            counts[v] = counts.get(v, 0) + 1
        rep["summary"] = {"total": len(rep.get("files", [])), **counts}
        rep["status"] = "done"
        _write_report(job_id, rep)

    threading.Thread(target=_retry, daemon=True).start()
    return jsonify({"ok": True, "retried": len(tasks)})


@app.route("/api/v1/convert", methods=["POST"])
def api_v1_convert():
    """Paid API stub ($29/mo plan). Returns 402 until the plan launches."""
    return jsonify({"ok": False, **_paywall_payload(
        "API access is part of the Unlimited plan."
    )}), 402


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.route("/guides")
def guides_index():
    import guides as _g
    return render_template(
        "guides.html",
        guides=_g.list_guides(),
        site_url=config.SITE_URL,
    )


@app.route("/guides/<slug>")
def guide_page(slug):
    import guides as _g
    result = _g.render_guide(slug)
    if not result:
        return "Guide not found", 404
    title, description, html = result
    return render_template(
        "guide.html",
        title=title,
        description=description,
        content=html,
        site_url=config.SITE_URL,
        slug=slug,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
