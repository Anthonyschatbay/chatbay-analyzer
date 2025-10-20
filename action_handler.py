# ──────────────────────────────────────────────────────────────
# Chatbay → GPT-4o Vision Analyzer + eBay CSV Exporter (v5.1-T2)
# Flask app for Render.com deployment
# Category-based template auto-loader (from /app/templates/)
# Full header sync; blanks preserved; Start price only; photo sanitization
# ──────────────────────────────────────────────────────────────
import os, io, csv, json, time, datetime, traceback, requests, urllib.parse
from typing import Dict, Any, List, Optional, Tuple
from flask import Flask, jsonify, send_file, request
from openai import OpenAI

app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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

# Core config
DEFAULT_PHOTOS_PER_ITEM = getenv_int("DEFAULT_PHOTOS_PER_ITEM", 4)
DEFAULT_CONDITION       = getenv_str("DEFAULT_CONDITION", "preowned").lower()
GALLERY_URL             = getenv_str("CHATBAY_GALLERY_URL", "https://chatbay.site/wp-json/chatbay/v1/gallery")

DEFAULT_LOCATION        = getenv_str("EBAY_LOCATION", "Middletown, CT, USA")
DEFAULT_SHIP_PROFILE    = getenv_str("EBAY_SHIP_PROFILE", "7.99 FLAT")
DEFAULT_RET_PROFILE     = getenv_str("EBAY_RET_PROFILE", "No returns accepted")
DEFAULT_PAY_PROFILE     = getenv_str("EBAY_PAY_PROFILE", "eBay Payments")

SLEEP_BETWEEN_ITEMS     = getenv_float("BATCH_SLEEP", 6.0)
MAX_RETRIES             = getenv_int("MAX_RETRIES", 5)

CATEGORY_MAP = {
    "panties": "11507", "underwear": "11507", "lingerie": "11514",
    "t-shirt": "15687", "shirt": "15687",
    "sweatshirt": "155226", "hoodie": "155226",
    "jacket": "57988", "jeans": "11483", "shorts": "15690", "pants": "57989",
    "bag": "169291", "tote": "169291", "patch": "156521", "button": "10960",
    "hat": "163571", "cap": "163571", "magazine": "280", "book": "261186",
}
CONDITION_ID_MAP = {"new": 1000, "preowned": 3000, "parts": 7000}

def match_category_id(text: str) -> str:
    if not text:
        return "15687"
    lower = text.lower()
    for k, cid in CATEGORY_MAP.items():
        if k in lower:
            return cid
    return "15687"

def normalize_condition(v: str) -> str:
    if not v:
        return DEFAULT_CONDITION
    v = v.strip().lower()
    if v in {"new", "preowned", "parts"}:
        return v
    if v in {"used", "vintage", "worn"}:
        return "preowned"
    if v in {"nwt", "nos", "deadstock"}:
        return "new"
    if "part" in v:
        return "parts"
    return DEFAULT_CONDITION

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
    s = "".join(ch for ch in s if ch.isdigit() or ch == ".")
    return s or fallback

def limit_len(s: str, n: int) -> str:
    return (s or "")[:n]

def build_title(brand, title_raw, size, color) -> str:
    parts = [p for p in [brand, title_raw, size, color] if p]
    return limit_len(" ".join(parts).strip(), 79)

# --- new helper: photo URL sanitization
def sanitize_photo_url(u: str) -> str:
    u = u.strip()
    if not u:
        return u
    if u.startswith("http://"):
        u = "https://" + u[len("http://"):]
    parts = urllib.parse.urlsplit(u)
    path  = urllib.parse.quote(parts.path, safe="/._-")
    return urllib.parse.urlunsplit(("https", parts.netloc, path, "", ""))

def join_item_photos(urls: List[str], max_photos: int) -> str:
    cleaned = []
    for u in urls:
        cu = sanitize_photo_url(u)
        if cu.lower().endswith((".jpg", ".jpeg", ".png")):
            cleaned.append(cu)
    seen = set()
    out = []
    for cu in cleaned:
        if cu not in seen:
            seen.add(cu)
            out.append(cu)
        if len(out) >= max(1, min(12, int(max_photos))):
            break
    return ",".join(out)

# Template loader and caching
_HEADER_CACHE: Dict[str, List[str]] = {}

def template_for_category(cat_text: str) -> str:
    lower = cat_text.lower() if cat_text else ""
    for key in CATEGORY_MAP.keys():
        if key in lower:
            return f"/app/templates/eBay-category-listing-template-{key}.csv"
    return "/app/templates/eBay-category-listing-template-panties.csv"

def load_fieldnames(template_path: str) -> List[str]:
    if template_path in _HEADER_CACHE:
        return _HEADER_CACHE[template_path]
    try:
        with open(template_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            headers = next(reader)
            _HEADER_CACHE[template_path] = [h.strip() for h in headers]
            return _HEADER_CACHE[template_path]
    except Exception:
        _HEADER_CACHE[template_path] = [
            "Action(SiteID=US|Country=US|Currency=USD|Version=1193)",
            "Custom label (SKU)", "Category ID", "Category name", "Title",
            "Start price", "Quantity", "Item photo URL", "Condition ID",
            "Description", "Format", "Duration",
            "Shipping profile name", "Return profile name", "Payment profile name"
        ]
        return _HEADER_CACHE[template_path]

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
            parsed = json.loads(raw) if raw.startswith("{") else {"raw": raw}
            parsed["photos"] = photo_urls
            return parsed
        except Exception as e:
            msg = str(e)
            if "429" in msg or "Rate limit" in msg:
                wait = SLEEP_BETWEEN_ITEMS * attempt
                time.sleep(wait)
                continue
            return {"error": msg, "photos": photo_urls}
    return {"error": "Failed after retries", "photos": photo_urls}

def build_row_from_analysis(a: Dict[str, Any], idx: int, photos_per_item: int, condition: str) -> Tuple[Dict[str, str], str]:
    category_text = a.get("category", "other")
    template_path = template_for_category(category_text)
    headers = load_fieldnames(template_path)
    row = {h: "" for h in headers}

    title_raw = (a.get("title") or f"Item {idx}").strip()
    desc_raw  = (a.get("description") or "Collectible item.").strip()
    cat_id     = match_category_id(category_text)
    price      = clean_price(a.get("price_estimate", "34.99"))

    brand    = (a.get("brand") or "").strip()
    color    = (a.get("color") or "").strip()
    material = (a.get("material") or "").strip()
    size     = (a.get("size") or "").strip()
    features = a.get("features", "")
    pattern  = (a.get("pattern") or "").strip()

    title   = build_title(brand, title_raw, size, color)
    cond_id = CONDITION_ID_MAP.get(normalize_condition(condition), 3000)
    photos  = a.get("photos") or []
    item_photo_url = join_item_photos(photos, photos_per_item)

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

    def setcol(col: str, val: str):
        if col in row:
            row[col] = str(val)

    setcol("Action(SiteID=US|Country=US|Currency=USD|Version=1193)", "Add")
    setcol("Custom label (SKU)", "")
    setcol("Category ID", cat_id)
    setcol("Category name", category_text)
    setcol("Title", title)
    setcol("Schedule Time", current_gmt_schedule())
    setcol("Location", DEFAULT_LOCATION)

    # Price logic: Start price only
    setcol("Start price", price)
    setcol("Buy It Now price", "")            # blank intentionally

    setcol("Quantity", "1")
    setcol("Item photo URL", item_photo_url)
    setcol("Condition ID", str(cond_id))
    setcol("Description", desc_html)
    setcol("Format", getenv_str("EBAY_FORMAT", "FixedPrice"))
    setcol("Duration", getenv_str("EBAY_DURATION", "GTC"))

    setcol("Shipping profile name", DEFAULT_SHIP_PROFILE)
    setcol("Return profile name", DEFAULT_RET_PROFILE)
    setcol("Payment profile name", DEFAULT_PAY_PROFILE)

    setcol("C:Brand", brand)
    setcol("C:Color", color)
    setcol("C:Material", material)
    setcol("C:Size", size)
    setcol("C:Pattern", pattern)
    setcol("C:Features", features if isinstance(features, str) else json.dumps(features))

    return row, template_path

def analyze_then_rows(limit: Optional[int], photos_per_item: int, condition: str) -> Tuple[List[Dict[str, str]], List[str]]:
    r = requests.get(get_gallery_url(), timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Gallery fetch failed: {r.status_code}")
    payload = r.json() or {}
    groups  = payload.get("groups", [])
    if not groups:
        return [], []

    rows:           List[Dict[str, str]] = []
    used_templates: List[str]             = []

    for i, g in enumerate(groups, start=1):
        photos = [u.strip() for u in str(g.get("photo_urls", "")).split(",") if u.strip()]
        if not photos:
            continue
        analysis, _ = analyze_photos_with_gpt(photos), ""
        row, tpath = build_row_from_analysis(analysis, i, photos_per_item, condition)
        rows.append(row)
        used_templates.append(tpath)
        time.sleep(SLEEP_BETWEEN_ITEMS)
        if isinstance(limit, int) and len(rows) >= limit:
            break

    return rows, used_templates

def union_headers(template_paths: List[str]) -> List[str]:
    headers: List[str] = []
    for path in template_paths:
        for h in load_fieldnames(path):
            if h not in headers:
                headers.append(h)
    return headers

@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "chatbay-analyzer", "version": "v5.1-T2"}), 200

@app.route("/analyze_gallery")
def analyze_gallery():
    try:
        r       = requests.get(get_gallery_url(), timeout=30)
        if r.status_code != 200:
            return jsonify({"error": "Gallery fetch failed"}), r.status_code
        payload = r.json() or {}
        groups  = payload.get("groups", [])
        if not groups:
            return jsonify({"error": "No groups found"}), 404

        results = []
        for i, g in enumerate(groups, start=1):
            photos = [u.strip() for u in str(g.get("photo_urls", "")).split(",") if u.strip()]
            if not photos:
                continue
            parsed = analyze_photos_with_gpt(photos)
            results.append(parsed)
            time.sleep(SLEEP_BETWEEN_ITEMS)
        return jsonify(results), 200

    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500

@app.route("/preview_csv")
def preview_csv():
    try:
        photos_per_item = int(request.args.get("photos_per_item", DEFAULT_PHOTOS_PER_ITEM))
        condition      = normalize_condition(request.args.get("condition", DEFAULT_CONDITION))
        rows, tpaths    = analyze_then_rows(2, photos_per_item, condition)
        return jsonify({"preview_count": len(rows), "rows": rows, "templates_used": tpaths}), 200
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500

@app.route("/export_csv")
def export_csv():
    try:
        photos_per_item = int(request.args.get("photos_per_item", DEFAULT_PHOTOS_PER_ITEM))
        condition      = normalize_condition(request.args.get("condition", DEFAULT_CONDITION))
        rows, template_paths = analyze_then_rows(None, photos_per_item, condition)

        if not rows:
            return jsonify({"error": "No rows generated"}), 400

        headers = union_headers(template_paths)
        out     = io.StringIO()
        writer  = csv.DictWriter(out, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            for h in headers:
                if h not in row:
                    row[h] = ""
            writer.writerow(row)

        out.seek(0)
        fname = f"ebay-listings-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
        return send_file(
            io.BytesIO(out.getvalue().encode()),
            mimetype="text/csv",
            as_attachment=True,
            download_name=fname
        )

    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"🚀 Chatbay Analyzer v5.1-T2 running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
