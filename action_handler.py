# ──────────────────────────────────────────────────────────────
# Chatbay → GPT-4o Vision Analyzer + eBay CSV Exporter (v4.2 HYBRID)
# Flask app for Render.com deployment
# Hybrid mode:
#   - You (in chat) choose: photos_per_item & condition (new|preowned|parts)
#   - App reads them as query params on /export_csv & /preview_csv
#   - /preview_csv returns first 2 rows as JSON (proof before print)
# ──────────────────────────────────────────────────────────────

import os
import io
import csv
import json
import datetime
import traceback
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

# ──────────────────────────────────────────────────────────────
# CATEGORY MAP
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
    for key, cat_id in CATEGORY_MAP.items():
        if key in lower:
            return cat_id
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
def current_gmt_schedule():
    est_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=4)
    next_day = (est_time + datetime.timedelta(days=1)).replace(hour=22, minute=0, second=0, microsecond=0)
    return (next_day + datetime.timedelta(hours=4)).strftime("%Y-%m-%d %H:%M:%S")

def ebay_row_from_analysis(analysis, idx: int, photos_per_item: int, condition: str):
    title = analysis.get("title", f"Item {idx}")
    desc = analysis.get("description", "Vintage collectible item.")
    category_text = analysis.get("category", "")
    cat_id = match_category_id(category_text)
    price = analysis.get("price_estimate", "$34.99").replace("$", "").split("-")[0].strip() or "34.99"

    brand = analysis.get("brand", "")
    color = analysis.get("color", "")
    material = analysis.get("material", "")
    size = analysis.get("size", "")
    features = analysis.get("features", "")
    pattern = analysis.get("pattern", "")

    cond = normalize_condition(condition)
    condition_id = CONDITION_ID_MAP.get(cond, 1000)
    cond_note = CONDITION_NOTE.get(cond, "")

    photo_urls = analysis.get("photos") or []
    max_count = max(1, min(12, int(photos_per_item)))
    item_photo_url = ",".join(photo_urls[:max_count])

    return {
        "Action(SiteID=US|Country=US|Currency=USD|Version=1193)": "Add",
        "Custom label (SKU)": "",
        "Category ID": cat_id,
        "Category name": category_text or "Other",
        "Title": title[:79],
        "Relationship": "",
        "Relationship details": "",
        "Schedule Time": current_gmt_schedule(),
        "P:UPC": "",
        "P:EPID": "",
        "Start price": price,
        "Quantity": "1",
        "Item photo URL": item_photo_url,
        "VideoID": "",
        "Condition ID": condition_id,
        "Description": f"<h4>{title}</h4><p>{desc}</p><p><em>{cond_note}</em></p>",
        "Format": "FixedPrice",
        "Duration": "GTC",
        "Buy It Now price": price,
        "Best Offer Enabled": "0",
        "Immediate pay required": "1",
        "Location": "Middletown, CT, USA",
        "Shipping service 1 option": "USPSGroundAdvantage",
        "Shipping service 1 cost": "0",
        "Max dispatch time": "2",
        "Returns accepted option": "ReturnsAccepted",
        "Returns within option": "30 Days",
        "Refund option": "MoneyBack",
        "Return shipping cost paid by": "Buyer",
        "Shipping profile name": "7.99 FLAT",
        "Return profile name": "No returns accepted",
        "Payment profile name": "eBay Payments",
        "C:Brand": brand,
        "C:Color": color,
        "C:Material": material,
        "C:Size": size,
        "C:Pattern": pattern,
        "C:Vintage": "Yes",
    }

# ──────────────────────────────────────────────────────────────
# Core routes
# ──────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "chatbay-analyzer"}), 200

@app.route("/")
def home():
    return jsonify({"status": "ok", "message": "Chatbay Analyzer v4.2 – hybrid mode + preview ready"}), 200

@app.route("/analyze_gallery")
def analyze_gallery():
    """Analyze gallery and attach photo URLs."""
    try:
        headers = {"User-Agent": "ChatbayAnalyzer/4.2", "Accept": "application/json"}
        resp = requests.get(GALLERY_URL, headers=headers, timeout=25)
        if resp.status_code != 200:
            return jsonify({"error": "Gallery fetch failed", "status": resp.status_code}), resp.status_code

        gallery = resp.json()
        groups = gallery.get("groups", [])
        if not groups:
            return jsonify({"error": "No groups found"}), 404

        results = []
        for idx, group in enumerate(groups, start=1):
            photo_urls = [u.strip() for u in group.get("photo_urls", "").split(",") if u.strip()]
            if not photo_urls:
                continue

            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": (
                            "Analyze these product photos and describe what you see — "
                            "brand, style, color, material, and any visible text. "
                            "Return concise JSON with keys: "
                            "title, category, description, price_estimate, brand, color, material, size, features, pattern."
                        )},
                        *[{"type": "image_url", "image_url": {"url": u, "detail": "high"}} for u in photo_urls],
                    ]
                }],
                max_tokens=500,
            )
            raw = completion.choices[0].message.content.strip()
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = {"group": idx, "raw": raw}
            parsed["photos"] = photo_urls
            results.append(parsed)

        return jsonify(results), 200
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500

# ──────────────────────────────────────────────────────────────
# CSV and Preview Routes
# ──────────────────────────────────────────────────────────────
@app.route("/export_csv")
def export_csv():
    """Generate full CSV from latest analysis."""
    try:
        photos_per_item = int(request.args.get("photos_per_item", DEFAULT_PHOTOS_PER_ITEM))
        condition = normalize_condition(request.args.get("condition", DEFAULT_CONDITION))
        photos_per_item = max(1, min(12, photos_per_item))

        r = requests.get(f"{request.host_url}analyze_gallery")
        if r.status_code != 200:
            return jsonify({"error": "Analyzer failed"}), r.status_code

        analyses = r.json()
        sample_fields = ebay_row_from_analysis({}, 1, photos_per_item, condition)
        fieldnames = list(sample_fields.keys())

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for idx, analysis in enumerate(analyses, start=1):
            writer.writerow(ebay_row_from_analysis(analysis, idx, photos_per_item, condition))

        output.seek(0)
        filename = f"ebay-listings-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
        return send_file(io.BytesIO(output.getvalue().encode("utf-8")),
                         mimetype="text/csv",
                         as_attachment=True,
                         download_name=filename)
    except Exception:
        print(f"💥 CSV export failed:\n{traceback.format_exc()}")
        return jsonify({"error": "CSV export failed"}), 500

@app.route("/preview_csv")
def preview_csv():
    """Show first 2 listings as JSON preview."""
    try:
        photos_per_item = int(request.args.get("photos_per_item", DEFAULT_PHOTOS_PER_ITEM))
        condition = normalize_condition(request.args.get("condition", DEFAULT_CONDITION))
        photos_per_item = max(1, min(12, photos_per_item))

        r = requests.get(f"{request.host_url}analyze_gallery")
        if r.status_code != 200:
            return jsonify({"error": "Analyzer failed"}), r.status_code

        analyses = r.json()[:2]
        rows = [ebay_row_from_analysis(a, i + 1, photos_per_item, condition) for i, a in enumerate(analyses)]
        return jsonify({
            "preview_count": len(rows),
            "photos_per_item": photos_per_item,
            "condition": condition,
            "rows": rows
        }), 200
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500

# ──────────────────────────────────────────────────────────────
# Render entry point
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"🚀 Chatbay Analyzer v4.2 running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
