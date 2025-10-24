# app.py — Chatbay Analyzer API (debug mode for Render import issue)

import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────────────────────
# Diagnostic info — this will appear in Render logs
print("📁 Current working directory:", os.getcwd())
print("📂 Files in cwd:", os.listdir("."))
print("💡 PYTHONPATH:", os.getenv("PYTHONPATH"))

# ──────────────────────────────────────────────────────────────
# CORS + base config
_frontend_origins = os.getenv(
    "FRONTEND_ORIGINS",
    "https://chatbay.site,https://www.chatbay.site,https://chatbay-analyzer.onrender.com,http://localhost:3000"
)
origins = [o.strip() for o in _frontend_origins.split(",") if o.strip()]

app = Flask(__name__)
print("✅ Flask app initialized successfully (imported by Gunicorn)")
CORS(app, resources={r"/*": {"origins": origins}})

# ──────────────────────────────────────────────────────────────
# Safe import for vision_test
try:
    from vision_test import analyze_item
    print("✅ vision_test imported successfully")
except Exception as e:
    print("❌ vision_test import failed:", e)
    analyze_item = None

# ──────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "chatbay-analyzer",
        "origins": origins,
    })

# ──────────────────────────────────────────────────────────────
def _payload_defaults(data: dict):
    return {
        "input": (data.get("input") or "").strip(),
        "condition": (data.get("condition") or os.getenv("DEFAULT_CONDITION", "preowned")).strip().lower(),
        "photos_per_item": int(data.get("photos_per_item") or os.getenv("DEFAULT_PHOTOS_PER_ITEM", 4)),
    }

@app.post("/preview_csv")
def preview_csv():
    if not analyze_item:
        return jsonify({"ok": False, "error": "vision_test failed to import"}), 500
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
    if not analyze_item:
        return jsonify({"ok": False, "error": "vision_test failed to import"}), 500
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

# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
