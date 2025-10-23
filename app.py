# ──────────────────────────────────────────────────────────────
# Chatbay Analyzer → Flask + OpenAI Vision + Dropbox
# v8.0 — optimized for Render deployment
# ──────────────────────────────────────────────────────────────

import os, io, json, tempfile, datetime
from flask import Flask, jsonify, request, send_file
from openai import OpenAI
import dropbox

# ── setup
app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ── dropbox client factory
def get_dbx():
    return dropbox.Dropbox(
        oauth2_refresh_token=os.getenv("DROPBOX_REFRESH_TOKEN"),
        app_key=os.getenv("DROPBOX_APP_KEY"),
        app_secret=os.getenv("DROPBOX_APP_SECRET"),
    )

# ──────────────────────────────────────────────────────────────
# 🔹 basic health check
@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.datetime.utcnow().isoformat()})

# ──────────────────────────────────────────────────────────────
# 🔹 vision preview endpoint
@app.route("/preview_csv")
def preview_csv():
    """Return one-item Vision analysis preview."""
    from vision_test import analyze_images_with_vision
    gallery = request.args.get("gallery")
    condition = request.args.get("condition", "preowned")
    photos_per_item = int(request.args.get("photos_per_item", 4))

    if not gallery:
        return jsonify({"error": "Missing gallery parameter"}), 400

    try:
        result = analyze_images_with_vision(
            gallery_urls=gallery.split(","),
            condition=condition,
            photos_per_item=photos_per_item,
            limit_preview=True,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────────────────────
# 🔹 full export endpoint
@app.route("/export_csv")
def export_csv():
    """Generate full CSV and upload to Dropbox."""
    from vision_test import analyze_images_with_vision, build_csv_bytes

    gallery = request.args.get("gallery")
    condition = request.args.get("condition", "preowned")
    photos_per_item = int(request.args.get("photos_per_item", 4))

    if not gallery:
        return jsonify({"error": "Missing gallery parameter"}), 400

    try:
        data = analyze_images_with_vision(
            gallery_urls=gallery.split(","),
            condition=condition,
            photos_per_item=photos_per_item,
            limit_preview=False,
        )
        csv_bytes, filename = build_csv_bytes(data)

        # upload to dropbox
        dbx = get_dbx()
        folder = os.getenv("DROPBOX_CSV_FOLDER", "/csv")
        path = f"{folder}/{filename}"
        dbx.files_upload(csv_bytes.getvalue(), path, mode=dropbox.files.WriteMode.overwrite)

        return send_file(
            io.BytesIO(csv_bytes.getvalue()),
            mimetype="text/csv",
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────────────────────
# 🔹 latest CSV download link
@app.route("/latest_csv_link")
def latest_csv_link():
    """Return the most recent CSV’s temporary Dropbox link."""
    try:
        dbx = get_dbx()
        folder = os.getenv("DROPBOX_CSV_FOLDER", "/csv")
        res = dbx.files_list_folder(folder)
        files = [e for e in res.entries if isinstance(e, dropbox.files.FileMetadata)]
        if not files:
            return {"error": "No CSV found"}, 404
        newest = max(files, key=lambda e: e.client_modified)
        link = dbx.files_get_temporary_link(newest.path_lower).link
        return {"filename": newest.name, "link": link}
    except Exception as e:
        return {"error": str(e)}, 500

# ──────────────────────────────────────────────────────────────
# run local (Render uses gunicorn)
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
