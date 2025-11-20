import json
import os
import re
import time
import urllib.parse
import requests
from flask import Flask, request, jsonify
from openai import OpenAI
from flask_cors import CORS
from dotenv import load_dotenv

# -----------------------------
# Config / constants
# -----------------------------
ISGD_API = "https://is.gd/create.php?format=simple&url="
SMS_COST_PER_FRAGMENT = 0.0225  # £ per 160-char fragment
STATS_FILE = "stats.json"

# Optional: remove https:// from short domains to save chars
REMOVE_SCHEME_FOR_SHORTENERS = False
SHORTENER_HOSTS = (r"is\.gd",)

# HTTP session (keep-alive + UA)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "TxtTrim/1.0"})

# -----------------------------
# Flask app
# -----------------------------
app = Flask(__name__)
CORS(app)

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# -----------------------------
# Stats file bootstrap
# -----------------------------
if not os.path.exists(STATS_FILE):
    with open(STATS_FILE, "w") as f:
        json.dump(
            {"total_sms_shortened": 0, "total_characters_saved": 0, "total_cost_saved": 0.0},
            f,
        )

def load_stats():
    with open(STATS_FILE, "r") as f:
        return json.load(f)

def save_stats(stats):
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

# -----------------------------
# URL Shortening (is.gd only)
# -----------------------------
def _shorten_with_isgd(url: str) -> str | None:
    """Shorten using is.gd (no auth)."""
    try:
        encoded = urllib.parse.quote_plus(url)
        r = SESSION.get(ISGD_API + encoded, timeout=6)
        if r.status_code == 200 and r.text.startswith("http"):
            return r.text.strip()
        else:
            print(f"[is.gd] HTTP {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        print(f"[is.gd] Exception: {e}")
        return None

def shorten_single_url(url: str) -> str:
    """Shorten a single URL via is.gd; if it fails, return original."""
    short = _shorten_with_isgd(url)
    return short or url

def shorten_urls_in_text(text: str) -> str:
    """Find http/https URLs and replace with shortened ones (deduped)."""
    url_pattern = re.compile(r'https?://\S+')
    urls = url_pattern.findall(text)
    cache: dict[str, str] = {}

    for u in urls:
        if u not in cache:
            cache[u] = shorten_single_url(u)

    def repl(m):
        u = m.group(0)
        return cache.get(u, u)

    return url_pattern.sub(repl, text)

def strip_scheme_from_shorteners(text: str) -> str:
    """
    Optionally strip 'https://' ONLY for trusted shortener domains (saves chars).
    Keeps links readable like 'is.gd/abcd'.
    """
    if not REMOVE_SCHEME_FOR_SHORTENERS:
        return text
    host_regex = "|".join(SHORTENER_HOSTS)
    pattern = re.compile(rf'https?://(?:{host_regex})/', re.IGNORECASE)
    return pattern.sub(lambda m: m.group(0).split("://", 1)[1], text)

# -----------------------------
# SMS helpers
# -----------------------------
def _sms_fragments(length: int, frag_size: int = 160) -> int:
    """Ceiling division without overcounting exact multiples."""
    return (length + frag_size - 1) // frag_size

# -----------------------------
# Route
# -----------------------------
@app.route('/shorten', methods=['POST'])
def shorten_sms():
    data = request.json or {}
    original_text = data.get("text", "")
    max_chars = int(data.get("max_chars", 160))
    do_shorten_urls = bool(data.get("shorten_urls", True))
    business_sector = data.get("business_sector", "General")

    if not original_text:
        return jsonify({"error": "No text provided"}), 400

    # Shorten URLs first (so the model works with the final links)
    processed_text = original_text
    if do_shorten_urls:
        processed_text = shorten_urls_in_text(processed_text)
        if REMOVE_SCHEME_FOR_SHORTENERS:
            processed_text = strip_scheme_from_shorteners(processed_text)

    # Prompt building (scheme stripping already applied above)
    sector_instruction = (
        f" Adjust the tone to suit the {business_sector} sector."
        if business_sector and business_sector != "General"
        else ""
    )

    prompt = f"""
You are a precise SMS message shortener. Your task is to shorten the following message to an **absolute maximum of {max_chars} characters**. The shortened message must retain the original meaning and tone (UK English spelling).

- Be as concise as possible.
- If needed, remove manners, filler words, or punctuation.
- If multiple links are included, all must remain in the final message.
- Do NOT exceed {max_chars} characters under any circumstance.
- Provide only the shortened SMS with no extra text or explanation.
-{sector_instruction}
Original message: {processed_text}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}],
            max_tokens=max(16, max_chars // 3),
        )

        shortened_text = (response.choices[0].message.content or "").strip()

        # Hard cap (safety guard)
        if len(shortened_text) > max_chars:
            shortened_text = shortened_text[:max_chars].rstrip(". ,")

        original_length = len(processed_text)
        shortened_length = len(shortened_text)
        characters_saved = max(0, original_length - shortened_length)

        original_sms_count = _sms_fragments(original_length, 160)
        new_sms_count = _sms_fragments(shortened_length, 160)
        cost_savings = max(0.0, (original_sms_count - new_sms_count) * SMS_COST_PER_FRAGMENT)

        # Persist stats
        stats = load_stats()
        stats["total_sms_shortened"] += 1
        stats["total_characters_saved"] += characters_saved
        stats["total_cost_saved"] += cost_savings
        save_stats(stats)

        return jsonify({
            "original_text": processed_text,         # after shortening URLs (what the model actually saw)
            "shortened_text": shortened_text,
            "original_length": original_length,
            "shortened_length": shortened_length,
            "characters_saved": characters_saved,
            "cost_savings": round(cost_savings, 2),
            "new_sms_count": new_sms_count
        })

    except Exception as e:
        print(f"[OpenAI Error] {e}")
        return jsonify({"error": str(e)}), 500


# -----------------------------
# Simple health route (is.gd only)
# -----------------------------
@app.route('/health', methods=['GET'])
def health():
    target = "https://example.org/?q=txttrim"
    isgd_ok = bool(_shorten_with_isgd(target))
    return jsonify({"isgd": isgd_ok}), (200 if isgd_ok else 503)


if __name__ == "__main__":
    # Useful if you run it directly
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
