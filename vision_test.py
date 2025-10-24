import os
import io
import csv
import re
import json
import datetime
from statistics import median
from urllib.parse import urlparse

import requests
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ──────────────────────────────────────────────────────────────
IMG_EXT_RE = re.compile(r"\.(?:jpg|jpeg|png|webp|gif)$", re.I)

def fetch_image_bytes(url: str) -> bytes:
    """Fetch an image from URL and return raw bytes."""
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.content

# ──────────────────────────────────────────────────────────────
def expand_postimg_gallery(url: str) -> list[str]:
    """
    Given a postimg.cc gallery URL, return a list of direct image URLs (.jpg/.png/.webp/.gif).
    Example gallery:
      https://postimg.cc/gallery/XXXXXX
    We'll scrape the HTML for https://i.postimg.cc/.../(file).(ext)
    """
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        html = r.text

        # Find direct image URLs hosted on i.postimg.cc with common image extensions
        imgs = re.findall(
            r"https://i\.postimg\.cc/[A-Za-z0-9_\-\/\.]+?\.(?:jpg|jpeg|png|webp|gif)",
            html,
        )
        # De-dupe (preserve order) and cap to a reasonable amount
        unique_imgs = list(dict.fromkeys(imgs))[:500]
        print(f"🖼️ Extracted {len(unique_imgs)} images from Postimg gallery: {url}")
        return unique_imgs
    except Exception as e:
        print(f"⚠️ Failed to expand Postimg gallery {url}: {e}")
        return []

def normalize_to_image_urls(raw_list: list[str]) -> list[str]:
    """
    Take a list of raw inputs (may include gallery links) and return only direct image URLs.
    - Expands postimg.cc gallery links
    - Keeps URLs that already look like direct images (ending with image extensions)
    - Filters out non-image/unknown links
    """
    out: list[str] = []
    for raw in raw_list:
        if not raw:
            continue
        u = raw.strip()

        # Expand Postimg gallery pages
        if "postimg.cc/gallery/" in u:
            out.extend(expand_postimg_gallery(u))
            continue

        # Accept direct image URLs
        if IMG_EXT_RE.search(u):
            out.append(u)
            continue

        # Optional: could HEAD-check content-type here
    # De-dupe while preserving order
    return list(dict.fromkeys(out))

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
        if not group:
            continue

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
    Supports automatic Postimg gallery expansion.
    """
    if not input_arg:
        raise ValueError("No input provided")

    raw_inputs = [u.strip() for u in input_arg.split(",") if u.strip()]
    gallery_urls = normalize_to_image_urls(raw_inputs)

    print(f"📦 {len(gallery_urls)} total image URLs ready for analysis.")
    if not gallery_urls:
        raise ValueError("No valid image URLs found after normalization")

    return analyze_images_with_vision(
        gallery_urls=gallery_urls,
        condition=condition,
        photos_per_item=photos_per_item,
        limit_preview=preview,
    )
