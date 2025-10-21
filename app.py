# ──────────────────────────────────────────────────────────────
# Chatbay Analyzer → Flask + OpenAI Vision (v5.5 full-header)
# Outputs FULL eBay CSV to the caller as an ATTACHMENT (ChatGPT)
# Render + Hostinger hybrid (gallery + analyzer)
# Routes:
#   /health, /openapi.json, /gallery, /preview_csv, /export_csv
# ──────────────────────────────────────────────────────────────

import os, io, csv, re, time, json, datetime, requests, urllib.parse
from flask import Flask, jsonify, send_file, request
from openai import OpenAI

# ──────────────────────────────────────────────────────────────
# Setup
app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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

EBAY_UPLOADS_URL        = getenv_str("EBAY_UPLOADS_URL", "https://chatbay.site/ebay-media")
EBAY_LOCATION           = getenv_str("EBAY_LOCATION", "Middletown, CT, USA")
EBAY_SHIP_PROFILE       = getenv_str("EBAY_SHIP_PROFILE", "ADV FREE 2 DAYS")
EBAY_RET_PROFILE        = getenv_str("EBAY_RET_PROFILE", "No returns accepted")
EBAY_PAY_PROFILE        = getenv_str("EBAY_PAY_PROFILE", "eBay Payments")

SLEEP_BETWEEN_ITEMS     = getenv_float("BATCH_SLEEP", 0.1)
MAX_RETRIES             = getenv_int("MAX_RETRIES", 4)

CONDITION_ID_MAP = {"new": 1000, "preowned": 3000, "parts": 7000}

# Minimal category hints; Vision text further refines
CATEGORY_MAP = {
    "panties": "11507", "underwear": "11507", "lingerie": "11514",
    "t-shirt": "15687", "tee": "15687", "shirt": "15687",
    "sweatshirt": "155226", "hoodie": "155226",
    "jacket": "57988", "jeans": "11483", "shorts": "15690", "pants": "57989",
    "bag": "169291", "tote": "169291", "patch": "156521", "button": "10960",
    "hat": "163571", "cap": "163571", "magazine": "280", "book": "261186",
}

# ──────────────────────────────────────────────────────────────
# CSV HEADER (exact order from user)
FULL_HEADER = [
    "Action(SiteID=US|Country=US|Currency=USD|Version=1193)",
    "Custom label (SKU)",
    "Category ID",
    "Category name",
    "Title",
    "Relationship",
    "Relationship details",
    "Schedule Time",
    "P:UPC",
    "P:EPID",
    "Start price",
    "Quantity",
    "Item photo URL",
    "VideoID",
    "Condition ID",
    "Description",
    "Format",
    "Duration",
    "Buy It Now price",
    "Best Offer Enabled",
    "Best Offer Auto Accept Price",
    "Minimum Best Offer Price",
    "Immediate pay required",
    "Location",
    "Shipping service 1 option",
    "Shipping service 1 cost",
    "Shipping service 1 priority",
    "Shipping service 2 option",
    "Shipping service 2 cost",
    "Shipping service 2 priority",
    "Max dispatch time",
    "Returns accepted option",
    "Returns within option",
    "Refund option",
    "Return shipping cost paid by",
    "Shipping profile name",
    "Return profile name",
    "Payment profile name",
    "ProductCompliancePolicyID",
    "Regional ProductCompliancePolicies",
    "C:Style",
    "C:Brand",
    "C:Size Type",
    "C:Color",
    "C:Department",
    "C:Size",
    "C:Type",
    "C:Features",
    "C:Character",
    "C:Theme",
    "C:Material",
    "C:Fabric Type",
    "C:Pattern",
    "C:Vintage",
    "C:Compression Area",
    "C:Number in Pack",
    "C:Band Size",
    "C:Cup Size",
    "C:Underwire Type",
    "C:Strap Type",
    "C:Support Level",
]

ALLOWED_EXTS = (".jpg", ".jpeg", ".png")

# ──────────────────────────────────────────────────────────────
# Helpers
def sanitize_photo_url(u: str) -> str:
    """Return https, percent-encoded path, only image extensions; else ''."""
    u = (u or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        u = "https:" + u
    if not u.startswith(("http://", "https://")):
        u = "https://" + u.lstrip("/")
    if u.startswith("http://"):
        u = "https://" + u[len("http://"):]
    parts = urllib.parse.urlsplit(u)
    path = urllib.parse.quote(parts.path, safe="/._-()")
    if not path.lower().endswith(ALLOWED_EXTS):
        return ""
    return urllib.parse.urlunsplit(("https", parts.netloc, path, "", ""))

def join_item_photos(urls, max_photos):
    """Return pipe-separated (|) https, encoded, deduped URLs (≤12)."""
    cleaned = []
    for u in urls:
        su = sanitize_photo_url(u)
        if su:
            cleaned.append(su)
    seen, out = set(), []
    limit = max(1, min(12, int(max_photos)))
    for cu in cleaned:
        if cu not in seen:
            seen.add(cu)
            out.append(cu)
            if len(out) >= limit:
                break
    return "|".join(out)

def fetch_gallery():
    try:
        r = requests.get(GALLERY_URL, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("groups", []) if isinstance(data, dict) else []
    except Exception as e:
        print("❌ fetch_gallery error:", e)
        return []

# ──────────────────────────────────────────────────────────────
# Vision & parsing
VISION_SYS_PROMPT = (
    "You are preparing data for an eBay listing. In one compact line, output: "
    "Title (<=79 chars) — then attributes as key:value pairs for brand, type, size, size_type, "
    "department, color, material, fabric_type, pattern, style, theme, character. "
    "Prefer words found on tags/labels. Keep it factual; no condition text."
)

KEY_PATTERNS = {
    "brand": r"(?:brand|maker|manufacturer)\s*[:\-]\s*([A-Za-z0-9 &.'\-]+)",
    "type": r"(?:type|style)\s*[:\-]\s*([A-Za-z0-9 &.'\-]+)",
    "size": r"(?:size)\s*[:\-]\s*([A-Za-z0-9 /\-]+)",
    "size_type": r"(?:size[_\s]?type)\s*[:\-]\s*([A-Za-z0-9 \-]+)",
    "department": r"(?:department)\s*[:\-]\s*([A-Za-z0-9 \-]+)",
    "color": r"(?:color)\s*[:\-]\s*([A-Za-z0-9 /&\-,]+)",
    "material": r"(?:material)\s*[:\-]\s*([A-Za-z0-9 /&\-,]+)",
    "fabric_type": r"(?:fabric[_\s]?type)\s*[:\-]\s*([A-Za-z0-9 /&\-,]+)",
    "pattern": r"(?:pattern)\s*[:\-]\s*([A-Za-z0-9 /&\-,]+)",
    "style": r"(?:style)\s*[:\-]\s*([A-Za-z0-9 /&\-,]+)",
    "theme": r"(?:theme)\s*[:\-]\s*([A-Za-z0-9 /&\-,]+)",
    "character": r"(?:character)\s*[:\-]\s*([A-Za-z0-9 /&\-,]+)",
}

def parse_attributes(text: str):
    attrs = {k: "" for k in KEY_PATTERNS.keys()}
    if not text:
        return "", attrs

    # Split "Title — key:value key:value ..."
    # Assume title before first " — " or treat first sentence as title
    parts = [p.strip() for p in re.split(r"\s+—\s+", text, maxsplit=1)]
    title = parts[0][:79]
    body = parts[1] if len(parts) > 1 else text

    for key, pat in KEY_PATTERNS.items():
        m = re.search(pat, body, flags=re.I)
        if m:
            attrs[key] = m.group(1).strip()

    # Light post-clean
    for k in attrs:
        attrs[k] = re.sub(r"\s{2,}", " ", attrs[k]).strip(" ,;|")

    return title, attrs

def analyze_with_vision(photo_urls, condition):
    """Return (title, attributes dict, desc_line) from first good image."""
    for url in photo_urls:
        su = sanitize_photo_url(url)
        if not su:
            continue
        try:
            img = requests.get(su, timeout=15)
            if img.status_code != 200 or not img.content:
                continue
            result = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{
                    "role": "system",
                    "content": VISION_SYS_PROMPT
                }, {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Condition={condition}. Extract facts from label/graphics."},
                        {"type": "image", "image_bytes": img.content}
                    ]
                }],
                max_tokens=180,
                temperature=0.2
            )
            line = (result.choices[0].message.content or "").strip()
            title, attrs = parse_attributes(line)
            if not title:
                # fallback tiny title
                title = "Listing"
            # description seed from the same line
            return title, attrs, line
        except Exception as e:
            print("❌ Vision error:", e)
            continue
    return "Listing", {k: "" for k in KEY_PATTERNS}, ""

def choose_category_id(title, attrs):
    t = " ".join([title, attrs.get("type",""), attrs.get("style","")]).lower()
    for key, cid in CATEGORY_MAP.items():
        if key in t:
            return cid
    # simple fallbacks by department/type
    if "t-shirt" in t or "tee" in t or "shirt" in t: return "15687"
    if "sweat" in t or "hoodie" in t: return "155226"
    if "hat" in t or "cap" in t: return "163571"
    if "bag" in t or "tote" in t: return "169291"
    return ""  # leave blank if unsure

def build_description_html(title, attrs, vision_line):
    bullets = [
        ("Brand", attrs.get("brand","")),
        ("Type/Style", attrs.get("type","") or attrs.get("style","")),
        ("Size Type", attrs.get("size_type","")),
        ("Size", attrs.get("size","")),
        ("Department", attrs.get("department","")),
        ("Color", attrs.get("color","")),
        ("Material", attrs.get("material","")),
        ("Fabric Type", attrs.get("fabric_type","")),
        ("Pattern", attrs.get("pattern","")),
        ("Theme", attrs.get("theme","")),
        ("Character", attrs.get("character","")),
    ]
    lis = "".join([f"<li>{k}: {v}</li>" for k,v in bullets if v])
    note = f"<p>{vision_line}</p>" if vision_line else ""
    return f"<p><center><h4>{title}</h4></center></p><ul>{lis}</ul>{note}"

# ──────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "chatbay-analyzer", "version": "v5.5", "source": "Render/Hostinger"})

@app.route("/openapi.json")
def serve_openapi():
    return send_file("openapi.json")

@app.route("/gallery")
def get_gallery():
    groups = fetch_gallery()
    return jsonify({"total_groups": len(groups), "groups": groups})

# ──────────────────────────────────────────────────────────────
@app.route("/preview_csv")
def preview_csv():
    """Preview first 2 analyzed rows as JSON (not CSV)."""
    condition = request.args.get("condition", DEFAULT_CONDITION).lower()
    ppi = int(request.args.get("photos_per_item", DEFAULT_PHOTOS_PER_ITEM))
    groups = fetch_gallery()
    if not groups:
        return jsonify({"error":"No gallery data found"}), 500

    rows = []
    for i, group in enumerate(groups[:2]):
        photos = [p.strip() for p in group.get("photo_urls","").split(",") if p.strip()]
        photo_field = join_item_photos(photos, ppi)
        title, attrs, line = analyze_with_vision(photos[:1], condition)
        title = title[:79]
        cat_id = choose_category_id(title, attrs)
        rows.append({
            "Title": title,
            "Category ID": cat_id,
            "Condition ID": CONDITION_ID_MAP.get(condition, 3000),
            "Item photo URL": photo_field,
            "C:Brand": attrs.get("brand",""),
            "C:Color": attrs.get("color",""),
            "C:Size": attrs.get("size",""),
            "C:Material": attrs.get("material",""),
            "C:Department": attrs.get("department",""),
        })
    return jsonify({"preview_count": len(rows), "condition": condition, "photos_per_item": ppi, "rows": rows})

# ──────────────────────────────────────────────────────────────
@app.route("/export_csv")
def export_csv():
    """
    Generate FULL-HEADER CSV and RETURN AS ATTACHMENT to the caller (ChatGPT).
    """
    condition = request.args.get("condition", DEFAULT_CONDITION).lower()
    ppi = int(request.args.get("photos_per_item", DEFAULT_PHOTOS_PER_ITEM))
    groups = fetch_gallery()
    if not groups:
        return jsonify({"error":"No gallery data found"}), 500

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(FULL_HEADER)

    for idx, group in enumerate(groups):
        photos = [p.strip() for p in group.get("photo_urls","").split(",") if p.strip()]
        photo_field = join_item_photos(photos, ppi)

        title, attrs, vision_line = analyze_with_vision(photos[:1], condition)
        title = title[:79]
        cat_id = choose_category_id(title, attrs)
        desc_html = build_description_html(title, attrs, vision_line)

        # Core defaults
        action = "Add"
        start_price = "34.99"
        quantity = "1"
        format_ = "FixedPrice"
        duration = "GTC"
        cond_id = str(CONDITION_ID_MAP.get(condition, 3000))

        # NOTE: We leave Schedule Time blank to avoid bad timezone offsets.
        # Profiles & location from env:
        ship_prof = EBAY_SHIP_PROFILE
        ret_prof  = EBAY_RET_PROFILE
        pay_prof  = EBAY_PAY_PROFILE

        row_map = {
            "Action(SiteID=US|Country=US|Currency=USD|Version=1193)": action,
            "Custom label (SKU)": "",
            "Category ID": cat_id,
            "Category name": "",
            "Title": title,
            "Relationship": "",
            "Relationship details": "",
            "Schedule Time": "",
            "P:UPC": "",
            "P:EPID": "",
            "Start price": start_price,
            "Quantity": quantity,
            "Item photo URL": photo_field,
            "VideoID": "",
            "Condition ID": cond_id,
            "Description": desc_html,
            "Format": format_,
            "Duration": duration,
            "Buy It Now price": "",
            "Best Offer Enabled": "",
            "Best Offer Auto Accept Price": "",
            "Minimum Best Offer Price": "",
            "Immediate pay required": "",
            "Location": EBAY_LOCATION,
            "Shipping service 1 option": "",
            "Shipping service 1 cost": "",
            "Shipping service 1 priority": "",
            "Shipping service 2 option": "",
            "Shipping service 2 cost": "",
            "Shipping service 2 priority": "",
            "Max dispatch time": "",
            "Returns accepted option": "",
            "Returns within option": "",
            "Refund option": "",
            "Return shipping cost paid by": "",
            "Shipping profile name": ship_prof,
            "Return profile name": ret_prof,
            "Payment profile name": pay_prof,
            "ProductCompliancePolicyID": "",
            "Regional ProductCompliancePolicies": "",
            "C:Style": attrs.get("style",""),
            "C:Brand": attrs.get("brand",""),
            "C:Size Type": attrs.get("size_type",""),
            "C:Color": attrs.get("color",""),
            "C:Department": attrs.get("department",""),
            "C:Size": attrs.get("size",""),
            "C:Type": attrs.get("type",""),
            "C:Features": "",
            "C:Character": attrs.get("character",""),
            "C:Theme": attrs.get("theme",""),
            "C:Material": attrs.get("material",""),
            "C:Fabric Type": attrs.get("fabric_type",""),
            "C:Pattern": attrs.get("pattern",""),
            "C:Vintage": "",
            "C:Compression Area": "",
            "C:Number in Pack": "",
            "C:Band Size": "",
            "C:Cup Size": "",
            "C:Underwire Type": "",
            "C:Strap Type": "",
            "C:Support Level": "",
        }

        # Emit row in exact header order
        w.writerow([row_map.get(col, "") for col in FULL_HEADER])
        time.sleep(SLEEP_BETWEEN_ITEMS)

    csv_data = out.getvalue()
    out.close()
    fname = f"chatbay-ebay-export-full-{datetime.datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.csv"

    # Return as downloadable attachment (ChatGPT will show a file to download)
    return csv_data, 200, {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": f"attachment; filename={fname}"
    }

# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"🚀 Chatbay Analyzer v5.5 running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
