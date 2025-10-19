# ──────────────────────────────────────────────────────────────
# Chatbay → GPT-4o Vision Analyzer + eBay CSV Exporter (v4.8)
# Flask app for Render deployment — full rate-limit control
# ──────────────────────────────────────────────────────────────
import os, io, csv, json, time, datetime, traceback, requests
from typing import Dict, Any, List, Optional
from flask import Flask, jsonify, send_file, request
from openai import OpenAI
from openai.error import RateLimitError, APIError

# ──────────────────────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────────────────────
app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ──────────────────────────────────────────────────────────────
# Env utilities
# ──────────────────────────────────────────────────────────────
def getenv_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)).strip())
    except Exception:
        print(f"⚠️ Invalid float for {key}, default={default}")
        return default

def getenv_int(key: str, default: int) -> int:
    try:
        return int(float(os.getenv(key, str(default)).strip()))
    except Exception:
        print(f"⚠️ Invalid int for {key}, default={default}")
        return default

def getenv_str(key: str, default: str) -> str:
    return str(os.getenv(key, default)).strip()

# ──────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────
DEFAULT_PHOTOS_PER_ITEM = getenv_int("DEFAULT_PHOTOS_PER_ITEM", 4)
DEFAULT_CONDITION = getenv_str("DEFAULT_CONDITION", "preowned").lower()
GALLERY_URL = getenv_str("CHATBAY_GALLERY_URL", "https://chatbay.site/wp-json/chatbay/v1/gallery")

DEFAULT_LOCATION = getenv_str("EBAY_LOCATION", "Middletown, CT, USA")
DEFAULT_SHIP_PROFILE = getenv_str("EBAY_SHIP_PROFILE", "7.99 FLAT")
DEFAULT_RET_PROFILE = getenv_str("EBAY_RET_PROFILE", "No returns accepted")
DEFAULT_PAY_PROFILE = getenv_str("EBAY_PAY_PROFILE", "eBay Payments")

CHUNK_SIZE = getenv_int("BATCH_LIMIT", 3)
SLEEP_BETWEEN_BATCHES = getenv_float("BATCH_SLEEP", 6.0)
MAX_RETRIES = getenv_int("MAX_RETRIES", 5)

# ──────────────────────────────────────────────────────────────
# Category mapping
# ──────────────────────────────────────────────────────────────
CATEGORY_MAP = {
    "t-shirt": "15687", "shirt": "15687", "sweatshirt": "155226", "hoodie": "155226",
    "jacket": "57988", "pants": "57989", "jeans": "11483", "shorts": "15690",
    "underwear": "11507", "lingerie": "11514", "hat": "163571", "cap": "163571",
    "bag": "169291", "tote": "169291", "patch": "156521", "sticker": "165326",
    "button": "10960", "magazine": "280", "book": "261186", "poster": "140",
    "tool": "631", "lamp": "112581", "decor": "10033", "wallet": "45258",
}

def match_category_id(text: str) -> str:
    if not text: return "15687"
    lower = text.lower()
    for k, cid in CATEGORY_MAP.items():
        if k in lower:
            return cid
    return "15687"

# ──────────────────────────────────────────────────────────────
# Condition helpers
# ──────────────────────────────────────────────────────────────
CONDITION_ID_MAP = {"new": 1000, "preowned": 3000, "parts": 7000}

def normalize_condition(v: str) -> str:
    if not v: return DEFAULT_CONDITION
    v = v.strip().lower()
    if v in {"new", "preowned", "parts"}: return v
    if v in {"used", "vintage", "worn"}: return "preowned"
    if v in {"nwt", "nos", "deadstock"}: return "new"
    if "part" in v: return "parts"
    return DEFAULT_CONDITION

# ──────────────────────────────────────────────────────────────
# Utility
# ──────────────────────────────────────────────────────────────
def current_gmt_schedule() -> str:
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    est = now_utc - datetime.timedelta(hours=5)
    next_day_est = (est + datetime.timedelta(days=1)).replace(hour=22, minute=0, second=0, microsecond=0)
    next_day_utc = next_day_est + datetime.timedelta(hours=5)
    return next_day_utc.strftime("%Y-%m-%d %H:%M:%S")

def clean_price(raw: str, fallback="34.99") -> str:
    if not raw: return fallback
    s = str(raw).replace("$", "").split("-")[0].strip()
    return "".join(ch for ch in s if ch.isdigit() or ch == ".") or fallback

def limit_len(s: str, n: int) -> str:
    return (s or "")[:n]

def build_title(brand, title_raw, size, color) -> str:
    parts = [p for p in [brand, title_raw, size, color] if p]
    return limit_len(" ".join(parts).strip(), 79)

def get_gallery_url() -> str:
    return request.args.get("gallery", GALLERY_URL)

# ──────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "chatbay-analyzer"}), 200

@app.route("/status")
def status():
    return jsonify({
        "version": "v4.8",
        "batch_limit": CHUNK_SIZE,
        "batch_sleep": SLEEP_BETWEEN_BATCHES,
        "max_retries": MAX_RETRIES,
        "gallery_url": GALLERY_URL,
        "default_condition": DEFAULT_CONDITION,
    }), 200

# ──────────────────────────────────────────────────────────────
# GPT call with rate-limit awareness
# ──────────────────────────────────────────────────────────────
def call_gpt_with_retry(payload: dict):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(**payload)
            return response
        except RateLimitError as e:
            reset_seconds = 10
            headers = getattr(e, "response_headers", {}) or {}
            if "x-ratelimit-reset-requests" in headers:
                reset_seconds = float(headers["x-ratelimit-reset-requests"])
            elif "x-ratelimit-reset-tokens" in headers:
                reset_seconds = float(headers["x-ratelimit-reset-tokens"])
            wait = reset_seconds + attempt * 2
            print(f"⏳ Rate limit hit. Waiting {wait:.1f}s (attempt {attempt}/{MAX_RETRIES})")
            time.sleep(wait)
        except APIError as e:
            print(f"⚠️ APIError: {e}, retrying in {attempt*3}s")
            time.sleep(attempt * 3)
        except Exception as e:
            msg = str(e)
            if "429" in msg or "limit" in msg:
                wait = attempt * 5
                print(f"⏳ 429 backoff {wait}s (attempt {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            print(f"❌ GPT error: {msg}")
            return None
    print("❌ Failed after max retries")
    return None

# ──────────────────────────────────────────────────────────────
# Analyze gallery
# ──────────────────────────────────────────────────────────────
@app.route("/analyze_gallery")
def analyze_gallery():
    try:
        gallery_url = get_gallery_url()
        print(f"📸 Fetching gallery from {gallery_url}")
        r = requests.get(gallery_url, timeout=30)
        if r.status_code != 200:
            return jsonify({"error": "Gallery fetch failed", "status": r.status_code}), r.status_code
        data = r.json()
        groups = data.get("groups", [])
        if not groups:
            return jsonify({"error": "No groups found"}), 404

        results = []
        total = len(groups)
        print(f"🧠 {total} groups found | batch={CHUNK_SIZE}, sleep={SLEEP_BETWEEN_BATCHES}s")

        for i in range(0, total, CHUNK_SIZE):
            batch = groups[i:i + CHUNK_SIZE]
            print(f"🚀 Batch {i//CHUNK_SIZE+1}/{(total-1)//CHUNK_SIZE+1}")
            for j, g in enumerate(batch, start=1):
                idx = i + j
                photos = [u.strip() for u in str(g.get("photo_urls", "")).split(",") if u.strip()]
                if not photos:
                    continue

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
                            *[{"type": "image_url", "image_url": {"url": u, "detail": "high"}} for u in photos],
                        ]
                    }]
                }

                comp = call_gpt_with_retry(payload)
                if not comp:
                    results.append({"group": idx, "error": "Failed after retries", "photos": photos})
                    continue

                raw = comp.choices[0].message.content.strip()
                try:
                    parsed = json.loads(raw)
                    if not isinstance(parsed, dict):
                        raise ValueError
                except Exception:
                    parsed = {"group": idx, "raw": raw}
                parsed["photos"] = photos
                results.append(parsed)
                time.sleep(SLEEP_BETWEEN_BATCHES)

            print(f"✅ Batch {i//CHUNK_SIZE+1} done. Sleeping {SLEEP_BETWEEN_BATCHES}s.")
            time.sleep(SLEEP_BETWEEN_BATCHES)

        print(f"🏁 Completed {len(results)} groups.")
        return jsonify(results), 200

    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500

# ──────────────────────────────────────────────────────────────
# CSV build
# ──────────────────────────────────────────────────────────────
def ebay_row_from_analysis(a: Dict[str, Any], idx: int, photos_per_item: int, condition: str) -> Dict[str, str]:
    title_raw = (a.get("title") or f"Item {idx}").strip()
    desc_raw = (a.get("description") or "Collectible item.").strip()
    category_text = a.get("category", "")
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
    photo_urls = ",".join(photos[:max(1, min(12, photos_per_item))])

    return {
        "Action(SiteID=US|Country=US|Currency=USD|Version=1193)": "Add",
        "Category ID": cat_id,
        "Category name": category_text or "Other",
        "Title": title,
        "Schedule Time": current_gmt_schedule(),
        "Start price": price,
        "Quantity": "1",
        "Item photo URL": photo_urls,
        "Condition ID": str(cond_id),
        "Description": desc_html,
        "Format": "FixedPrice",
        "Duration": "GTC",
        "Buy It Now price": price,
        "Immediate pay required": "1",
        "Location": DEFAULT_LOCATION,
        "Shipping profile name": DEFAULT_SHIP_PROFILE,
        "Return profile name": DEFAULT_RET_PROFILE,
        "Payment profile name": DEFAULT_PAY_PROFILE,
        "C:Brand": brand, "C:Color": color, "C:Material": material,
        "C:Size": size, "C:Pattern": pattern, "C:Features": features, "C:Vintage": "No",
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
        photos_per_item = int(request.args.get("photos_per_item", DEFAULT_PHOTOS_PER_ITEM))
        condition = normalize_condition(request.args.get("condition", DEFAULT_CONDITION))
        rows = _analyze_then_rows(None, photos_per_item, condition)
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        for row in rows: w.writerow(row)
        out.seek(0)
        fname = f"ebay-listings-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
        return send_file(io.BytesIO(out.getvalue().encode()), mimetype="text/csv",
                         as_attachment=True, download_name=fname)
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

# ──────────────────────────────────────────────────────────────
# Run
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"🚀 Chatbay Analyzer v4.8 running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
