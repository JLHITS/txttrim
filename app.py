import os
import re
import urllib.parse
import requests
import logging # <--- NEW
import time # <--- NEW
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

# Configure Logging to print to Render Console
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "TxtTrim/1.0"})

# --- HELPERS ---
def _shorten_with_isgd(url: str) -> str | None:
    try:
        encoded = urllib.parse.quote_plus(url)
        r = SESSION.get(ISGD_API + encoded, timeout=6)
        if r.status_code == 200 and r.text.startswith("http"):
            return r.text.strip()
    except Exception as e:
        logger.error(f"[is.gd Error] {e}") # <--- LOG ERROR
    return None

def shorten_urls_in_text(text: str) -> str:
    url_pattern = re.compile(r'https?://[^\s]+')
    urls = url_pattern.findall(text)
    cache = {}
    for u in urls:
        clean_url = u.rstrip(".,;!?")
        if clean_url not in cache:
            short = _shorten_with_isgd(clean_url)
            cache[u] = short if short else u
    return url_pattern.sub(lambda m: cache.get(m.group(0), m.group(0)), text)

def _sms_fragments(length: int) -> int:
    return (length + 159) // 160

# --- ROUTES ---
@app.route('/shorten', methods=['POST'])
def shorten_sms():
    start_time = time.time() # <--- Start Timer
    data = request.json or {}
    original_text = data.get("text", "")
    max_chars = int(data.get("max_chars", 160))
    do_shorten_urls = bool(data.get("shorten_urls", True))
    business_sector = data.get("business_sector", "General")
    protect_variables = bool(data.get("protect_variables", True))
    target_language = data.get("target_language", "English")

    if not original_text:
        return jsonify({"error": "No text provided"}), 400

    # Log the attempt (Privacy safe: don't log the actual PII text)
    logger.info(f"Processing: Lang={target_language} | Sector={business_sector} | Length={len(original_text)}")

    processed_text = original_text
    if do_shorten_urls:
        processed_text = shorten_urls_in_text(processed_text)

    # --- PROMPT ENGINEERING ---
    role = "You are a precise SMS message shortener and translator."
    
    protection = ""
    if protect_variables:
        protection = "CRITICAL: Do NOT change, delete, or translate any text inside [square brackets] (e.g. [Date]). Keep them exactly as is."

    if target_language and target_language != "English":
        task = f"Task: Translate the message to {target_language} FIRST, and THEN shorten the translated text to under {max_chars} characters."
    else:
        task = f"Task: Shorten the message to under {max_chars} characters in English."

    prompt = f"""
    {role}
    {task}
    
    Rules:
    - Maintain the original meaning.
    - Tone: {business_sector}.
    - {protection}
    - If multiple links exist, keep them all.
    - Provide ONLY the final SMS text. No intro/outro.
    
    Message to process: {processed_text}
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}],
            max_tokens=max_chars + 100, 
        )

        shortened_text = (response.choices[0].message.content or "").strip()

        if target_language == "English" and len(shortened_text) > max_chars:
            shortened_text = shortened_text[:max_chars].rstrip(". ,")

        # Log Success
        duration = round(time.time() - start_time, 2)
        logger.info(f"Success: {duration}s | Old:{len(original_text)} -> New:{len(shortened_text)} | Tokens: {response.usage.total_tokens}")

        return jsonify({
            "original_text": processed_text,
            "shortened_text": shortened_text,
            "original_length": len(processed_text),
            "shortened_length": len(shortened_text),
            "sms_fragments": _sms_fragments(len(shortened_text))
        })

    except Exception as e:
        logger.error(f"AI Error: {str(e)}") # <--- LOG ERROR
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))