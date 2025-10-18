@app.route("/analyze_gallery")
def analyze_gallery():
    try:
        headers = {
            "User-Agent": "ChatbayAnalyzer/1.0 (+https://chatbay-analyzer.onrender.com)",
            "Accept": "application/json"
        }

        GALLERY_URL = os.getenv(
            "CHATBAY_GALLERY_URL",
            "https://chatbay.site/wp-json/chatbay/v1/gallery"
        )

        # 🔍 Attempt to fetch gallery
        r = requests.get(GALLERY_URL, headers=headers, timeout=20)

        # Log full response details to diagnose
        print(f"Gallery fetch status: {r.status_code}")
        print(f"Gallery response text (first 300 chars): {r.text[:300]}")

        if r.status_code != 200:
            return jsonify({
                "error": f"Gallery fetch failed",
                "status_code": r.status_code,
                "response_snippet": r.text[:200]
            }), r.status_code

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
                                    "brand, style, color, any visible text. Return concise JSON "
                                    "with keys: title, category, description, price_estimate."
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

            raw = completion.choices[0].message.content
            try:
                results.append(json.loads(raw))
            except Exception:
                results.append({"group": idx, "raw": raw})

        return jsonify(results)

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"Full error traceback:\n{tb}")
        return jsonify({"error": str(e)}), 500
