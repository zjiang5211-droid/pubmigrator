"""SEO guides section: loads markdown articles from guides/ and renders them."""

import os
import re

GUIDES_DIR = os.path.join(os.path.dirname(__file__), "guides")

# slug -> (title, description) — used for the index page and <title>/meta.
GUIDE_META = {
    "batch-convert-pub-to-pdf": (
        "How to Batch Convert PUB to PDF",
        "Converting hundreds of Publisher files? Here's how to batch convert PUB to PDF — and verify every file made it through.",
    ),
    "convert-pub-to-docx-editable": (
        "How to Convert PUB to Editable DOCX",
        "Need to edit a Publisher file in Word? How to convert PUB to DOCX without losing your layout.",
    ),
    "free-pub-to-pdf-converters-compared": (
        "Free PUB to PDF Converters Compared (2026)",
        "We tested the free PUB to PDF converters so you don't have to — including which ones silently break your files.",
    ),
    "how-to-open-pub-file-without-publisher": (
        "How to Open a PUB File Without Publisher",
        "Publisher is gone. Here are 5 ways to open .pub files without it — free options included.",
    ),
    "microsoft-publisher-alternative-2026": (
        "Microsoft Publisher Alternative in 2026",
        "Publisher was retired. What to use instead — and how to get your old files out.",
    ),
    "migrate-thousands-of-publisher-files": (
        "How to Migrate Thousands of Publisher Files",
        "Sitting on an archive of .pub files? A practical plan for bulk migration with verification.",
    ),
    "pub-file-wont-open": (
        "PUB File Won't Open? Fix It Here",
        "Your .pub file won't open. Here's what's probably wrong and how to get your content back.",
    ),
    "pub-to-pdf-converter-with-verification": (
        "PUB to PDF Converter with Verification",
        "Most converters won't tell you when they break a file. Ours does — per-file verification report included.",
    ),
    "publisher-2021-support-ends-october-13-2026": (
        "Publisher 2021 Support Ends October 13, 2026: What Actually Happens",
        "Support for Publisher 2021 ends October 13, 2026 — here's what actually changes, what keeps working, and how to migrate your archive this week.",
    ),
}

_markdown = None

def _get_markdown():
    global _markdown
    if _markdown is None:
        import markdown as _md
        _markdown = _md.Markdown(extensions=["extra", "toc"])
    return _markdown

def list_guides():
    """Return [{slug, title, description}] for all guides with .md files."""
    out = []
    for slug, (title, desc) in GUIDE_META.items():
        path = os.path.join(GUIDES_DIR, slug + ".md")
        if os.path.exists(path):
            out.append({"slug": slug, "title": title, "description": desc})
    return out

def render_guide(slug):
    """Return (title, description, html) for a guide, or None."""
    meta = GUIDE_META.get(slug)
    if not meta:
        return None
    path = os.path.join(GUIDES_DIR, slug + ".md")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        src = f.read()
    # Strip the first H1 (we render the title separately from meta).
    src = re.sub(r"^# .+\n+", "", src, count=1)
    md = _get_markdown()
    md.reset()
    html = md.convert(src)
    return meta[0], meta[1], html
