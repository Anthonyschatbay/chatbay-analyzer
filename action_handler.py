# ──────────────────────────────────────────────────────────────
# Chatbay → GPT-4o Vision Analyzer + eBay CSV Exporter (v4.5 HYBRID)
# Flask app for Render deployment
# 
# Upgrades:
#   • Auto-chunk batching: large galleries split into 6-group slices
#   • Smart pacing between batches (sleep & backoff)
#   • Keeps all v4.4 fixes (SEO titles, item specifics, /status route)
# ──────────────────────────────────────────────────────────────

import os, io, csv, json, time, random, datetime, traceback
from typing import List, Dict, Any, Optional
import requests
from flask import Flask, jsonify, send_file, request
from openai import OpenAI

# ──────────────────────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────────────────────
app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

DEFAULT_PHOTOS_PER_ITEM = int(os.getenv("DEFAULT_PHOTOS_PER_ITEM", 4))
DEFAULT_CONDITION = os.getenv("DEFAULT_CONDITION", "preowned").lower()
GALLERY_URL = os.getenv("CHATBAY_GALLERY_URL", "https://chatbay.site/wp-json/chatbay/v1/gallery")
CHUNK_SIZE = int(os.getenv("BATCH_LIMIT", 6))           # groups per batch
SLEEP_BETWEEN_BATCHES = float(os.getenv("BATCH_SLEEP", 5.0))  # seconds between batches
ENFORCE_PARAMS = os.getenv("ENFORCE_PARAMS", "0") == "1"

DEFAULT_LOCATION = os.getenv("EBAY_LOCATION", "Middletown, CT, USA")
DEFAULT_SHIP_PROFILE = os.getenv("EBAY_SHIP_PROFILE", "7.99 FLAT")
DEFAULT_RET_PROFILE = os.getenv("EBAY_RET_PROFILE", "No returns accepted")
DEFAULT_PAY_PROFILE = os.getenv("EBAY_PAY_PROFILE", "eBay Payments")

# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────
def current_gmt_schedule():
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    est = now_utc - datetime.timedelta(hours=5)
    next_day_est = (est + datetime.timedelta(days=1)).replace(hour=22, minute=0, second=0, microsecond=0)
    next_day_utc = next_day_est + datetime.timedelta(hours=5)
    return next_day_utc.strftime("%Y-%m-%d %H:%M:%S")

def normalize_condition(v: str) -> str:
    if not v: return DEFAULT_CONDITION
    v = v.lower().strip()
    if v in {"new","preowned","parts"}: return v
    if v in {"used","worn","vintage"}: return "preowned"
    if v in {"nos","deadstock","nwt"}: return "new"
    if "part" in v: return "parts"
    return DEFAULT_CONDITION

# simplified category and price helpers from v4.4 here...
CATEGORY_MAP = {"t-shirt":"15687","shirt":"15687","sweatshirt":"155226","hoodie":"155226",
                "underwear":"11507","lingerie":"11514","bag":"169291","magazine":"280"}
def match_category_id(text:str)->str:
    t=(text or "").lower()
    for k,v in CATEGORY_MAP.items():
        if k in t:return v
    return "15687"
def clean_price(raw:str,fallback="34.99")->str:
    if not raw:return fallback
    s=str(raw).replace("$","").split("-")[0].strip()
    return "".join(c for c in s if c.isdigit() or c==".") or fallback

# ──────────────────────────────────────────────────────────────
# GPT Vision with exponential backoff
# ──────────────────────────────────────────────────────────────
def vision_analyze_photos(photo_urls:List[str],max_tokens=500,max_attempts=5)->Dict[str,Any]:
    for attempt in range(max_attempts):
        try:
            comp=client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role":"user","content":[
                    {"type":"text","text":(
                        "Analyze these product photos and return concise JSON with keys: "
                        "title, category, description, price_estimate, brand, color, material, size, features, pattern."
                    )},
                    *[{"type":"image_url","image_url":{"url":u,"detail":"high"}} for u in photo_urls]
                ]}],
                max_tokens=max_tokens)
            raw=comp.choices[0].message.content.strip()
            try:
                parsed=json.loads(raw)
                return parsed if isinstance(parsed,dict) else {"raw":raw}
            except: return {"raw":raw}
        except Exception as e:
            wait=(0.5*(2**attempt))+random.uniform(0,0.25)
            if attempt>=max_attempts-1:
                return {"error":"rate_limit","detail":str(e)}
            time.sleep(wait)

# ──────────────────────────────────────────────────────────────
# /analyze_gallery — now auto-chunks
# ──────────────────────────────────────────────────────────────
@app.route("/analyze_gallery")
def analyze_gallery():
    try:
        gallery_url=request.args.get("gallery",GALLERY_URL)
        headers={"User-Agent":"ChatbayAnalyzer/4.5","Accept":"application/json"}
        resp=requests.get(gallery_url,headers=headers,timeout=30)
        if resp.status_code!=200:
            return jsonify({"error":"Gallery fetch failed","status":resp.status_code}),resp.status_code
        gallery=resp.json(); groups=gallery.get("groups",[])
        if not groups: return jsonify({"error":"No groups found"}),404

        results=[]; total=len(groups)
        chunks=[groups[i:i+CHUNK_SIZE] for i in range(0,total,CHUNK_SIZE)]

        for c_idx,chunk in enumerate(chunks,1):
            print(f"⚙️ Processing batch {c_idx}/{len(chunks)} ({len(chunk)} groups)")
            for g_idx,g in enumerate(chunk,1):
                photos=[u.strip() for u in str(g.get("photo_urls","")).split(",") if u.strip()]
                if not photos: continue
                parsed=vision_analyze_photos(photos)
                parsed["photos"]=photos
                parsed["group"]=len(results)+1
                results.append(parsed)
                time.sleep(0.2)  # light intra-batch pacing
            if c_idx<len(chunks):
                print(f"⏸ Sleeping {SLEEP_BETWEEN_BATCHES}s between batches...")
                time.sleep(SLEEP_BETWEEN_BATCHES)

        return jsonify({"total_groups":total,"analyzed":len(results),"results":results}),200
    except Exception:
        return jsonify({"error":traceback.format_exc()}),500

# ──────────────────────────────────────────────────────────────
# CSV/preview + /status routes stay identical to v4.4
# (you can paste them below unchanged)
# ──────────────────────────────────────────────────────────────
