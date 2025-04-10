import json
import os
import re
import requests
from flask import Flask, request, jsonify
from openai import OpenAI
from flask_cors import CORS
from dotenv import load_dotenv

app = Flask(__name__)
CORS(app)

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

STATS_FILE = "stats.json"
SMS_COST_PER_FRAGMENT = 0.0225

if not os.path.exists(STATS_FILE):
    with open(STATS_FILE, "w") as f:
        json.dump({"total_sms_shortened": 0, "total_characters_saved": 0, "total_cost_saved": 0.0}, f)

def load_stats():
    with open(STATS_FILE, "r") as f:
        return json.load(f)

def save_stats(stats):
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

def shorten_urls_in_text(text):
    url_pattern = re.compile(r'https?://\S+')
    urls = url_pattern.findall(text)
    shortened_map = {}

    for url in urls:
        if url in shortened_map:
            continue  # Avoid shortening duplicates multiple times
        try:
            res = requests.get(f'https://tinyurl.com/api-create.php?url={url}')
            if res.status_code == 200:
                shortened_map[url] = res.text
            else:
                print(f"[URL Shorten] Failed to shorten {url}. Status: {res.status_code}, Response: {res.text}")
        except Exception as e:
            print(f"[URL Shorten] Exception while shortening {url}: {e}")

    # Replace all URLs in the original text with their shortened version
    def replace_match(match):
        url = match.group(0)
        return shortened_map.get(url, url)

    return url_pattern.sub(replace_match, text)


@app.route('/shorten', methods=['POST'])
def shorten_sms():
    data = request.json
    original_text = data.get("text", "")
    max_chars = int(data.get("max_chars", 160))
    shorten_urls = data.get("shorten_urls", True)
    business_sector = data.get("business_sector", "General")

    if not original_text:
        return jsonify({"error": "No text provided"}), 400

    if shorten_urls:
        original_text = shorten_urls_in_text(original_text)

    url_instruction = "Remove https:// from links but keep the rest intact." if shorten_urls else ""
    sector_instruction = f" Adjust the tone to suit the {business_sector} sector." if business_sector != "General" else ""

    prompt = f"""
You are a precise SMS message shortener. Your task is to shorten the following message to an **absolute maximum of {max_chars} characters**. The shortened message must retain the original meaning and tone (UK English spelling).

- Be as concise as possible.
- If needed, remove manners, filler words, or punctuation.
- If multiple links are included, all must remain in the final message.
- Do NOT exceed {max_chars} characters under any circumstance.
- Provide only the shortened SMS with no extra text or explanation.
- {url_instruction}
- {sector_instruction}
Original message: {original_text}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}],
            max_tokens=max_chars // 4
        )

        shortened_text = response.choices[0].message.content.strip()
        if len(shortened_text) > max_chars:
            shortened_text = shortened_text[:max_chars].rstrip(". ,")
            
        original_length = len(original_text)
        shortened_length = len(shortened_text)
        characters_saved = original_length - shortened_length

        original_sms_count = (original_length // 160) + 1
        new_sms_count = (shortened_length // 160) + 1
        cost_savings = max(0, (original_sms_count - new_sms_count) * SMS_COST_PER_FRAGMENT)

        stats = load_stats()
        stats["total_sms_shortened"] += 1
        stats["total_characters_saved"] += characters_saved
        stats["total_cost_saved"] += cost_savings
        save_stats(stats)

        return jsonify({
            "original_text": original_text,
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


