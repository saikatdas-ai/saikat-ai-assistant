import os
import sys
import json
import time
import logging
import feedparser
from urllib.parse import quote
from datetime import datetime, timedelta
import telebot
import google.generativeai as genai

# ==========================================================
# 1. SAFE CONFIG
# ==========================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_USER_ID")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

if not BOT_TOKEN or not ADMIN_ID:
    logging.critical("Missing TELEGRAM_TOKEN or ADMIN_USER_ID")
    sys.exit(1)

try:
    ADMIN_ID = int(ADMIN_ID)
except:
    logging.critical("ADMIN_USER_ID must be integer")
    sys.exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

GEMINI_ENABLED = False
model = None

if GEMINI_KEY:
    try:
        genai.configure(api_key=GEMINI_KEY)
        models = genai.list_models()
        candidates = [m.name for m in models if "generateContent" in m.supported_generation_methods]
        if candidates:
            model = genai.GenerativeModel(candidates[0])
            GEMINI_ENABLED = True
    except Exception as e:
        logging.warning("Gemini init failed: %s" % e)

# ==========================================================
# 2. STORAGE
# ==========================================================

DATA_DIR = "/app/data"
os.makedirs(DATA_DIR, exist_ok=True)

SEEN_FILE = os.path.join(DATA_DIR, "seen_links.json")
SIGNATURE_FILE = os.path.join(DATA_DIR, "league_signatures.json")

def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except:
        pass
    return default

def save_json_atomic(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception as e:
        logging.error("Write error: %s" % e)

# ==========================================================
# 3. FILTER GATES
# ==========================================================

STRUCTURE_WORDS = [
    "league", "premier league", "t20", "t10", "championship"
]

LIFECYCLE_WORDS = [
    "launch", "announces", "inaugural", "season",
    "expansion", "auction", "draft", "investment",
    "ownership", "broadcast", "sponsor", "franchise model"
]

NOISE_WORDS = [
    " vs ", "beat", "preview", "match",
    "score", "franchise tag", "contract extension"
]

COMMERCIAL_SCALE_WORDS = [
    "6 teams", "8 teams", "10 teams", "12 teams",
    "six-team", "eight-team",
    "city-based", "multi-team", "auction", "draft",
    "broadcast partner", "title sponsor",
    "ownership group", "media rights"
]

INDIA_WORDS = [
    "india", "mumbai", "delhi", "kolkata",
    "hyderabad", "chennai", "pune", "punjab",
    "uttar pradesh", "up"
]

CRICKET_WORDS = ["t20", "t10", "cricket"]

def passes_structure(text):
    t = text.lower()
    return any(w in t for w in STRUCTURE_WORDS)

def passes_lifecycle(text):
    t = text.lower()
    return any(w in t for w in LIFECYCLE_WORDS)

def is_noise(text):
    t = text.lower()
    return any(w in t for w in NOISE_WORDS)

def passes_commercial_scale(text):
    t = text.lower()
    return any(w in t for w in COMMERCIAL_SCALE_WORDS)

def calculate_score(text):
    t = text.lower()
    score = 50
    if any(w in t for w in INDIA_WORDS):
        score += 30
    if any(w in t for w in CRICKET_WORDS):
        score += 20
    return min(score, 100)

# ==========================================================
# 4. ENTITY SIGNATURE
# ==========================================================

def extract_league_name(title):
    words = title.split()
    for i in range(len(words)):
        if words[i].lower() == "league":
            return " ".join(words[max(0, i-3):i+1])
    return title[:50]

def is_duplicate_signature(name):
    db = load_json(SIGNATURE_FILE, {})
    if name in db:
        return True
    db[name] = datetime.utcnow().isoformat()
    save_json_atomic(SIGNATURE_FILE, db)
    return False

# ==========================================================
# 5. GEMINI INTELLIGENCE
# ==========================================================

def generate_intelligence(title, link):
    if not GEMINI_ENABLED or not model:
        return "Why It Matters: Commercial franchise league movement.\nSuggested Action: Identify promoter and media head."

    prompt = f"""
    You are a sports business intelligence assistant.
    Analyze this headline and produce structured output:

    Headline: {title}

    Provide:
    League:
    Country:
    Sport:
    Stage:
    Commercial Signal:
    Why It Matters:
    Suggested Action:

    Keep it concise.
    """

    try:
        response = model.generate_content(prompt, request_options={"timeout": 20})
        return response.text.strip()
    except:
        return "Why It Matters: Commercial franchise league movement.\nSuggested Action: Identify promoter and media head."

# ==========================================================
# 6. FETCH ENGINE
# ==========================================================

def fetch_sports(days):
    past_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    queries = [
        f'franchise league launch after:{past_date}',
        f't20 league expansion after:{past_date}',
        f'premier league auction after:{past_date}',
        f'league investment after:{past_date}'
    ]

    seen = {i["link"] for i in load_json(SEEN_FILE, []) if isinstance(i, dict)}
    new_seen = []
    tier_a, tier_b, tier_c = [], [], []
    scanned = 0

    for q in queries:
        try:
            url = f"https://news.google.com/rss/search?q={quote(q)}&hl=en-IN&gl=IN&ceid=IN:en"
            feed = feedparser.parse(url)

            for entry in feed.entries[:25]:
                scanned += 1
                if entry.link in seen:
                    continue

                text = entry.title + " " + getattr(entry, "summary", "")

                if not passes_structure(text):
                    continue
                if not passes_lifecycle(text):
                    continue
                if is_noise(text):
                    continue
                if not passes_commercial_scale(text):
                    continue

                league_name = extract_league_name(entry.title)
                if is_duplicate_signature(league_name):
                    continue

                score = calculate_score(text)

                item = {
                    "title": entry.title,
                    "link": entry.link,
                    "score": score
                }

                if score >= 85:
                    tier_a.append(item)
                elif score >= 70:
                    tier_b.append(item)
                elif score >= 55:
                    tier_c.append(item)

                new_seen.append({
                    "link": entry.link,
                    "date": datetime.utcnow().isoformat()
                })

        except Exception as e:
            logging.error("Feed error: %s" % e)

    if new_seen:
        existing = load_json(SEEN_FILE, [])
        save_json_atomic(SEEN_FILE, existing + new_seen)

    return tier_a, tier_b, tier_c, scanned

# ==========================================================
# 7. COMMANDS
# ==========================================================

@bot.message_handler(commands=["leads-sports"])
def leads_sports(message):
    if message.from_user.id != ADMIN_ID:
        return

    bot.send_message(
        message.chat.id,
        "Sports Radar running. Checking 48h + 7d buffer. Estimated 15-30 seconds."
    )

    tier_a, tier_b, tier_c, scanned = fetch_sports(days=2)

    report = f"Scanned: {scanned}\n\n"

    def format_tier(name, data):
        if not data:
            return f"{name}: None\n\n"
        block = f"{name} ({len(data)})\n\n"
        for item in data:
            intelligence = generate_intelligence(item["title"], item["link"])
            block += f"{intelligence}\nLink: {item['link']}\n\n"
        return block

    report += format_tier("Tier A", tier_a)
    report += format_tier("Tier B", tier_b)
    report += format_tier("Tier C", tier_c)

    bot.send_message(message.chat.id, report)

@bot.message_handler(commands=["bootstrap-archive"])
def bootstrap_archive(message):
    if message.from_user.id != ADMIN_ID:
        return

    bot.send_message(
        message.chat.id,
        "Running 90-day archive scan. This may take 30-60 seconds."
    )

    tier_a, tier_b, tier_c, scanned = fetch_sports(days=90)

    report = f"90 Day Scan Completed\nScanned: {scanned}\n\n"
    report += f"Tier A: {len(tier_a)}\n"
    report += f"Tier B: {len(tier_b)}\n"
    report += f"Tier C: {len(tier_c)}\n"

    bot.send_message(message.chat.id, report)

# ==========================================================
# 8. SELF HEALING RUNNER
# ==========================================================

if __name__ == "__main__":
    delay = 5
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=10)
            delay = 5
        except Exception as e:
            logging.error("Crash detected: %s" % e)
            time.sleep(delay)
            delay = min(delay * 2, 300)
