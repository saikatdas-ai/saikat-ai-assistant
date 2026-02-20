import os
import sys
import json
import time
import logging
import feedparser
import difflib
import threading
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

if GEMINI_KEY:
    try:
        genai.configure(api_key=GEMINI_KEY)
    except Exception as e:
        logging.warning("Gemini config failed: %s" % e)

# ==========================================================
# 2. STORAGE (ATOMIC SAFE)
# ==========================================================

DATA_DIR = "/app/data"
os.makedirs(DATA_DIR, exist_ok=True)

SEEN_FILE = os.path.join(DATA_DIR, "seen_links.json")
SIGNATURE_FILE = os.path.join(DATA_DIR, "league_signatures.json")
FOLLOW_FILE = os.path.join(DATA_DIR, "followups.json")

def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except Exception as e:
        logging.error("Read error %s: %s" % (path, e))
    return default

def save_json_atomic(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception as e:
        logging.error("Write error %s: %s" % (path, e))

# ==========================================================
# 3. FILTER ENGINE
# ==========================================================

STRUCTURE_WORDS = [
    "league", "premier league", "t20", "t10",
    "pro league", "championship"
]

LIFECYCLE_WORDS = [
    "launch", "announces", "inaugural", "season",
    "expansion", "auction", "draft", "investment",
    "ownership", "broadcast", "sponsor",
    "franchise model"
]

NOISE_WORDS = [
    " vs ", "beat", "preview", "match",
    "score", "franchise tag", "player signs",
    "contract extension"
]

INDIA_WORDS = [
    "india", "mumbai", "delhi", "kolkata",
    "hyderabad", "chennai", "pune", "bengal",
    "punjab", "up", "uttar pradesh"
]

CRICKET_WORDS = [
    "t20", "t10", "cricket"
]

INVESTMENT_WORDS = [
    "investment", "ownership", "broadcast", "sponsor"
]

def passes_structure(title):
    t = title.lower()
    return any(word in t for word in STRUCTURE_WORDS)

def passes_lifecycle(title):
    t = title.lower()
    return any(word in t for word in LIFECYCLE_WORDS)

def is_noise(title):
    t = title.lower()
    return any(word in t for word in NOISE_WORDS)

def calculate_score(title):
    t = title.lower()
    score = 50

    if any(word in t for word in INDIA_WORDS):
        score += 30

    if any(word in t for word in CRICKET_WORDS):
        score += 20

    if any(word in t for word in INVESTMENT_WORDS):
        score += 15

    return min(score, 100)

# ==========================================================
# 4. ENTITY EXTRACTION + SIGNATURE DEDUPE
# ==========================================================

def extract_league_name(title):
    words = title.split()
    for i in range(len(words)):
        if words[i].lower() == "league":
            return " ".join(words[max(0, i-3):i+1])
    return title[:40]

def is_duplicate_signature(league_name):
    signatures = load_json(SIGNATURE_FILE, {})
    if league_name in signatures:
        return True
    signatures[league_name] = datetime.utcnow().isoformat()
    save_json_atomic(SIGNATURE_FILE, signatures)
    return False

# ==========================================================
# 5. FETCH ENGINE
# ==========================================================

def fetch_sports(days=2):
    past_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    queries = [
        f'franchise league launch after:{past_date}',
        f'premier league expansion after:{past_date}',
        f't20 league season after:{past_date}',
        f'league investment after:{past_date}',
        f'state t20 league after:{past_date}'
    ]

    seen_links = {item["link"] for item in load_json(SEEN_FILE, []) if isinstance(item, dict)}
    new_seen = []
    tier_a, tier_b, tier_c = [], [], []
    scanned = 0

    for q in queries:
        try:
            url = f"https://news.google.com/rss/search?q={quote(q)}&hl=en-IN&gl=IN&ceid=IN:en"
            feed = feedparser.parse(url)

            for entry in feed.entries[:20]:
                scanned += 1
                if entry.link in seen_links:
                    continue

                title = entry.title

                if not passes_structure(title):
                    continue
                if not passes_lifecycle(title):
                    continue
                if is_noise(title):
                    continue

                league_name = extract_league_name(title)
                if is_duplicate_signature(league_name):
                    continue

                score = calculate_score(title)

                item = {
                    "title": title,
                    "link": entry.link,
                    "score": score
                }

                if score >= 85:
                    tier_a.append(item)
                elif score >= 70:
                    tier_b.append(item)
                elif score >= 55:
                    tier_c.append(item)

                new_seen.append({"link": entry.link, "date": datetime.utcnow().isoformat()})

        except Exception as e:
            logging.error("Feed error: %s" % e)

    if new_seen:
        existing = load_json(SEEN_FILE, [])
        save_json_atomic(SEEN_FILE, existing + new_seen)

    return tier_a, tier_b, tier_c, scanned

# ==========================================================
# 6. COMMANDS
# ==========================================================

@bot.message_handler(commands=["leads-sports"])
def leads_sports(message):
    if message.from_user.id != ADMIN_ID:
        return

    bot.send_message(message.chat.id,
        "Sports Radar running. Checking 48h + 7d buffer. Estimated 10-20 seconds."
    )

    tier_a, tier_b, tier_c, scanned = fetch_sports(days=2)

    report = f"Scanned: {scanned}\n\n"

    def format_tier(name, data):
        if not data:
            return f"{name}: None\n\n"
        block = f"{name} ({len(data)})\n"
        for item in data:
            block += f"({item['score']}) {item['title']}\n{item['link']}\n\n"
        return block

    report += format_tier("Tier A", tier_a)
    report += format_tier("Tier B", tier_b)
    report += format_tier("Tier C", tier_c)

    bot.send_message(message.chat.id, report)

@bot.message_handler(commands=["bootstrap-archive"])
def bootstrap_archive(message):
    if message.from_user.id != ADMIN_ID:
        return

    bot.send_message(message.chat.id,
        "Running 90-day archive scan. This may take 20-40 seconds."
    )

    tier_a, tier_b, tier_c, scanned = fetch_sports(days=90)

    report = f"90 Day Scan Completed\nScanned: {scanned}\n\n"

    report += f"Tier A: {len(tier_a)}\n"
    report += f"Tier B: {len(tier_b)}\n"
    report += f"Tier C: {len(tier_c)}\n"

    bot.send_message(message.chat.id, report)

# ==========================================================
# 7. SELF-HEALING RUNNER
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
