# ──────────────────────────────────────────────────────────────
# Chatbay → GPT-4o Vision Analyzer + eBay CSV Exporter (v5.0-S)
# Flask app for Render.com deployment
# Sequential, stable; full template sync from CSV header
# ──────────────────────────────────────────────────────────────
import os, io, csv, json, time, datetime, traceback, requests
from typing import Dict, Any, List, Optional
from flask import Flask, jsonify, send_file, request
from openai import OpenAI

app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ── Safe env readers ─────────────────────────────────────────
def getenv_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)).strip())
    except Exception:
        return default

def getenv_int(key: str, default: int) -> int:
    try:
        return int(float(os.getenv(key, str(default)).strip()))
    except Exception:
        return default

def getenv_str(key: str, default: str) -> str:
    return str(os.getenv(key, default)).strip()

# ── Core config ───────────────────────────────────────────────
DEFAULT_PHOTOS_PER_ITEM = getenv_int("DEFAULT_PHOTOS_PER_ITEM", 4)
DEFAULT_CONDITION       = getenv_str("DEFAULT_CONDITION", "preowned").lower()
GALLERY_URL             = getenv_str("CHATBAY_GALLERY_URL", "https://chatbay.site/wp-json/chatbay/v1/gallery")
EBAY_TEMPLATE_CSV       = getenv_str("EBAY_TEMPLATE_CSV", "eBay-category-listing-template-panties.csv")

DEFAULT_LOCATION        = getenv_str("EBAY_LOCATION", "Middletown, CT, USA")
DEFAULT_SHIP_PROFILE    = getenv_str("EBAY_SHIP_PROFILE", "7.99 FLAT")
DEFAULT_RET_PROFILE     = getenv_str("EBAY_RET_PROFILE", "No returns accepted")
DEFAULT_PAY_PROFILE     = getenv_str("EBAY_PAY_PROFILE", "eBay Payments")

SLEEP_BETWEEN_ITEMS     = getenv_float("BATCH_SLEEP", 6.0)
MAX_RETRIES             = getenv_int("MAX_RETRIES", 5)

# ── eBay helpers ─────────────────────────────────────────────
CATEGORY_MAP = {
    "t-shirt": "15687", "shirt": "15687", "sweatshirt": "155226", "hoodie": "155226",
    "jacket": "57988", "pants": "57989", "jeans": "11483", "shorts": "15690",
    "underwear": "11507", "lingerie": "11514", "hat": "163571", "cap": "163571",
    "bag": "169291", "tote": "169291", "patch": "156521", "sticker": "165326",
    "button": "10960", "magazine": "280", "book": "261186", "poster": "140",
    "tool": "631", "lamp": "112581", "decor": "10033", "wallet": "45258",
}
CONDITION_ID_MAP = {"new": 1000, "preowned": 3000, "parts": 7000}

def match_category_id(text: str) -> str:
    if not text: return "15687"
    lower = text.lower()
    for k, cid in CATEGORY_MAP.items():
        if k in lower: return cid
    return "15687"

def normalize_condition(v: str) -> str:
    if not v: return DEFAULT_CONDITION
    v = v.strip().lower()
    if v in {"new", "preowned", "parts"}: return v
    if v in {"used", "vintage", "worn"}:  return "preowned"
    if v in {"nwt", "nos", "deadstock"}:  return "new"
    if "part" in v:                      return "parts"
    return DEFAULT_CONDITION

def current_gmt_schedule() -> str:
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    est = now_utc - datetime.timedelta(hours=5)
    next_day_est = (est + datetime.timedelta(days=1)).replace(hour=22, minute=0, second=0, microsecond=0)
    next_day_utc = next_day_est + datetime.timedelta(hours=5)
    return next_day_utc.strftime("%Y-%m-%d %H:%M:%S")

def clean_price(raw: str, fallback="34.99") -> str:
    if not raw: return fallback
    s = str(raw).replace("$", "").split("-")[0].strip()
    s = "".join(ch for ch in s if ch.isdigit() or ch == ".")
    return s or fallback

def limit_len(s: str, n: int) -> str:
    return (s or "")[:n]

def build_title(brand, title_raw, size, color) -> str:
    parts = [p for p in [brand, title_raw, size, color] if p]
    return limit_len(" ".join(parts).strip(), 79)

def get_gallery_url() -> str:
    return request.args.get("gallery", GALLERY_URL)

# ── Template header loader ───────────────────────────────────
_FIELDNAMES_CACHE: Optional[List[str]] = None

def load_fieldnames() -> List[str]:
    global _FIELDNAMES_CACHE
    if _FIELDNAMES_CACHE: return _FIELDNAMES_CACHE
    try:
        # Read only the header row from the template CSV
        with open(EBAY_TEMPLATE_CSV, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            headers = next(reader)
            _FIELDNAMES_CACHE = [h.strip() for h in headers]
            return _FIELDNAMES_CACHE
    except Exception as e:
        # Fallback minimal superset (kept small; all blanks allowed)
        _FIELDNAMES_CACHE = [
            "Action(SiteID=US|Country=US|Currency=USD|Version=1193)",
            "Custom label (SKU)",
            "Category ID","Category name","Title",
            "Schedule Time","P:UPC","P:EPID",
            "Start price","Quantity","Item photo URL","VideoID",
            "Condition ID","Description","Format","Duration",
            "Buy It Now price","Best Offer Enabled","Immediate pay required",
            "Location",
            "Shipping profile name","Return profile name","Payment profile name",
            "Shipping service 1 option","Shipping service 1 cost",
            "Max dispatch time",
            "Returns accepted option","Returns within option","Refund option","Return shipping cost paid by",
            # Item specifics (common)
            "C:Style","C:Brand","C:Color","C:Size","C:Material","C:Pattern","C:Features","C:Vintage",
        ]
        print(f"⚠️ Could not load template headers from {EBAY_TEMPLATE_CSV}: {e}")
        return _FIELDNAMES_CACHE

# ── GPT call with sequential retry/backoff ───────────────────
def analyze_photos_with_gpt(photo_urls: List[str]) -> Dict[str, Any]:
    payload = {
        "model": "gpt-4o-mini",
        "max_tokens": 500,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text":
                    "Analyze these product photos and return JSON with: "
                    "title, category, description, price_estimate, brand, color, material, size, features, pattern."
                },
                *[{"type": "image_url", "image_url": {"url": u, "detail": "high"}} for u in photo_urls],
            ]
        }]
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.chat.completions.create(**payload)
            raw = resp.choices[0].message.content.strip()
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise ValueError
            except Exception:
                parsed = {"raw": raw}
            parsed["photos"] = photo_urls
            return parsed
        except Exception as e:
            msg = str(e)
            if ("429" in msg) or ("Rate limit" in msg) or ("tokens per min" in msg) or ("requests per min" in msg):
                wait = max(SLEEP_BETWEEN_ITEMS, 3.0) * attempt
                print(f"⏳ Rate limit — retry {attempt}/{MAX_RETRIES} after {wait:.1f}s")
                time.sleep(wait)
                continue
            if ("502" in msg) or ("500" in msg) or ("timeout" in msg.lower()):
                wait = 2.0 * attempt
                print(f"⚠️ 5xx/timeout — retry {attempt}/{MAX_RETRIES} after {wait:.1f}s")
                time.sleep(wait)
                continue
            print(f"❌ GPT error, no retry: {msg}")
            return {"error": msg, "photos": photo_urls}
    return {"error": "Failed after retries", "photos": photo_urls}

# ── Row builder using dynamic headers (unused left blank) ─────
def ebay_row_from_analysis(a: Dict[str, Any], idx: int, photos_per_item: int, condition: str) -> Dict[str, str]:
    headers = load_fieldnames()
    row = {h: "" for h in headers}

    title_raw = (a.get("title") or f"Item {idx}").strip()
    desc_raw  = (a.get("description") or "Collectible item.").strip()
    category_text = a.get("category", "")
    cat_id = match_category_id(category_text)
    price  = clean_price(a.get("price_estimate", "34.99"))

    brand   = (a.get("brand") or "").strip()
    color   = (a.get("color") or "").strip()
    material= (a.get("material") or "").strip()
    size    = (a.get("size") or "").strip()
    features= a.get("features", "")
    pattern = (a.get("pattern") or "").strip()

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

    # Set only if column exists in the template
    def setcol(col: str, val: str):
        if col in row: row[col] = str(val)

    setcol("Action(SiteID=US|Country=US|Currency=USD|Version=1193)", "Add")
    setcol("Custom label (SKU)", "")
    setcol("Category ID", cat_id)
    setcol("Category name", category_text or "Other")
    setcol("Title", title)
    setcol("Schedule Time", current_gmt_schedule())

    setcol("P:UPC", "")
    setcol("P:EPID", "")

    setcol("Start price", price)
    setcol("Quantity", "1")
    setcol("Item photo URL", item_photo_url)
    setcol("VideoID", "")

    setcol("Condition ID", str(cond_id))
    setcol("Description", desc_html)

    setcol("Format", "FixedPrice")
    setcol("Duration", "GTC")
    setcol("Buy It Now price", price)
    setcol("Best Offer Enabled", "")
    setcol("Immediate pay required", "1")

    setcol("Location", DEFAULT_LOCATION)
    setcol("Shipping service 1 option", "USPSGroundAdvantage")
    setcol("Shipping service 1 cost", "0")
    setcol("Max dispatch time", "2")

    setcol("Returns accepted option", "ReturnsAccepted")
    setcol("Returns within option", "30 Days")
    setcol("Refund option", "MoneyBack")
    setcol("Return shipping cost paid by", "Buyer")

    setcol("Shipping profile name", DEFAULT_SHIP_PROFILE)
    setcol("Return profile name", DEFAULT_RET_PROFILE)
    setcol("Payment profile name", DEFAULT_PAY_PROFILE)

    # Common item specifics (only set if present in template)
    setcol("C:Style", title_raw)
    setcol("C:Brand", brand)
    setcol("C:Color", color)
    setcol("C:Size", size)
    setcol("C:Material", material)
    setcol("C:Pattern", pattern)
    setcol("C:Features", features if isinstance(features, str) else json.dumps(features))
    setcol("C:Vintage", "No")

    return row

# ── Internal helper to analyze then build rows ────────────────
def _analyze_then_rows(limit: Optional[int], photos_per_item: int, condition: str):
    r = requests.get(f"{request.host_url}analyze_gallery", params={"gallery": get_gallery_url()})
    if r.status_code != 200:
        raise RuntimeError(f"Analyzer failed: {r.status_code}")
    data = r.json()
    if isinstance(limit, int):
        data = data[:limit]
    rows = [ebay_row_from_analysis(a, i + 1, photos_per_item, condition) for i, a in enumerate(data)]
    return rows

# ── Routes ───────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "chatbay-analyzer"}), 200

@app.route("/status")
def status():
    headers = load_fieldnames()
    return jsonify({
        "version": "v5.0-S",
        "sleep_between_items": SLEEP_BETWEEN_ITEMS,
        "max_retries": MAX_RETRIES,
        "gallery_url": GALLERY_URL,
        "template": EBAY_TEMPLATE_CSV,
        "header_count": len(headers),
        "first_10_headers": headers[:10]
    }), 200

@app.route("/analyze_gallery")
def analyze_gallery():
    try:
        gallery_url = get_gallery_url()
        r = requests.get(gallery_url, timeout=30)
        if r.status_code != 200:
            return jsonify({"error": "Gallery fetch failed", "status": r.status_code}), r.status_code
        payload = r.json() or {}
        groups = payload.get("groups", [])
        if not groups:
            return jsonify({"error": "No groups found"}), 404

        results = []
        for i, g in enumerate(groups, start=1):
            photos = [u.strip() for u in str(g.get("photo_urls", "")).split(",") if u.strip()]
            if not photos:
                continue
            print(f"🧠 Analyzing group {i}/{len(groups)}")
            parsed = analyze_photos_with_gpt(photos)
            results.append(parsed)
            time.sleep(SLEEP_BETWEEN_ITEMS)

        print(f"🏁 Completed {len(results)} groups.")
        return jsonify(results), 200
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500

@app.route("/preview_csv")
def preview_csv():
    try:
        photos_per_item = int(request.args.get("photos_per_item", DEFAULT_PHOTOS_PER_ITEM))
        condition = normalize_condition(request.args.get("condition", DEFAULT_CONDITION))
        rows = _analyze_then_rows(2, photos_per_item, condition)
        return jsonify({"preview_count": len(rows), "rows": rows}), 200
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500

@app.route("/export_csv")
def export_csv():
    try:
        photos_per_item = int(request.args.get("photos_per_item", DEFAULT_PHOTOS_PER_ITEM))
        condition = normalize_condition(request.args.get("condition", DEFAULT_CONDITION))
        rows = _analyze_then_rows(None, photos_per_item, condition)

        headers = load_fieldnames()
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            # Ensure all template columns exist; leave blanks intact
            for h in headers:
                if h not in row:
                    row[h] = ""
            w.writerow(row)

        out.seek(0)
        fname = f"ebay-listings-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
        return send_file(io.BytesIO(out.getvalue().encode()),
                         mimetype="text/csv", as_attachment=True, download_name=fname)
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500

# ── Entrypoint ────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"🚀 Chatbay Analyzer v5.0-S running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
