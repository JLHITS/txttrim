import os
import re
import urllib.parse
import requests
from flask import Flask, request, jsonify
from openai import OpenAI
from flask_cors import CORS
from dotenv import load_dotenv

# --- CONFIG ---
ISGD_API = "https://is.gd/create.php?format=simple&url="

# --- SETUP ---
app = Flask(__name__)
CORS(app)
load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "TxtTrim/1.0"})

# --- HELPERS ---
def _shorten_with_isgd(url: str) -> str | None:
    try:
        # API requires the URL to be encoded
        encoded = urllib.parse.quote_plus(url)
        r = SESSION.get(ISGD_API + encoded, timeout=6)
        if r.status_code == 200 and r.text.startswith("http"):
            return r.text.strip()
    except Exception as e:
        print(f"[is.gd Error] {e}")
    return None

def shorten_urls_in_text(text: str) -> str:
    # Regex to find URLs (http/https)
    # [^\s]+ means "anything that is NOT a space"
    url_pattern = re.compile(r'https?://[^\s]+')
    
    urls = url_pattern.findall(text)
    cache = {}
    
    for u in urls:
        # CLEANUP: Strip common punctuation from the end of the URL
        # This fixes the issue where "https://nhs.uk." fails
        clean_url = u.rstrip(".,;!?")
        
        if clean_url not in cache:
            short = _shorten_with_isgd(clean_url)
            # Map the original (dirty) URL to the new short one
            cache[u] = short if short else u

    # Replace in text
    return url_pattern.sub(lambda m: cache.get(m.group(0), m.group(0)), text)

def _sms_fragments(length: int) -> int:
    return (length + 159) // 160

# --- ROUTES ---
@app.route('/shorten', methods=['POST'])
def shorten_sms():
    data = request.json or {}
    original_text = data.get("text", "")
    max_chars = int(data.get("max_chars", 160))
    do_shorten_urls = bool(data.get("shorten_urls", True))
    business_sector = data.get("business_sector", "General")
    
    # NEW: Check if we should protect variables
    protect_variables = bool(data.get("protect_variables", True))

    if not original_text:
        return jsonify({"error": "No text provided"}), 400

    # 1. Handle URL Shortening
    processed_text = original_text
    if do_shorten_urls:
        processed_text = shorten_urls_in_text(processed_text)

    # 2. Build Instructions
    sector_instruction = (
        f" Adjust the tone to suit the {business_sector} sector."
        if business_sector and business_sector != "General"
        else ""
    )

    # NEW: Protection Instruction
    protection_instruction = ""
    if protect_variables:
        protection_instruction = "- CRITICAL: Do NOT change, delete, or reword any text enclosed in [square brackets], e.g. [Date] or [Patient Name]. Keep them exactly as provided."

    prompt = f"""
You are a precise SMS message shortener. Your task is to shorten the following message to an **absolute maximum of {max_chars} characters**. The shortened message must retain the original meaning and tone (UK English spelling).

- Be as concise as possible.
- If needed, remove manners, filler words, or punctuation.
- If multiple links are included, all must remain in the final message.
- Do NOT exceed {max_chars} characters under any circumstance.
- Provide only the shortened SMS with no extra text or explanation.
{protection_instruction}
-{sector_instruction}
Original message: {processed_text}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}],
            max_tokens=max_chars,
        )

        shortened_text = (response.choices[0].message.content or "").strip()

        # Safety truncate
        if len(shortened_text) > max_chars:
            shortened_text = shortened_text[:max_chars].rstrip(". ,")

        return jsonify({
            "original_text": processed_text,
            "shortened_text": shortened_text,
            "original_length": len(processed_text),
            "shortened_length": len(shortened_text),
            "sms_fragments": _sms_fragments(len(shortened_text))
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))