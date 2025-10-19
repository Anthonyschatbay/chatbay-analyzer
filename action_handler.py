# ──────────────────────────────────────────────────────────────
# Chatbay → GPT-4o Vision Analyzer + eBay CSV Exporter (v4.3a HYBRID)
# Flask app for Render.com deployment
# Hybrid mode:
#   • You (in chat) choose: photos_per_item & condition (new|preowned|parts)
#   • App reads them as query params on /export_csv & /preview_csv
#   • /preview_csv returns first 2 rows as JSON (proof before print)
# Upgrades in v4.3a:
#   • Descriptive SEO titles (no “Vintage Collectible”)
#   • Item specifics mapped (C:Brand / Size / Material / Color / etc.)
#   • Condition excluded from HTML description (policy compliant)
#   • ?gallery= override supported on all routes
#   • Optional strict param enforcement (ENFORCE_PARAMS = 1)
# ──────────────────────────────────────────────────────────────

import os, io, csv, json, datetime, traceback
from typing import List, Dict, Any, Optional
import requests
from flask import Flask, jsonify, send_file, request
from openai import OpenAI

# ──────────────────────────────────────────────────────────────
# App + OpenAI setup
# ──────────────────────────────────────────────────────────────
app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

DEFAULT_PHOTOS_PER_ITEM = int(os.getenv("DEFAULT_PHOTOS_PER_ITEM", 4))
DEFAULT_CONDITION = os.getenv("DEFAULT_CONDITION", "preowned").lower()
GALLERY_URL = os.getenv("CHATBAY_GALLERY_URL", "https://chatbay.site/wp-json/chatbay/v1/gallery")
ENFORCE_PARAMS = os.getenv("ENFORCE_PARAMS", "0") == "1"

DEFAULT_LOCATION = os.getenv("EBAY_LOCATION", "Middletown, CT, USA")
DEFAULT_SHIP_PROFILE = os.getenv("EBAY_SHIP_PROFILE", "7.99 FLAT")
DEFAULT_RET_PROFILE = os.getenv("EBAY_RET_PROFILE", "No returns accepted")
DEFAULT_PAY_PROFILE = os.getenv("EBAY_PAY_PROFILE", "eBay Payments")

# ──────────────────────────────────────────────────────────────
# Category map
# ──────────────────────────────────────────────────────────────
CATEGORY_MAP = {
    "t-shirt": "15687", "shirt": "15687", "sweatshirt": "155226", "hoodie": "155226",
    "jacket": "57988", "pants": "57989", "jeans": "11483", "shorts": "15690",
    "underwear": "11507", "lingerie": "11514", "hat": "163571", "cap": "163571",
    "beanie": "15662", "belt": "2993", "bag": "169291", "tote": "169291",
    "purse": "169291", "backpack": "182982", "wallet": "45258", "magazine": "280",
    "book": "261186", "comic": "63", "poster": "140", "patch": "156521",
    "sticker": "165326", "button": "10960", "pin": "11116", "tool": "631",
    "vintage tool": "631", "lamp": "112581", "clock": "37912", "decor": "10033",
    "glass": "50693", "ceramic": "50693", "plate": "870", "mug": "20625",
}

def match_category_id(category_text: str) -> str:
    if not category_text:
        return "15687"
    lower = category_text.lower()
    for key, cid in CATEGORY_MAP.items():
        if key in lower:
            return cid
    return "15687"

# ──────────────────────────────────────────────────────────────
# Condition helpers
# ──────────────────────────────────────────────────────────────
CONDITION_ID_MAP = {"new": 1000, "preowned": 3000, "parts": 7000}
CONDITION_NOTE = {
    "new": "New old stock / unworn vintage.",
    "preowned": "Excellent pre-owned vintage condition.",
    "parts": "For parts or repair, sold as-is.",
}

def normalize_condition(value: str) -> str:
    if not value:
        return DEFAULT_CONDITION
    v = value.strip().lower()
    if v in {"new", "preowned", "parts"}:
        return v
    if v in {"used", "worn", "vintage"}:
        return "preowned"
    if v in {"nos", "deadstock", "nwt"}:
        return "new"
    if "part" in v or "repair" in v:
        return "parts"
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
    parts: List[str] = []
    if brand: parts.append(brand)
    if title_raw: parts.append(title_raw)
    if size: parts.append(size)
    if color: parts.append(color)
    return limit_len(" ".join(parts).strip(), 79)

def get_gallery_url_from_request() -> str:
    return request.args.get("gallery", GALLERY_URL)

def require_params_or_400():
    if not ENFORCE_PARAMS:
        return None
    missing = []
    if "photos_per_item" not in request.args:
        missing.append("photos_per_item")
    if "condition" not in request.args:
        missing.append("condition")
    if missing:
        return jsonify({
            "error": "Missing required parameters",
            "message": "Need photos_per_item (1–12) and condition (new|preowned|parts).",
            "missing": missing
        }), 400
    return None

# ──────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "chatbay-analyzer"}), 200

@app.route("/")
def home():
    return jsonify({"status": "ok", "message": "Chatbay Analyzer v4.3a – hybrid + preview ready"}), 200

@app.route("/analyze_gallery")
def analyze_gallery():
    try:
        gallery_url = get_gallery_url_from_request()
        headers = {"User-Agent": "ChatbayAnalyzer/4.3a", "Accept": "application/json"}
        r = requests.get(gallery_url, headers=headers, timeout=30)
        if r.status_code != 200:
            return jsonify({"error": "Gallery fetch failed", "status": r.status_code, "url": gallery_url}), r.status_code
        gallery = r.json()
        groups = gallery.get("groups", [])
        if not groups:
            return jsonify({"error": "No groups found", "url": gallery_url}), 404

        results = []
        for idx, g in enumerate(groups, 1):
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
        return jsonify(results), 200
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500

# ──────────────────────────────────────────────────────────────
# Row builder
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

    cond = normalize_condition(condition)
    cond_id = CONDITION_ID_MAP.get(cond, 3000)
    photos = a.get("photos") or []
    item_photo_url = ",".join(photos[:max(1, min(12, int(photos_per_item)))])

    return {
        "Action(SiteID=US|Country=US|Currency=USD|Version=1193)": "Add",
        "Custom label (SKU)": "",
        "Category ID": cat_id,
        "Category name": category_text or "Other",
        "Title": title,
        "Relationship": "",
        "Relationship details": "",
        "Schedule Time": current_gmt_schedule(),
        "P:UPC": "",
        "P:EPID": "",
        "Start price": price,
        "Quantity": "1",
        "Item photo URL": item_photo_url,
        "VideoID": "",
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
        # Item specifics
        "C:Style": title_raw,
        "C:Brand": brand,
        "C:Size Type": "",
        "C:Color": color,
        "C:Department": "",
        "C:Size": size,
        "C:Type": "",
        "C:Features": features if isinstance(features, str) else json.dumps(features),
        "C:Character": "",
        "C:Theme": "",
        "C:Material": material,
        "C:Fabric Type": "",
        "C:Pattern": pattern,
        "C:Vintage": "No",
        "C:Band Size": "",
        "C:Cup Size": "",
        "C:Underwire Type": "",
        "C:Strap Type": "",
        "C:Support Level": "",
    }

# ──────────────────────────────────────────────────────────────
# CSV + Preview
# ──────────────────────────────────────────────────────────────
FIELDNAMES = list(ebay_row_from_analysis({}, 1, 1, "preowned").keys())

def _analyze_then_rows(limit: Optional[int], photos_per_item: int, condition: str):
    r = requests.get(f"{request.host_url}analyze_gallery", params={"gallery": get_gallery_url_from_request()})
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
                         mimetype="text/csv",
                         as_attachment=True,
                         download_name=fname)
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
    print(f"🚀 Chatbay Analyzer v4.3a running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
