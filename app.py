import json
import os
from flask import Flask, request, jsonify
from openai import OpenAI
from flask_cors import CORS
from dotenv import load_dotenv

app = Flask(__name__)
CORS(app)  # Allow requests from frontend

load_dotenv()  # Load environment variables from .env file

# Initialize OpenAI client (Remove 'project')
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))  # Get API key from environment variable

STATS_FILE = "stats.json"

# SMS Pricing
SMS_COST_PER_FRAGMENT = 0.0225  # 2.25 pence per fragment

# Ensure stats.json exists
if not os.path.exists(STATS_FILE):
    with open(STATS_FILE, "w") as f:
        json.dump({"total_sms_shortened": 0, "total_characters_saved": 0, "total_cost_saved": 0.0}, f)

# Load stats from file
def load_stats():
    with open(STATS_FILE, "r") as f:
        return json.load(f)

# Save stats to file
def save_stats(stats):
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

@app.route('/shorten', methods=['POST'])
def shorten_sms():
    data = request.json
    original_text = data.get("text", "")
    max_chars = int(data.get("max_chars", 160))

    if not original_text:
        return jsonify({"error": "No text provided"}), 400

    # OpenAI API call to shorten message using GPT-4o-mini
    prompt = f"""Shorten this SMS message to an explicit maximum of {max_chars} characters whilst keeping the meaning. Use UK English spelling.
                 Only if you must, remove unnecessary punctuation, spacing and manners to acheive the maximum limmit specified. 
                 Provide only the shortened SMS in your response. 
                 Original message: {original_text}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}],
            max_tokens=max_chars // 4  # Ensure response stays short
        )

        shortened_text = response.choices[0].message.content.strip()
        original_length = len(original_text)
        shortened_length = len(shortened_text)
        characters_saved = original_length - shortened_length  # ✅ Fixed missing variable

        # Calculate cost savings
        original_sms_count = (original_length // 160) + 1
        new_sms_count = (shortened_length // 160) + 1
        cost_savings = (original_sms_count - new_sms_count) * SMS_COST_PER_FRAGMENT

        # Load and update stats
        stats = load_stats()
        stats["total_sms_shortened"] += 1
        stats["total_characters_saved"] += characters_saved
        stats["total_cost_saved"] += cost_savings

        save_stats(stats)  # Save updated stats

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
        return jsonify({"error": str(e)}), 500
        
@app.route('/stats', methods=['GET'])
def get_stats():
    stats = load_stats()
    return jsonify(stats)

if __name__ == '__main__':
    app.run(debug=True)
