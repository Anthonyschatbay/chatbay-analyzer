# app.py — Chatbay Analyzer API (v8.4 Secure + HTML UI)
# Exposes:
#   GET  /                    → secure_upload.html (browser UI)
#   GET  /health              → service status
#   POST /preview_csv         → returns JSON preview
#   POST /export_csv          → returns JSON or downloadable CSV
#
# Adds: Password protection via UPLOAD_PASSWORD environment variable

import os
from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
from dotenv import load_dotenv
from io import BytesIO

load_dotenv()

# ──────────────────────────────────────────────────────────────
# Environment setup
_frontend_origins = os.getenv(
    "FRONTEND_ORIGINS",
    "https://chatbay.site,https://www.chatbay.site,https://chatbay-analyzer.onrender.com,http://localhost:3000"
)
origins = [o.strip() for o in _frontend_origins.split(",") if o.strip()]

UPLOAD_PASSWORD = os.getenv("UPLOAD_PASSWORD", "").strip()

app = Flask(__name__, template_folder="templates")
CORS(app, resources={r"/*": {"origins": origins}})

# Import analyzer after env is ready
from vision_test import analyze_item, build_csv_bytes  # noqa: E402

# ──────────────────────────────────────────────────────────────
def check_auth(req):
    """Validate password in request args, JSON, or Authorization header."""
    pw = ""
    if "password" in req.args:
        pw = req.args.get("password", "")
    elif req.is_json:
        data = req.get_json(silent=True) or {}
        pw = data.get("password", "")
    elif "Authorization" in req.headers:
        auth = req.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            pw = auth.replace("Bearer ", "").strip()

    if not UPLOAD_PASSWORD:
        print("⚠️  Warning: UPLOAD_PASSWORD not set in environment!")
        return True

    if pw != UPLOAD_PASSWORD:
        print("❌ Unauthorized access attempt.")
        return False

    return True

# ──────────────────────────────────────────────────────────────
@app.get("/")
def index():
    """Serve the secure upload web form."""
    return render_template("secure_upload.html")

# ──────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    """Basic service heartbeat."""
    return jsonify({
        "ok": True,
        "service": "chatbay-analyzer",
        "origins": origins,
        "password_protected": bool(UPLOAD_PASSWORD),
    })

# ──────────────────────────────────────────────────────────────
def _payload_defaults(data: dict):
    """Normalize input payload and apply environment defaults."""
    return {
        "input": (data.get("input") or "").strip(),
        "condition": (data.get("condition") or os.getenv("DEFAULT_CONDITION", "preowned")).strip().lower(),
        "photos_per_item": int(data.get("photos_per_item") or os.getenv("DEFAULT_PHOTOS_PER_ITEM", 4)),
    }

# ──────────────────────────────────────────────────────────────
@app.post("/preview_csv")
def preview_csv():
    """Run a 1-item Vision analysis preview."""
    if not check_auth(request):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

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

# ──────────────────────────────────────────────────────────────
@app.post("/export_csv")
def export_csv():
    """Run full batch Vision analysis and return downloadable CSV."""
    if not check_auth(request):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    try:
        data = request.get_json(force=True) or {}
        p = _payload_defaults(data)
        result = analyze_item(
            input_arg=p["input"],
            condition=p["condition"],
            photos_per_item=p["photos_per_item"],
            preview=False
        )

        # Convert result to CSV bytes and send as file download
        csv_bytes, filename = build_csv_bytes(result)
        return send_file(
            BytesIO(csv_bytes.read()),
            as_attachment=True,
            download_name=filename,
            mimetype="text/csv"
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
