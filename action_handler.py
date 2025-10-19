# ──────────────────────────────────────────────────────────────
# Chatbay → GPT-4o Vision Analyzer + eBay CSV Exporter (v4.6 HYBRID)
# Flask app for Render.com deployment
# Hybrid Mode:
#   • You (in chat) choose: photos_per_item & condition (new|preowned|parts)
#   • App reads them as query params on /export_csv & /preview_csv
#   • /preview_csv returns first 2 rows as JSON (proof before print)
#
# Upgrades in v4.6:
#   • Safe float parsing for BATCH_SLEEP (no more '5.0' error)
#   • Intelligent batch throttling (auto-pause between groups)
#   • Descriptive SEO titles, no “Vintage Collectible”
#   • /status route exposes live config
#   • ?gallery= override supported
# ──────────────────────────────────────────────────────────────

import os, io, csv, json, time, datetime, traceback, requests
from typing import Dict, Any, List, Optional
from flask import Flask, jsonify, send_file, request
from openai import OpenAI

# ──────────────────────────────────────────────────────────────
# App setup
# ──────────────────────────────────────────────────────────────
app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ──────────────────────────────────────────────────────────────
# Safe env readers
# ──────────────────────────────────────────────────────────────
def getenv_float(key: str, default: float) -> float:
    """Safely parse environment variable as float."""
    try:
        v = os.getenv(key, str(default)).strip()
        return float(v)
    except Exception:
        print(f"⚠️ Invalid float for {key}, using default {default}")
        return float(default)

def getenv_int(key: str, default: int) -> int:
    """Safely parse environment variable as int."""
    try:
        v = os.getenv(key, str(default)).strip()
        return int(float(v))
    except Exception:
        print(f"⚠️ Invalid int for {key}, using default {default}")
        return int(default)

def getenv_str(key: str, default: str) -> str:
    return str(os.getenv(key, default)).strip()

# ──────────────────────────────────────────────────────────────
# Core configuration
# ──────────────────────────────────────────────────────────────
DEFAULT_PHOTOS_PER_ITEM = getenv_int("DEFAULT_PHOTOS_PER_ITEM", 4)
DEFAULT_CONDITION = getenv_str("DEFAULT_CONDITION", "preowned").lower()
GALLERY_URL = getenv_str("CHATBAY_GALLERY_URL", "https://chatbay.site/wp-json/chatbay/v1/gallery")
ENFORCE_PARAMS = getenv_str("ENFORCE_PARAMS", "0") == "1"

DEFAULT_LOCATION = getenv_str("EBAY_LOCATION", "Middletown, CT, USA")
DEFAULT_SHIP_PROFILE = getenv_str("EBAY_SHIP_PROFILE", "7.99 FLAT")
DEFAULT_RET_PROFILE = getenv_str("EBAY_RET_PROFILE", "No returns accepted")
DEFAULT_PAY_PROFILE = getenv_str("EBAY_PAY_PROFILE", "eBay Payments")

CHUNK_SIZE = getenv_int("BATCH_LIMIT", 5)                   # groups per batch
SLEEP_BETWEEN_BATCHES = getenv_float("BATCH_SLEEP", 5.0)    # seconds between batches

# ──────────────────────────────────────────────────────────────
# Category mapping
# ──────────────────────────────────────────────────────────────
CATEGORY_MAP = {
    "t-shirt": "15687", "shirt": "15687", "sweatshirt": "155226", "hoodie": "155226",
    "jacket": "57988", "pants": "57989", "jeans": "11483", "shorts": "15690",
    "underwear": "11507", "lingerie": "11514", "hat": "163571", "cap": "163571",
    "beanie": "15662", "belt": "2993", "bag": "169291", "tote": "169291",
    "purse": "169291", "backpack": "182982", "wallet": "45258", "magazine": "280",
    "book": "261186", "comic": "63", "poster": "140", "patch": "156521",
    "sticker": "165326", "button": "10960", "pin": "11116", "tool": "631",
    "lamp": "112581", "clock": "37912", "decor": "10033",
    "glass": "50693", "ceramic": "50693", "plate": "870", "mug": "20625",
}

def match_category_id(text: str) -> str:
    if not text: return "15687"
    lower = text.lower()
    for key, cid in CATEGORY_MAP.items():
        if key in lower:
            return cid
    return "15687"

# ──────────────────────────────────────────────────────────────
# Condition helpers
# ──────────────────────────────────────────────────────────────
CONDITION_ID_MAP = {"new": 1000, "preowned": 3000, "parts": 7000}

def normalize_condition(value: str) -> str:
    if not value:
        return DEFAULT_CONDITION
    v = value.strip().lower()
    if v in {"new", "preowned", "parts"}: return v
    if v in {"used", "vintage", "worn"}: return "preowned"
    if v in {"nwt", "nos", "deadstock"}: return "new"
    if "part" in v or "repair" in v: return "parts"
    return DEFAULT_CONDITION

# ──────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────
def current_gmt_schedule() -> str:
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    est = now_utc - datetime.timedelta(hours=5)
    next_day_est = (est + datetime.timedelta(days=1)).replace(hour=22, minute=0, second=0, microsecond=0)
    next_day_utc = next_day_est + datetime.timedelta(hours=5)
    return next_day_utc.strftime("%Y-%m-%d %H:%M:%S")

def clean_price(raw: str, fallback="34.99") -> str:
    if not raw:
        return fallback
    s = str(raw).replace("$", "").split("-")[0].strip()
    return "".join(ch for ch in s if ch.isdigit() or ch == ".") or fallback

def limit_len(s: str, n: int) -> str:
    return (s or "")[:n]

def build_title(brand: str, title_raw: str, size: str, color: str) -> str:
    parts = []
    if brand: parts.append(brand)
    if title_raw: parts.append(title_raw)
    if size: parts.append(size)
    if color: parts.append(color)
    return limit_len(" ".join(parts).strip(), 79)

def get_gallery_url() -> str:
    return request.args.get("gallery", GALLERY_URL)

def require_params_or_400():
    if not ENFORCE_PARAMS: return None
    missing = []
    if "photos_per_item" not in request.args: missing.append("photos_per_item")
    if "condition" not in request.args: missing.append("condition")
    if missing:
        return jsonify({"error": "Missing required parameters", "missing": missing}), 400
    return None

# ──────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "chatbay-analyzer"}), 200

@app.route("/status")
def status():
    try:
        config = {
            "version": "v4.6",
            "default_photos_per_item": DEFAULT_PHOTOS_PER_ITEM,
            "default_condition": DEFAULT_CONDITION,
            "gallery_url": GALLERY_URL,
            "batch_limit_groups": CHUNK_SIZE,
            "batch_sleep_seconds": SLEEP_BETWEEN_BATCHES,
            "location": DEFAULT_LOCATION,
            "ship_profile": DEFAULT_SHIP_PROFILE,
            "return_profile": DEFAULT_RET_PROFILE,
            "payment_profile": DEFAULT_PAY_PROFILE,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
        return jsonify({"ok": True, "config": config}), 200
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500

@app.route("/analyze_gallery")
def analyze_gallery():
    try:
        gallery_url = get_gallery_url()
        headers = {"User-Agent": "ChatbayAnalyzer/4.6", "Accept": "application/json"}
        r = requests.get(gallery_url, headers=headers, timeout=30)
        if r.status_code != 200:
            return jsonify({"error": "Gallery fetch failed", "status": r.status_code, "url": gallery_url}), r.status_code

        gallery = r.json()
        groups = gallery.get("groups", [])
        if not groups:
            return jsonify({"error": "No groups found"}), 404

        results = []
        total = len(groups)
        for i in range(0, total, CHUNK_SIZE):
            batch = groups[i:i + CHUNK_SIZE]
            print(f"🧠 Processing batch {i//CHUNK_SIZE+1}/{(total-1)//CHUNK_SIZE+1} ({len(batch)} groups)...")
            for idx, g in enumerate(batch, 1):
                photos = [u.strip() for u in str(g.get("photo_urls", "")).split(",") if u.strip()]
                if not photos:
                    continue
                comp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": (
                                "Analyze these product photos and return concise JSON with keys: "
                                "title, category, description, price_estimate, brand, color, material, size, features, pattern."
                            )},
                            *[{"type": "image_url", "image_url": {"url": u, "detail": "high"}} for u in photos],
                        ]
                    }],
                    max_tokens=500,
                )
                raw = comp.choices[0].message.content.strip()
                try:
                    parsed = json.loads(raw)
                    if not isinstance(parsed, dict):
                        raise ValueError
                except Exception:
                    parsed = {"group": idx, "raw": raw}
                parsed["photos"] = photos
                results.append(parsed)

            if i + CHUNK_SIZE < total:
                print(f"⏸ Sleeping {SLEEP_BETWEEN_BATCHES} seconds to avoid rate limit...")
                time.sleep(SLEEP_BETWEEN_BATCHES)

        print(f"✅ Completed {len(results)} groups total.")
        return jsonify(results), 200
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500

# ──────────────────────────────────────────────────────────────
# CSV + Row builder
# ──────────────────────────────────────────────────────────────
def ebay_row_from_analysis(a: Dict[str, Any], idx: int, photos_per_item: int, condition: str) -> Dict[str, str]:
    title_raw = (a.get("title") or f"Item {idx}").strip()
    desc_raw = (a.get("description") or "Collectible item.").strip()
    category_text = a.get("category", "") or ""
    cat_id = match_category_id(category_text)
    price = clean_price(a.get("price_estimate", "34.99"))
    brand = (a.get("brand") or "").strip()
    color = (a.get("color") or "").strip()
    material = (a.get("material") or "").strip()
    size = (a.get("size") or "").strip()
    features = a.get("features", "")
    pattern = a.get("pattern", "")
    title = build_title(brand, title_raw, size, color)

    desc_html = f"""
<p><center><h4>{title}</h4></center></p>
<p>{desc_raw}</p>
<ul>
<li>Brand: {brand or "—"}</li>
<li>Category: {category_text or "—"}</li>
<li>Features: {features or "—"}</li>
<li>Size / Material: {size or "—"} / {material or "—"}</li>
</ul>
<p>Collector note: classic style with enduring appeal.</p>
""".strip()

    cond_id = CONDITION_ID_MAP.get(normalize_condition(condition), 3000)
    photos = a.get("photos") or []
    item_photo_url = ",".join(photos[:max(1, min(12, int(photos_per_item)))])

    return {
        "Action(SiteID=US|Country=US|Currency=USD|Version=1193)": "Add",
        "Custom label (SKU)": "",
        "Category ID": cat_id,
        "Category name": category_text or "Other",
        "Title": title,
        "Schedule Time": current_gmt_schedule(),
        "Start price": price,
        "Quantity": "1",
        "Item photo URL": item_photo_url,
        "Condition ID": str(cond_id),
        "Description": desc_html,
        "Format": "FixedPrice",
        "Duration": "GTC",
        "Buy It Now price": price,
        "Best Offer Enabled": "0",
        "Immediate pay required": "1",
        "Location": DEFAULT_LOCATION,
        "Shipping service 1 option": "USPSGroundAdvantage",
        "Shipping service 1 cost": "0",
        "Max dispatch time": "2",
        "Returns accepted option": "ReturnsAccepted",
        "Returns within option": "30 Days",
        "Refund option": "MoneyBack",
        "Return shipping cost paid by": "Buyer",
        "Shipping profile name": DEFAULT_SHIP_PROFILE,
        "Return profile name": DEFAULT_RET_PROFILE,
        "Payment profile name": DEFAULT_PAY_PROFILE,
        "C:Brand": brand,
        "C:Color": color,
        "C:Material": material,
        "C:Size": size,
        "C:Features": features if isinstance(features, str) else json.dumps(features),
        "C:Pattern": pattern,
        "C:Vintage": "No",
    }

FIELDNAMES = list(ebay_row_from_analysis({}, 1, 1, "preowned").keys())

def _analyze_then_rows(limit: Optional[int], photos_per_item: int, condition: str):
    r = requests.get(f"{request.host_url}analyze_gallery", params={"gallery": get_gallery_url()})
    if r.status_code != 200:
        raise RuntimeError(f"Analyzer failed: {r.status_code}")
    data = r.json()
    if isinstance(limit, int):
        data = data[:limit]
    return [ebay_row_from_analysis(a, i + 1, photos_per_item, condition) for i, a in enumerate(data)]

@app.route("/export_csv")
def export_csv():
    try:
        err = require_params_or_400()
        if err: return err
        photos_per_item = int(request.args.get("photos_per_item", DEFAULT_PHOTOS_PER_ITEM))
        condition = normalize_condition(request.args.get("condition", DEFAULT_CONDITION))
        rows = _analyze_then_rows(None, photos_per_item, condition)
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        for r_ in rows: w.writerow(r_)
        out.seek(0)
        fname = f"ebay-listings-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
        return send_file(io.BytesIO(out.getvalue().encode("utf-8")),
                         mimetype="text/csv", as_attachment=True, download_name=fname)
    except Exception:
        return jsonify({"error": "CSV export failed", "trace": traceback.format_exc()}), 500

@app.route("/preview_csv")
def preview_csv():
    try:
        err = require_params_or_400()
        if err: return err
        photos_per_item = int(request.args.get("photos_per_item", DEFAULT_PHOTOS_PER_ITEM))
        condition = normalize_condition(request.args.get("condition", DEFAULT_CONDITION))
        rows = _analyze_then_rows(2, photos_per_item, condition)
        return jsonify({
            "preview_count": len(rows),
            "photos_per_item": photos_per_item,
            "condition": condition,
            "rows": rows
        }), 200
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500

# ──────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"🚀 Chatbay Analyzer v4.6 running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
