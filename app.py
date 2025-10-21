# app.py
# ──────────────────────────────────────────────────────────────
# Chatbay Analyzer → Flask + OpenAI Vision (v5.8)
# Adds: multi-host allowlist, PNG support, public URL preflight, inline CSV
# Routes:
#   /health, /openapi.json, /gallery, /preview_csv, /export_csv, /export_csv_text
# Env (additions):
#   ALLOWED_HOSTS=chatbay.site,i.postimg.cc,cdn.chatbay.site
# ──────────────────────────────────────────────────────────────

import os
import io
import csv
import json
import time
import math
import datetime
import traceback
import urllib.parse
from statistics import median
from typing import List, Dict, Any, Optional

import requests
from flask import Flask, jsonify, send_file, request, Response, send_from_directory
from openai import OpenAI

# ──────────────────────────────────────────────────────────────
# Setup
app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "ChatbayAnalyzer/5.8 (+https://chatbay-analyzer.onrender.com)"
})
REQUEST_TIMEOUT = (6.0, 30.0)

# ──────────────────────────────────────────────────────────────
# Env helpers
def getenv_str(key, default): return str(os.getenv(key, default)).strip()
def getenv_int(key, default):
    try: return int(os.getenv(key, str(default)))
    except Exception: return default
def getenv_float(key, default):
    try: return float(os.getenv(key, str(default)))
    except Exception: return default

# ──────────────────────────────────────────────────────────────
# Config
DEFAULT_PHOTOS_PER_ITEM = getenv_int("DEFAULT_PHOTOS_PER_ITEM", 4)
DEFAULT_CONDITION       = getenv_str("DEFAULT_CONDITION", "preowned").lower()

GALLERY_URL             = getenv_str("CHATBAY_GALLERY_URL", "https://chatbay.site/wp-json/chatbay/v1/gallery")
EBAY_UPLOADS_URL        = getenv_str("EBAY_UPLOADS_URL", "https://chatbay.site/ebay-media")

# NEW: allow multiple image hosts (comma-separated)
_default_host = urllib.parse.urlsplit(EBAY_UPLOADS_URL).netloc.lower()
ALLOWED_HOSTS           = {h.strip().lower() for h in getenv_str("ALLOWED_HOSTS", _default_host).split(",") if h.strip()}

DEFAULT_LOCATION        = getenv_str("EBAY_LOCATION", "Middletown, CT, USA")
DEFAULT_SHIP_PROFILE    = getenv_str("EBAY_SHIP_PROFILE", "ADV FREE 2 DAYS")
DEFAULT_RET_PROFILE     = getenv_str("EBAY_RET_PROFILE", "No returns accepted")
DEFAULT_PAY_PROFILE     = getenv_str("EBAY_PAY_PROFILE", "eBay Payments")

SLEEP_BETWEEN_ITEMS     = getenv_float("BATCH_SLEEP", 0.8)
MAX_RETRIES             = getenv_int("MAX_RETRIES", 3)
EBAY_APP_ID             = getenv_str("EBAY_APP_ID", "")

# ──────────────────────────────────────────────────────────────
# Maps
CATEGORY_MAP = {
    "panties": "11507", "underwear": "11507", "lingerie": "11514",
    "t-shirt": "15687", "shirt": "15687", "tee": "15687",
    "sweatshirt": "155226", "hoodie": "155226",
    "jacket": "57988", "jeans": "11483", "shorts": "15690", "pants": "57989",
    "bag": "169291", "tote": "169291", "patch": "156521", "button": "10960",
    "hat": "163571", "cap": "163571", "magazine": "280", "book": "261186",
}
CONDITION_ID_MAP = {"new": 1000, "preowned": 3000, "parts": 7000}

# ──────────────────────────────────────────────────────────────
# URL / Photo helpers
ALLOWED_EXTS = (".jpg", ".jpeg", ".png")

def _httpsify(url: str) -> str:
    url = url.strip()
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    return url

def ensure_public(u: str) -> bool:
    """HEAD check to confirm the URL is publicly reachable and is an image."""
    try:
        r = SESSION.head(u, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return False
        ctype = r.headers.get("Content-Type", "").lower()
        return ctype.startswith("image/")
    except Exception:
        return False

def sanitize_photo_url(u: str) -> Optional[str]:
    if not u:
        return None
    u = _httpsify(u)
    parts = urllib.parse.urlsplit(u)
    if not parts.netloc or parts.netloc.lower() not in ALLOWED_HOSTS:
        return None
    if not parts.path.lower().endswith(ALLOWED_EXTS):
        return None
    path = urllib.parse.quote(parts.path, safe="/._-")
    url = urllib.parse.urlunsplit(("https", parts.netloc, path, "", ""))
    return url if ensure_public(url) else None

def collect_item_photos(urls: List[str], max_photos: int) -> List[str]:
    cleaned, seen = [], set()
    limit = max(1, min(12, int(max_photos)))
    for raw in urls:
        cu = sanitize_photo_url(raw)
        if cu and cu not in seen:
            seen.add(cu)
            cleaned.append(cu)
            if len(cleaned) >= limit:
                break
    return cleaned

def pipe_join(urls: List[str]) -> str:
    return "|".join(urls)

# ──────────────────────────────────────────────────────────────
# Fetch WordPress gallery
def fetch_gallery_groups() -> List[Dict[str, Any]]:
    try:
        r = SESSION.get(GALLERY_URL, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        groups = data.get("groups", []) if isinstance(data, dict) else []
        print(f"✅ Gallery fetched: {len(groups)} groups")
        return groups
    except Exception as e:
        print("❌ Error fetching gallery:", e)
        return []

# ──────────────────────────────────────────────────────────────
# Vision Analysis using GPT-4o
def analyze_images_with_vision(photo_urls: List[str], condition: str) -> Dict[str, Any]:
    """
    Sends public image URLs to GPT-4o for structured analysis.
    Returns JSON: {title, brand, color, size, material, category_guess, short_description}
    """
    try:
        if not photo_urls:
            print("⚠️ Vision skipped: no valid photo URLs after sanitization/preflight.")
            return {}
        contents = [{
            "type": "text",
            "text": (
                "You are an expert eBay cataloger. "
                "Analyze these product photos and return strict JSON with keys:\n"
                "{"
                "\"title\": \"<=79 chars, SEO-rich\",\n"
                "\"category_guess\": \"noun like shirt, panties, hat, hoodie\",\n"
                "\"brand\": \"visible brand or likely maker\",\n"
                "\"color\": \"dominant color or palette\",\n"
                "\"material\": \"fabric/content if visible\",\n"
                "\"size\": \"tag or best guess\",\n"
                "\"year_or_style\": \"era or decade (e.g. 90s, Y2K)\",\n"
                "\"short_description\": \"2-3 factual sentences, no condition\"\n"
                "}"
            )
        }]
        for u in photo_urls:
            contents.append({"type": "image_url", "image_url": {"url": u}})

        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": contents}],
            temperature=0.2,
            max_tokens=500,
        )

        raw = (resp.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`").replace("json", "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1:
            raw = raw[start:end+1]
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"❌ Vision analysis failed: {e}")
        return {}

# ──────────────────────────────────────────────────────────────
# Helper functions
def clamp_title(t: str) -> str:
    return (t or "").strip()[:79]

def pick_category_id(category_guess: str) -> str:
    return CATEGORY_MAP.get((category_guess or "").strip().lower(), "15687")

# ──────────────────────────────────────────────────────────────
# Pricing research
def fetch_median_sold_price(keywords: str, category_id: str) -> Optional[float]:
    if not EBAY_APP_ID:
        return None
    try:
        params = {
            "OPERATION-NAME": "findCompletedItems",
            "SERVICE-VERSION": "1.13.0",
            "SECURITY-APPNAME": EBAY_APP_ID,
            "RESPONSE-DATA-FORMAT": "JSON",
            "REST-PAYLOAD": "true",
            "keywords": keywords,
            "categoryId": category_id,
            "itemFilter(0).name": "SoldItemsOnly",
            "itemFilter(0).value": "true",
            "paginationInput.entriesPerPage": "50",
        }
        r = SESSION.get("https://svcs.ebay.com/services/search/FindingService/v1", params=params, timeout=REQUEST_TIMEOUT)
        data = r.json()
        items = (
            data.get("findCompletedItemsResponse", [{}])[0]
                .get("searchResult", [{}])[0]
                .get("item", [])
        )
        prices = []
        for it in items:
            sold = it.get("sellingStatus", [{}])[0].get("sellingState", [""])[0]
            if sold.lower() != "endedwithsales":
                continue
            amt = it.get("sellingStatus", [{}])[0].get("currentPrice", [{}])[0].get("__value__")
            if amt:
                try:
                    val = float(amt)
                    if 2 <= val <= 5000:
                        prices.append(val)
                except: 
                    pass
        if not prices:
            return None
        prices.sort()
        return float(median(prices))
    except Exception as e:
        print("⚠️ Pricing fetch failed:", e)
        return None

# ──────────────────────────────────────────────────────────────
# CSV helpers
FULL_HEADERS = [
    "Action(SiteID=US|Country=US|Currency=USD|Version=1193)",
    "Custom label (SKU)", "Category ID", "Category name", "Title",
    "Relationship", "Relationship details", "Schedule Time", "P:UPC", "P:EPID",
    "Start price", "Quantity", "Item photo URL", "VideoID", "Condition ID",
    "Description", "Format", "Duration", "Buy It Now price",
    "Best Offer Enabled", "Best Offer Auto Accept Price", "Minimum Best Offer Price",
    "Immediate pay required", "Location", "Shipping service 1 option",
    "Shipping service 1 cost", "Shipping service 1 priority", "Shipping service 2 option",
    "Shipping service 2 cost", "Shipping service 2 priority", "Max dispatch time",
    "Returns accepted option", "Returns within option", "Refund option",
    "Return shipping cost paid by", "Shipping profile name", "Return profile name",
    "Payment profile name", "ProductCompliancePolicyID", "Regional ProductCompliancePolicies",
    "C:Style","C:Brand","C:Size Type","C:Color","C:Department","C:Size","C:Type",
    "C:Features","C:Character","C:Theme","C:Material","C:Fabric Type","C:Pattern","C:Vintage"
]

def build_desc_html(title: str, desc: str) -> str:
    return f'<p><center><h4>{title}</h4></center></p><p>{desc}</p>'

def schedule_time_next_day_22_gmt() -> str:
    now = datetime.datetime.utcnow()
    is_dst = now.month in (4,5,6,7,8,9,10)
    ny_offset = -4 if is_dst else -5
    next_day = now + datetime.timedelta(days=1)
    target = datetime.datetime(next_day.year, next_day.month, next_day.day, 22, 0, 0)
    utc_time = target - datetime.timedelta(hours=ny_offset)
    return utc_time.strftime("%Y-%m-%dT%H:%M:%SZ")

def row_from_item(analyzed: Dict[str, Any], photos: List[str], condition: str) -> List[str]:
    title = clamp_title(analyzed.get("title"))
    category_id = pick_category_id(analyzed.get("category_guess"))
    desc_html = build_desc_html(title, analyzed.get("short_description", ""))
    price = fetch_median_sold_price(title or analyzed.get("brand", ""), category_id) or 34.99
    photo_pipe = pipe_join(photos)

    row = [
        "Add","",category_id,"",title,"","",schedule_time_next_day_22_gmt(),"","",
        f"{price:.2f}","1",photo_pipe,"",str(CONDITION_ID_MAP.get(condition, 3000)),
        desc_html,"FixedPrice","GTC","","","","","",DEFAULT_LOCATION,"","","","","","","2",
        "","","","","",DEFAULT_SHIP_PROFILE,DEFAULT_RET_PROFILE,DEFAULT_PAY_PROFILE,"","",
        analyzed.get("year_or_style",""), analyzed.get("brand",""),"",
        analyzed.get("color",""),"", analyzed.get("size",""),
        analyzed.get("category_guess",""),"","","",
        analyzed.get("material",""),"","","Yes" if "90" in analyzed.get("year_or_style","").lower() or "y2k" in analyzed.get("year_or_style","").lower() else ""
    ]

    # ✅ pad to match full header length
    if len(row) < len(FULL_HEADERS):
        row += [""] * (len(FULL_HEADERS) - len(row))

    return row[:len(FULL_HEADERS)]  # ensures row never exceeds headers

# ──────────────────────────────────────────────────────────────
# Routes
@app.route("/health")
def health(): return jsonify({"ok": True, "version": "v5.8", "service": "chatbay-analyzer"})

@app.route("/openapi.json")
def serve_openapi():
    static_path = os.path.join(app.root_path, "static")
    f = os.path.join(static_path, "openapi.json")
    if os.path.exists(f):
        return send_from_directory(static_path, "openapi.json", mimetype="application/json")
    f2 = os.path.join(app.root_path, "openapi.json")
    return send_file(f2, mimetype="application/json") if os.path.exists(f2) else (jsonify({"error":"openapi.json not found"}),404)

@app.route("/gallery")
def get_gallery():
    groups = fetch_gallery_groups()
    return jsonify({"total_groups": len(groups), "groups": groups})

@app.route("/preview_csv")
def preview_csv():
    try:
        condition = request.args.get("condition", DEFAULT_CONDITION).lower()
        photos_per_item = int(request.args.get("photos_per_item", DEFAULT_PHOTOS_PER_ITEM))
        groups = fetch_gallery_groups()
        preview = []
        for g in groups[:2]:
            photos = collect_item_photos(g.get("photo_urls","").split(","), photos_per_item)
            analyzed = analyze_images_with_vision(photos, condition)
            row = row_from_item(analyzed, photos, condition)
            preview.append({h: row[i] if i < len(row) else "" for i, h in enumerate(FULL_HEADERS)})
        return jsonify({"preview_count": len(preview), "photos_per_item": photos_per_item, "condition": condition, "rows": preview})
    except Exception as e:
        print("❌ Preview error:", e)
        return jsonify({"error": "preview failed"}), 500

@app.route("/export_csv")
def export_csv():
    """
    Generates the complete eBay CSV and returns it inline (base64-encoded text)
    so ChatGPT can display or attach it directly in chat instead of a download link.
    """
    try:
        condition = request.args.get("condition", DEFAULT_CONDITION).lower()
        photos_per_item = int(request.args.get("photos_per_item", DEFAULT_PHOTOS_PER_ITEM))
        groups = fetch_gallery_groups()
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        w.writerow(FULL_HEADERS)

        for g in groups:
            photos = collect_item_photos(g.get("photo_urls", "").split(","), photos_per_item)
            analyzed = analyze_images_with_vision(photos, condition)
            w.writerow(row_from_item(analyzed, photos, condition))
            time.sleep(SLEEP_BETWEEN_ITEMS)

        csv_data = out.getvalue()
        filename = f"chatbay-ebay-export-{datetime.datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.csv"

        # Instead of sending as attachment, embed it in JSON for chat
        encoded = base64.b64encode(csv_data.encode("utf-8")).decode("utf-8")
        return jsonify({
            "ok": True,
            "filename": filename,
            "csv_preview": csv_data[:800],  # first few lines visible for chat context
            "csv_base64": encoded
        })
    except Exception as e:
        print("❌ Export error:", e)
        return jsonify({"error": "export failed"}), 500

@app.route("/export_csv_text")
def export_csv_text():
    """Explicit JSON (inline) CSV for Chat surfaces."""
    try:
        condition = request.args.get("condition", DEFAULT_CONDITION).lower()
        photos_per_item = int(request.args.get("photos_per_item", DEFAULT_PHOTOS_PER_ITEM))
        groups = fetch_gallery_groups()
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        w.writerow(FULL_HEADERS)
        for g in groups:
            photos = collect_item_photos(g.get("photo_urls","").split(","), photos_per_item)
            analyzed = analyze_images_with_vision(photos, condition)
            w.writerow(row_from_item(analyzed, photos, condition))
            time.sleep(SLEEP_BETWEEN_ITEMS)
        csv_data = out.getvalue()
        filename = f"chatbay-ebay-export-{datetime.datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.csv"
        return jsonify({"filename": filename, "csv_text": csv_data})
    except Exception as e:
        print("❌ Export text error:", e)
        return jsonify({"error": "export failed"}), 500

# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    print(f"🚀 Chatbay Analyzer v5.8 running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)

