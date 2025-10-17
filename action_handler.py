# Chatbay → GPT-4o Vision Analyzer
# Flask app for Render.com deployment

import os, json, requests
from flask import Flask, jsonify
from openai import OpenAI

app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 🔎 simple heartbeat for Render/uptime checks
@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "chatbay-analyzer"})

GALLERY_URL = os.getenv("CHATBAY_GALLERY_URL", "https://chatbay.site/wp-json/chatbay/v1/gallery")

@app.route("/")
def home():
    return jsonify({"status": "ok", "message": "Chatbay analyzer online"})

@app.route("/analyze_gallery")
def analyze_gallery():
    try:
        r = requests.get(GALLERY_URL, timeout=20)
        r.raise_for_status()
        gallery = r.json()

        results = []
        for idx, group in enumerate(gallery.get("groups", []), start=1):
            photo_urls = group["photo_urls"].split(",")
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
                                    "brand, style, color, any visible text. Return concise JSON with keys: "
                                    "title, category, description, price_estimate."
                                ),
                            },
                            *[
                                {"type": "image_url", "image_url": {"url": u.strip(), "detail": "high"}}
                                for u in photo_urls
                            ],
                        ],
                    }
                ],
                max_tokens=400,
            )
            # try to parse JSON; if model returns plain text, fall back to raw string
            raw = completion.choices[0].message.content
            try:
                results.append(json.loads(raw))
            except Exception:
                results.append({"group": idx, "raw": raw})

        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # Render sets $PORT; default to 10000 if missing
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
