# ──────────────────────────────────────────────────────────────
# Chatbay → GPT-4o Vision Analyzer + eBay CSV Exporter (v4.9)
# Flask app for Render deployment — rate-limit aware + optional asyncio
# ──────────────────────────────────────────────────────────────
import os, io, csv, json, time, datetime, traceback, requests
from typing import Dict, Any, List, Optional

from flask import Flask, jsonify, send_file, request
from openai import OpenAI
try:
    # Optional async client (SDK v1+). If unavailable, we'll fall back to sync.
    from openai import AsyncOpenAI  # type: ignore
    HAS_ASYNC = True
except Exception:
    HAS_ASYNC = False

# ──────────────────────────────────────────────────────────────
# App
# ──────────────────────────────────────────────────────────────
app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
async_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY")) if HAS_ASYNC else None

# ──────────────────────────────────────────────────────────────
# Env helpers
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

# batching / pacing
CHUNK_SIZE = getenv_int("BATCH_LIMIT", 3)                 # groups per batch (sequential mode)
SLEEP_BETWEEN_BATCHES = getenv_float("BATCH_SLEEP", 6.0)  # seconds between calls/batches
MAX_RETRIES = getenv_int("MAX_RETRIES", 5)

# optional async
ASYNC_CONCURRENCY = getenv_int("ASYNC_CONCURRENCY", 1)    # >1 enables asyncio path (if SDK supports)
ASYNC_SPREAD_DELAY = getenv_float("ASYNC_SPREAD_DELAY", 0.4)  # small delay between task launches

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
        if k in lower: return cid
    return "15687"

# ──────────────────────────────────────────────────────────────
# Condition helpers
# ──────────────────────────────────────────────────────────────
CONDITION_ID_MAP = {"new": 1000, "preowned": 3000, "parts": 7000}
def normalize_condition(v: str) -> str:
    if not v: return DEFAULT_CONDITION
    v = v.strip().lower()
    if v in {"new","preowned","parts"}: return v
    if v in {"used","vintage","worn"}: return "preowned"
    if v in {"nwt","nos","deadstock"}: return "new"
    if "part" in v: return "parts"
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
# Routes: health / status
# ──────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "chatbay-analyzer"}), 200

@app.route("/status")
def status():
    return jsonify({
        "version": "v4.9",
        "batch_limit": CHUNK_SIZE,
        "batch_sleep": SLEEP_BETWEEN_BATCHES,
        "max_retries": MAX_RETRIES,
        "async_enabled": HAS_ASYNC and (ASYNC_CONCURRENCY > 1),
        "async_concurrency": ASYNC_CONCURRENCY,
        "async_spread_delay": ASYNC_SPREAD_DELAY,
        "gallery_url": GALLERY_URL,
        "defaults": {
            "photos_per_item": DEFAULT_PHOTOS_PER_ITEM,
            "condition": DEFAULT_CONDITION
        }
    }), 200

# ──────────────────────────────────────────────────────────────
# GPT call helpers (sync + async) with rate-limit–aware retry
# ──────────────────────────────────────────────────────────────
def _wait_from_headers(headers: Optional[dict], attempt: int) -> float:
    if not headers: return max(SLEEP_BETWEEN_BATCHES, 3.0) + attempt * 2
    # try OpenAI v1 headers; fall back to exponential
    for key in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        if key in headers:
            try:
                base = float(headers[key])
                return max(base, 2.0) + attempt * 1.5
            except Exception:
                pass
    return max(SLEEP_BETWEEN_BATCHES, 3.0) + attempt * 2

def call_gpt_sync(images: List[str]) -> Optional[dict]:
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
                *[{"type": "image_url", "image_url": {"url": u, "detail": "high"}} for u in images],
            ]
        }]
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.chat.completions.create(**payload)
            return {"text": resp.choices[0].message.content, "headers": getattr(resp, "headers", None)}
        except Exception as e:
            msg = str(e)
            headers = getattr(e, "response_headers", None) or getattr(e, "headers", None)
            if ("429" in msg) or ("Rate limit" in msg) or ("tokens per min" in msg):
                wait = _wait_from_headers(headers, attempt)
                print(f"⏳ RL (sync) attempt {attempt}/{MAX_RETRIES} — sleep {wait:.1f}s")
                time.sleep(wait); continue
            if ("502" in msg) or ("500" in msg) or ("timeout" in msg.lower()):
                wait = 2 * attempt
                print(f"⚠️ 5xx/timeout (sync) attempt {attempt}/{MAX_RETRIES} — sleep {wait:.1f}s")
                time.sleep(wait); continue
            print(f"❌ GPT sync error (no retry): {msg}")
            return None
    return None

async def call_gpt_async(images: List[str], sem, attempt0_sleep: float = 0.0) -> Optional[dict]:
    import asyncio
    async with sem:
        if attempt0_sleep > 0:
            await asyncio.sleep(attempt0_sleep)
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
                    *[{"type": "image_url", "image_url": {"url": u, "detail": "high"}} for u in images],
                ]
            }]
        }
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = await async_client.chat.completions.create(**payload)  # type: ignore
                return {"text": resp.choices[0].message.content, "headers": getattr(resp, "headers", None)}
            except Exception as e:
                msg = str(e)
                headers = getattr(e, "response_headers", None) or getattr(e, "headers", None)
                if ("429" in msg) or ("Rate limit" in msg) or ("tokens per min" in msg):
                    wait = _wait_from_headers(headers, attempt)
                    print(f"⏳ RL (async) attempt {attempt}/{MAX_RETRIES} — sleep {wait:.1f}s")
                    await asyncio.sleep(wait); continue
                if ("502" in msg) or ("500" in msg) or ("timeout" in msg.lower()):
                    wait = 2 * attempt
                    print(f"⚠️ 5xx/timeout (async) attempt {attempt}/{MAX_RETRIES} — sleep {wait:.1f}s")
                    await asyncio.sleep(wait); continue
                print(f"❌ GPT async error (no retry): {msg}")
                return None
        return None

# ──────────────────────────────────────────────────────────────
# Analyze gallery (auto: async or sync)
# ──────────────────────────────────────────────────────────────
@app.route("/analyze_gallery")
def analyze_gallery():
    try:
        gallery_url = get_gallery_url()
        print(f"📸 Fetching gallery: {gallery_url}")
        r = requests.get(gallery_url, timeout=30)
        if r.status_code != 200:
            return jsonify({"error": "Gallery fetch failed", "status": r.status_code}), r.status_code

        groups = (r.json() or {}).get("groups", [])
        if not groups:
            return jsonify({"error": "No groups found"}), 404

        results: List[dict] = []
        total = len(groups)
        print(f"🧠 {total} groups | async={'ON' if (HAS_ASYNC and ASYNC_CONCURRENCY>1) else 'OFF'} "
              f"| concurrency={ASYNC_CONCURRENCY} | sleep={SLEEP_BETWEEN_BATCHES}s")

        # ---- ASYNC PATH ----
        if HAS_ASYNC and ASYNC_CONCURRENCY > 1:
            import asyncio

            async def run_async(groups: List[dict]) -> List[dict]:
                sem = asyncio.Semaphore(ASYNC_CONCURRENCY)
                tasks = []
                for i, g in enumerate(groups, start=1):
                    photos = [u.strip() for u in str(g.get("photo_urls", "")).split(",") if u.strip()]
                    if not photos:
                        continue
                    # spread start times slightly to avoid instant bursts
                    delay = (i % ASYNC_CONCURRENCY) * ASYNC_SPREAD_DELAY
                    tasks.append(_one_async(i, photos, sem, delay))
                return [r for r in await asyncio.gather(*tasks) if r]

            async def _one_async(idx: int, photos: List[str], sem, delay: float):
                item = await call_gpt_async(photos, sem, attempt0_sleep=delay)
                if not item:
                    return {"group": idx, "error": "Failed after retries", "photos": photos}
                raw = (item.get("text") or "").strip()
                try:
                    parsed = json.loads(raw)
                    if not isinstance(parsed, dict): raise ValueError
                except Exception:
                    parsed = {"group": idx, "raw": raw}
                parsed["photos"] = photos
                return parsed

            results = asyncio.run(run_async(groups))

        # ---- SYNC PATH ----
        else:
            # process in small batches with sleeps between to be gentle
            for i in range(0, total, CHUNK_SIZE):
                batch = groups[i:i+CHUNK_SIZE]
                print(f"🚀 Batch {i//CHUNK_SIZE+1}/{(total-1)//CHUNK_SIZE+1}")
                for j, g in enumerate(batch, start=1):
                    idx = i + j
                    photos = [u.strip() for u in str(g.get("photo_urls", "")).split(",") if u.strip()]
                    if not photos: continue
                    item = call_gpt_sync(photos)
                    if not item:
                        results.append({"group": idx, "error": "Failed after retries", "photos": photos})
                    else:
                        raw = (item.get("text") or "").strip()
                        try:
                            parsed = json.loads(raw)
                            if not isinstance(parsed, dict): raise ValueError
                        except Exception:
                            parsed = {"group": idx, "raw": raw}
                        parsed["photos"] = photos
                        results.append(parsed)
                    time.sleep(SLEEP_BETWEEN_BATCHES)
                print(f"✅ Batch {i//CHUNK_SIZE+1} done — sleeping {SLEEP_BETWEEN_BATCHES}s")
                time.sleep(SLEEP_BETWEEN_BATCHES)

        print(f"🏁 Completed {len(results)} groups.")
        return jsonify(results), 200

    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500

# ──────────────────────────────────────────────────────────────
# CSV builder
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
    if isinstance(limit, int): data = data[:limit]
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
        return send_file(io.BytesIO(out.getvalue().encode()),
                         mimetype="text/csv", as_attachment=True, download_name=fname)
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
    print(f"🚀 Chatbay Analyzer v4.9 running on port {port} | async={'ON' if (HAS_ASYNC and ASYNC_CONCURRENCY>1) else 'OFF'}")
    app.run(host="0.0.0.0", port=port, debug=False)
