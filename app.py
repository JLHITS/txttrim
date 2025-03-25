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
    for url in urls:
        try:
            res = requests.get(f'https://tinyurl.com/api-create.php?url={url}')
            if res.status_code == 200:
                text = text.replace(url, res.text)
            else:
                print(f"[URL Shorten] Failed to shorten {url}. Status: {res.status_code}, Response: {res.text}")
        except Exception as e:
            print(f"[URL Shorten] Exception while shortening {url}: {e}")
    return text


@app.route('/shorten', methods=['POST'])
def shorten_sms():
    data = request.json
    original_text = data.get("text", "")
    max_chars = int(data.get("max_chars", 160))
    shorten_urls = data.get("shorten_urls", True)

    if not original_text:
        return jsonify({"error": "No text provided"}), 400

    if shorten_urls:
        original_text = shorten_urls_in_text(original_text)

    prompt = f"""Shorten this SMS message to an explicit maximum of {max_chars} characters whilst keeping the meaning. Use UK English spelling.
                 Only if you must, remove unnecessary punctuation, spacing and manners to acheive the maximum limit specified. 
                 Provide only the shortened SMS in your response. 
                 Original message: {original_text}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}],
            max_tokens=max_chars // 4
        )

        shortened_text = response.choices[0].message.content.strip()
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


