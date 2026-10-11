"""Site configuration. Override with environment variables in production."""

import os

# --- Payments (Stripe Payment Links, created later in the Stripe dashboard) ---
# Placeholder "#" until the user creates the links. The frontend paywall
# shows a "coming soon" state while these are unset.
STRIPE_ONETIME_URL = os.environ.get("STRIPE_ONETIME_URL", "#")  # $9 one-time: 200-file batch pack
STRIPE_SUB_URL = os.environ.get("STRIPE_SUB_URL", "#")          # $29/mo: unlimited batch + API
PRICE_ONETIME = "$9"
PRICE_SUB = "$29/mo"

# --- Paid unlock (until Stripe webhooks exist) ---
# The $9 pack buyer gets this key (shown on the Stripe thank-you page / email);
# entering it in the batch form unlocks the full verification report.
# Unset/empty = paid tier unavailable, paywall shows "coming soon".
BATCH_UNLOCK_KEY = os.environ.get("BATCH_UNLOCK_KEY", "")

# --- Free quotas (per client IP, rolling 24h window) ---
FREE_SINGLE_PER_DAY = int(os.environ.get("FREE_SINGLE_PER_DAY", "3"))
FREE_BATCH_FILES = int(os.environ.get("FREE_BATCH_FILES", "5"))   # first N files of a ZIP free
FREE_BATCH_PER_DAY = int(os.environ.get("FREE_BATCH_PER_DAY", "2"))
MAX_BATCH_FILES = int(os.environ.get("MAX_BATCH_FILES", "200"))

# --- Upload limits ---
MAX_SINGLE_MB = int(os.environ.get("MAX_SINGLE_MB", "50"))
MAX_ZIP_MB = int(os.environ.get("MAX_ZIP_MB", "500"))

# --- Misc ---
SITE_NAME = "PubMigrator"
SITE_URL = os.environ.get("SITE_URL", "http://localhost:8000")  # set to real domain at deploy
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "hello@example.com")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
