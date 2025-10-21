# ──────────────────────────────────────────────────────────────
# Chatbay Analyzer → Flask + OpenAI Vision (v5.3 unified workflow)
# Aligned with Workflow v4.3, for Render + Hostinger hybrid
# Routes:
#   /health
#   /openapi.json
#   /gallery
#   /preview_csv
#   /export_csv
# ──────────────────────────────────────────────────────────────

import os, io, csv, json, time, datetime, traceback, requests, urllib.parse
from flask import Flask, jsonify, send_file, request
from openai import OpenAI

# ──────────────────────────────────────────────────────────────
# Setup
app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))  # ✅ FIXED — no 'proxies' argument

# ──────────────────────────────────────────────────────────────
# Env helpers
def getenv_str(key, default): return str(os.getenv(key, default)).strip()
def getenv_int(key, default): 
    try: return int(os.getenv(key, str(default)))
    except: return default
def getenv_float(key, default):
    try: return float(os.getenv(key, str(default)))
    except: return default

# ──────────────────────────────────────────────────────────────
# Config
DEFAULT_PHOTOS_PER_ITEM = getenv_int("DEFAULT_PHOTOS_PER_ITEM", 4)
DEFAULT_CONDITION       = getenv_str("DEFAULT_CONDITION", "preowned").lower()
GALLERY_URL             = getenv_str("CHATBAY_GALLERY_URL", "https://chatbay.site/wp-json/chatbay/v1/gallery")

EBAY_UPLOADS_DIR        = getenv_str("EBAY_UPLOADS_DIR", "ebay-media")
EBAY_UPLOADS_URL        = getenv_str("EBAY_UPLOADS_URL", "https://chatbay.site/ebay-media")

DEFAULT_LOCATION        = getenv_str("EBAY_LOCATION", "Middletown, CT, USA")
DEFAULT_SHIP_PROFILE    = getenv_str("EBAY_SHIP_PROFILE", "ADV FREE 2 DAYS")
DEFAULT_RET_PROFILE     = getenv_str("EBAY_RET_PROFILE", "No returns accepted")
DEFAULT_PAY_PROFILE     = getenv_str("EBAY_PAY_PROFILE", "eBay Payments")

SLEEP_BETWEEN_ITEMS     = getenv_float("BATCH_SLEEP", 5.0)
MAX_RETRIES             = getenv_int("MAX_RETRIES", 5)

# ──────────────────────────────────────────────────────────────
CATEGORY_MAP = {
    "panties": "11507", "underwear": "11507", "lingerie": "11514",
    "t-shirt": "15687", "shirt": "15687",
    "sweatshirt": "155226", "hoodie": "155226",
    "jacket": "57988", "jeans": "11483", "shorts": "15690", "pants": "57989",
    "bag": "169291", "tote": "169291", "patch": "156521", "button": "10960",
    "hat": "163571", "cap": "163571", "magazine": "280", "book": "261186",
}
CONDITION_ID_MAP = {"new": 1000, "preowned": 3000, "parts": 7000}

# ──────────────────────────────────────────────────────────────
# Utility: Sanitize URLs
def sanitize_photo_url(u: str) -> str:
    u = u.strip()
    if not u: return u
    if u.startswith("http://"): u = "https://" + u[len("http://"):]
    parts = urllib.parse.urlsplit(u)
    path  = urllib.parse.quote(parts.path, safe="/._-")
    return urllib.parse.urlunsplit(("https", parts.netloc, path, "", ""))

def join_item_photos(urls, max_photos):
    cleaned = [sanitize_photo_url(u) for u in urls if u.lower().endswith((".jpg", ".jpeg", ".png"))]
    seen, out = set(), []
    for cu in cleaned:
        if cu not in seen:
            seen.add(cu)
            out.append(cu)
        if len(out) >= max(1, min(12, int(max_photos))):
            break
    return ",".join(out)

# ──────────────────────────────────────────────────────────────
# Fetch WordPress gallery
def fetch_gallery():
    try:
        resp = requests.get(GALLERY_URL, timeout=10)
        data = resp.json()
        if "groups" in data and data["groups"]:
            print(f"✅ Gallery fetched successfully: {len(data['groups'])} groups")
            return data["groups"]
        else:
            print("⚠️ No gallery groups found:", data)
            return []
    except Exception as e:
        print("❌ Error fetching gallery:", e)
        return []

# ──────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "chatbay-analyzer", "version": "v5.3", "source": "Render/Hostinger"})

# ──────────────────────────────────────────────────────────────
@app.route("/openapi.json")
def serve_openapi():
    return send_file("openapi.json")

# ──────────────────────────────────────────────────────────────
@app.route("/gallery")
def get_gallery():
    """Proxy to chatbay.site/wp-json/chatbay/v1/gallery"""
    groups = fetch_gallery()
    return jsonify({"total_groups": len(groups), "groups": groups})

# ──────────────────────────────────────────────────────────────
@app.route("/preview_csv")
def preview_csv():
    """Preview first 2 CSV rows with live gallery data"""
    condition = request.args.get("condition", DEFAULT_CONDITION)
    photos_per_item = int(request.args.get("photos_per_item", DEFAULT_PHOTOS_PER_ITEM))
    groups = fetch_gallery()

    if not groups:
        return jsonify({"error": "No gallery data found"}), 500

    preview_rows = []
    for idx, group in enumerate(groups[:2]):
        photos = [p.strip() for p in group["photo_urls"].split(",") if p.strip()]
        photo_str = join_item_photos(photos, photos_per_item)
        title = f"Listing {idx+1}"
        preview_rows.append({
            "Title": title,
            "Category ID": "15687",
            "Start price": "34.99",
            "Condition ID": CONDITION_ID_MAP.get(condition, 3000),
            "Item photo URL": photo_str,
            "Format": "FixedPrice",
            "Duration": "GTC",
            "Shipping profile name": DEFAULT_SHIP_PROFILE,
            "Return profile name": DEFAULT_RET_PROFILE,
            "Payment profile name": DEFAULT_PAY_PROFILE
        })

    preview = {
        "preview_count": len(preview_rows),
        "photos_per_item": photos_per_item,
        "condition": condition,
        "rows": preview_rows
    }
    return jsonify(preview)

# ──────────────────────────────────────────────────────────────
@app.route("/export_csv")
def export_csv():
    """Generate full eBay-ready CSV using live gallery data"""
    condition = request.args.get("condition", DEFAULT_CONDITION)
    photos_per_item = int(request.args.get("photos_per_item", DEFAULT_PHOTOS_PER_ITEM))
    groups = fetch_gallery()

    if not groups:
        return jsonify({"error": "No gallery data found"}), 500

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Action(SiteID=US|Country=US|Currency=USD|Version=1193)",
        "Category ID", "Title", "Start price", "Quantity",
        "Item photo URL", "Condition ID", "Description",
        "Format", "Duration",
        "Shipping profile name", "Return profile name", "Payment profile name"
    ])

    for idx, group in enumerate(groups):
        photos = [p.strip() for p in group["photo_urls"].split(",") if p.strip()]
        photo_str = join_item_photos(photos, photos_per_item)
        title = f"Listing {idx+1}"
        desc = f"<p><center><h4>{title}</h4></center></p><p>Auto-generated listing from Chatbay Analyzer.</p>"

        writer.writerow([
            "Add", "15687", title, "34.99", "1", photo_str,
            CONDITION_ID_MAP.get(condition, 3000),
            desc, "FixedPrice", "GTC",
            DEFAULT_SHIP_PROFILE, DEFAULT_RET_PROFILE, DEFAULT_PAY_PROFILE
        ])

    csv_data = output.getvalue()
    output.close()
    filename = f"chatbay-ebay-export-{datetime.datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.csv"

    return csv_data, 200, {
        "Content-Type": "text/csv",
        "Content-Disposition": f"attachment; filename={filename}"
    }

# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"🚀 Chatbay Analyzer v5.3 running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
