# vision_test.py — Vision analyzer core (v8.1)
# Used by both Flask endpoints and CLI testing.

import os
import io
import csv
import re
import json
import datetime
from statistics import median
import requests
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ──────────────────────────────────────────────────────────────
def fetch_image_bytes(url: str) -> bytes:
    """Fetch an image from URL and return raw bytes."""
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.content

# ──────────────────────────────────────────────────────────────
def analyze_images_with_vision(gallery_urls, condition, photos_per_item, limit_preview=False):
    """
    Run OpenAI Vision over grouped images.
    Returns list[dict] describing each item.
    """
    groups = [
        gallery_urls[i: i + photos_per_item]
        for i in range(0, len(gallery_urls), photos_per_item)
    ]
    if limit_preview:
        groups = groups[:1]

    results = []
    for group in groups:
        prompt = (
            "Analyze these product images and return a JSON object with:\n"
            "brand, type, color, size, material, pattern, department, title, and category_id.\n"
            "Keep values clean and simple for eBay CSV usage."
        )
        img_parts = [{"type": "image_url", "image_url": {"url": url}} for url in group]
        messages = [{"role": "user", "content": [{"type": "text", "text": prompt}, *img_parts]}]

        resp = client.chat.completions.create(model="gpt-4o", messages=messages, temperature=0)
        text = resp.choices[0].message.content.strip()

        try:
            data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        except Exception:
            data = {"raw_output": text}

        data["photos"] = group
        data["condition"] = condition
        results.append(data)
    return results

# ──────────────────────────────────────────────────────────────
def build_csv_bytes(data):
    """
    Build an in-memory CSV from analyzed data.
    """
    headers = [
        "Action(SiteID=US|Country=US|Currency=USD|Version=1193)",
        "Custom label (SKU)",
        "Category ID",
        "Category name",
        "Title",
        "Condition ID",
        "Item photo URL",
        "Description",
    ]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)

    for item in data:
        row = [
            "Add",
            "",
            item.get("category_id", ""),
            "",
            item.get("title", ""),
            1000 if item.get("condition", "") == "new" else 3000,
            ",".join(item.get("photos", [])),
            json.dumps(item, ensure_ascii=False),
        ]
        writer.writerow(row)

    csv_bytes = io.BytesIO(buf.getvalue().encode("utf-8"))
    filename = f"eBay_export_{datetime.datetime.now().strftime('%Y-%m-%d-%H%M')}.csv"
    return csv_bytes, filename

# ──────────────────────────────────────────────────────────────
def analyze_item(input_arg, condition, photos_per_item, preview=False):
    """
    Compatibility wrapper used by Flask routes.
    Splits input URLs and calls analyze_images_with_vision.
    """
    if not input_arg:
        raise ValueError("No input provided")

    # Split by commas, trim whitespace
    gallery_urls = [u.strip() for u in input_arg.split(",") if u.strip()]
    return analyze_images_with_vision(
        gallery_urls=gallery_urls,
        condition=condition,
        photos_per_item=photos_per_item,
        limit_preview=preview
    )
