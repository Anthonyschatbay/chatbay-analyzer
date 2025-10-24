# app.py — Chatbay Analyzer API (v8.0)
# Exposes:
#   GET  /health
#   POST /preview_csv  {input, condition, photos_per_item}
#   POST /export_csv   {input, condition, photos_per_item}
#
# Notes:
# - Wraps vision_test.analyze_item() so the same logic powers CLI & API
# - CORS is controlled by FRONTEND_ORIGINS (comma-separated) in .env

import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

# Allow specific origins (comma-separated), or default to your domain
_frontend_origins = os.getenv("FRONTEND_ORIGINS", "https://chatbay.site,https://www.chatbay.site")
origins = [o.strip() for o in _frontend_origins.split(",") if o.strip()]

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": origins}})

# Import analyzer after env is ready
from vision_test import analyze_item  # noqa: E402

@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "chatbay-analyzer",
        "origins": origins,
    })

def _payload_defaults(data: dict):
    return {
        "input": (data.get("input") or "").strip(),
        "condition": (data.get("condition") or os.getenv("DEFAULT_CONDITION", "preowned")).strip().lower(),
        "photos_per_item": int(data.get("photos_per_item") or os.getenv("DEFAULT_PHOTOS_PER_ITEM", 4)),
    }

@app.post("/preview_csv")
def preview_csv():
    try:
        data = request.get_json(force=True) or {}
        p = _payload_defaults(data)
        result = analyze_item(
            input_arg=p["input"],
            condition=p["condition"],
            photos_per_item=p["photos_per_item"],
            preview=True
        )
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.post("/export_csv")
def export_csv():
    try:
        data = request.get_json(force=True) or {}
        p = _payload_defaults(data)
        result = analyze_item(
            input_arg=p["input"],
            condition=p["condition"],
            photos_per_item=p["photos_per_item"],
            preview=False
        )
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

if __name__ == "__main__":
    # Local dev
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
