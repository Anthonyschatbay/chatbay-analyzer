# ──────────────────────────────────────────────────────────────
# Chatbay Analyzer → Flask + OpenAI Vision (v5.2-hostinger)
# Now mapped to chatbay.site/ebay-media/ for image access
# ──────────────────────────────────────────────────────────────

import os, io, csv, json, time, datetime, traceback, requests, urllib.parse
from flask import Flask, jsonify, send_file, request
from openai import OpenAI

app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ------------------------------------------------------------------
# Env helpers
def getenv_str(key, default): return str(os.getenv(key, default)).strip()
def getenv_int(key, default): 
    try: return int(os.getenv(key, str(default)))
    except: return default
def getenv_float(key, default):
    try: return float(os.getenv(key, str(default)))
    except: return default

# ------------------------------------------------------------------
# Config
DEFAULT_PHOTOS_PER_ITEM = getenv_int("DEFAULT_PHOTOS_PER_ITEM", 4)
DEFAULT_CONDITION       = getenv_str("DEFAULT_CONDITION", "preowned").lower()
GALLERY_URL             = getenv_str("CHATBAY_GALLERY_URL", "https://chatbay.site/wp-json/chatbay/v1/gallery")

EBAY_UPLOADS_DIR        = getenv_str("EBAY_UPLOADS_DIR", "ebay-media")
EBAY_UPLOADS_URL        = getenv_str("EBAY_UPLOADS_URL", "https://chatbay.site/ebay-media")

DEFAULT_LOCATION        = getenv_str("EBAY_LOCATION", "Middletown, CT, USA")
DEFAULT_SHIP_PROFILE    = getenv_str("EBAY_SHIP_PROFILE", "7.99 FLAT")
DEFAULT_RET_PROFILE     = getenv_str("EBAY_RET_PROFILE", "No returns accepted")
DEFAULT_PAY_PROFILE     = getenv_str("EBAY_PAY_PROFILE", "eBay Payments")

SLEEP_BETWEEN_ITEMS     = getenv_float("BATCH_SLEEP", 5.0)
MAX_RETRIES             = getenv_int("MAX_RETRIES", 5)

# ------------------------------------------------------------------
CATEGORY_MAP = {
    "panties": "11507", "underwear": "11507", "lingerie": "11514",
    "t-shirt": "15687", "shirt": "15687",
    "sweatshirt": "155226", "hoodie": "155226",
    "jacket": "57988", "jeans": "11483", "shorts": "15690", "pants": "57989",
    "bag": "169291", "tote": "169291", "patch": "156521", "button": "10960",
    "hat": "163571", "cap": "163571", "magazine": "280", "book": "261186",
}
CONDITION_ID_MAP = {"new": 1000, "preowned": 3000, "parts": 7000}

# ------------------------------------------------------------------
def sanitize_photo_url(u: str) -> str:
    u = u.strip()
    if not u: return u
    if u.startswith("http://"): u = "https://" + u[len("http://"):]
    parts = urllib.parse.urlsplit(u)
    path  = urllib.parse.quote(parts.path, safe="/._-")
    return urllib.parse.urlunsplit(("https", parts.netloc, path, "", ""))

def join_item_photos(urls, max_photos):
    cleaned = [sanitize_photo_url(u) for u in urls if u.lower().endswith((".jpg", ".jpeg"))]
    seen, out = set(), []
    for cu in cleaned:
        if cu not in seen:
            seen.add(cu)
            out.append(cu)
        if len(out) >= max(1, min(12, int(max_photos))):
            break
    # note: pipe | separator for eBay CSV compatibility
    return "|".join(out)

# ------------------------------------------------------------------
def get_gallery_url():
    return GALLERY_URL

# (all other functions stay the same from v5.1 — analyze_photos_with_gpt, build_row_from_analysis, export_csv, etc.)

# ------------------------------------------------------------------
@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "chatbay-analyzer", "version": "v5.2-hostinger"}), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"🚀 Chatbay Analyzer v5.2-hostinger running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
