# Chatbay → GPT-4o Vision Analyzer
# Flask app for Render.com deployment

import os
import json
import requests
import traceback
from flask import Flask, jsonify
from openai import OpenAI

# ──────────────────────────────────────────────────────────────
# Flask + OpenAI setup
# ──────────────────────────────────────────────────────────────
app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ──────────────────────────────────────────────────────────────
# Basic heartbeat for uptime checks
# ──────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "chatbay-analyzer"})

@app.route("/")
def home():
    return jsonify({"status": "ok", "message": "Chatbay analyzer online"})

# ──────────────────────────────────────────────────────────────
# Analyze Postimg gallery from WordPress REST endpoint
# ──────────────────────────────────────────────────────────────
@app.route("/analyze_gallery")
def analyze_gallery():
    try:
        GALLERY_URL = os.getenv(
            "CHATBAY_GALLERY_URL",
            "https://chatbay.site/wp-json/chatbay/v1/gallery"
        )

        headers = {
            "User-Agent": "ChatbayAnalyzer/1.0 (+https://chatbay-analyzer.onrender.com)",
            "Accept": "application/json"
        }

        # Attempt to fetch gallery JSON
        r = requests.get(GALLERY_URL, headers=headers, timeout=20)
        print(f"📡 Fetching gallery from: {GALLERY_URL}")
        print(f"Gallery fetch status: {r.status_code}")

        # Log limited response snippet for debugging
        snippet = r.text[:300] if r.text else "No response body"
        print(f"Gallery response snippet:\n{snippet}")

        if r.status_code != 200:
            return jsonify({
                "error": "Gallery fetch failed",
                "status_code": r.status_code,
                "response_snippet": snippet
            }), r.status_code

        # Parse the gallery JSON
        try:
            gallery = r.json()
        except Exception as parse_err:
            print(f"❌ JSON decode error: {parse_err}")
            return jsonify({"error": "Invalid JSON from gallery", "raw": snippet}), 500

        results = []

        # Process each image group
        for idx, group in enumerate(gallery.get("groups", []), start=1):
            photo_urls = group.get("photo_urls", "").split(",")
            if not photo_urls:
                continue

            print(f"🖼️ Processing group {idx} with {len(photo_urls)} images")

            # Send to OpenAI for visual analysis
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Analyze these product photos and describe what you see — "
                                    "brand, style, color, and any visible text. "
                                    "Return concise JSON with keys: "
                                    "title, category, description, price_estimate."
                                ),
                            },
                            *[
                                {"type": "image_url", "image_url": {"url": u.strip(), "detail": "high"}}
                                for u in photo_urls if u.strip()
                            ],
                        ],
                    }
                ],
                max_tokens=400,
            )

            raw = completion.choices[0].message.content
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = {"group": idx, "raw": raw}

            results.append(parsed)

        return jsonify(results)

    except Exception as e:
        tb = traceback.format_exc()
        print(f"🔥 Full error traceback:\n{tb}")
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────────────────────
# Render entry point
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
