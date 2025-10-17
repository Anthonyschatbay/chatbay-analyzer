from flask import Flask, request, jsonify
import os
from action_handler import analyze_gallery  # assuming this is your main function

app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({"message": "Chatbay Analyzer is live."})

@app.route("/gallery", methods=["POST"])
def gallery():
    try:
        data = request.get_json()
        gallery_url = data.get("url")

        if not gallery_url:
            return jsonify({"error": "Missing 'url' in request JSON"}), 400

        result = analyze_gallery(gallery_url)
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
