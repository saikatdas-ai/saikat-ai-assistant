import os
import time
import json
import logging
import threading
from datetime import datetime, timedelta, date
from urllib.parse import quote

import telebot
import feedparser

# ==========================================================
# 1. BOOT SAFETY
# ==========================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_USER_ID")

if not BOT_TOKEN or not ADMIN_ID:
    logging.error("Missing TELEGRAM_TOKEN or ADMIN_USER_ID")
    while True:
        time.sleep(60)

try:
    ADMIN_ID = int(ADMIN_ID)
except:
    logging.error("ADMIN_USER_ID must be integer")
    while True:
        time.sleep(60)

bot = telebot.TeleBot(BOT_TOKEN)

# ==========================================================
# 2. STORAGE
# ==========================================================

DATA_DIR = "/app/data"
os.makedirs(DATA_DIR, exist_ok=True)

SEEN_FILE = os.path.join(DATA_DIR, "sports_seen.json")
SIGNATURE_FILE = os.path.join(DATA_DIR, "sports_signatures.json")

def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except:
        pass
    return default

def save_json_atomic(path, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception as e:
        logging.error(f"Write error: {e}")

# ==========================================================
# 3. HARD FILTERS
# ==========================================================

EXCLUDE_KEYWORDS = [
    "live score", "vs", "defeats", "beats",
    "odi", "test match", "t20i",
    "icc", "world cup", "championship",
    "squad", "injury", "rankings",
    "preview", "match report",
    "biography", "profile"
]

FRANCHISE_REQUIRED = [
    "franchise", "city-based", "season 1",
    "inaugural", "auction", "draft",
    "expansion", "adds teams",
    "title sponsor", "media rights",
    "private equity", "valuation",
    "to debut", "new league"
]

CRICKET_KEYWORDS = [
    "t20 league", "t10 league",
    "premier league cricket",
    "global t20", "major league cricket",
    "sa20", "ilt20"
]

EMERGING_KEYWORDS = [
    "rugby league", "football league",
    "kabaddi league", "golf league",
    "tennis league"
]

# ==========================================================
# 4. SIGNATURE DEDUPE
# ==========================================================

def generate_signature(title):
    words = [w.lower() for w in title.split() if len(w) > 4]
    return "_".join(sorted(words[:5]))

def is_duplicate(signature):
    db = load_json(SIGNATURE_FILE, {})
    return signature in db

def register_signature(signature):
    db = load_json(SIGNATURE_FILE, {})
    db[signature] = datetime.utcnow().isoformat()
    save_json_atomic(SIGNATURE_FILE, db)

# ==========================================================
# 5. LIFECYCLE CLASSIFIER
# ==========================================================

def classify_tier(title):
    t = title.lower()

    if any(x in t for x in ["inaugural", "season 1", "to debut", "new league"]):
        return "A"

    if any(x in t for x in ["season 2", "expands", "adds teams", "auction", "draft"]):
        return "A-"

    if any(x in t for x in ["title sponsor", "private equity", "valuation", "media rights"]):
        return "B"

    return "C"

# ==========================================================
# 6. SPORTS FETCH ENGINE
# ==========================================================

def build_queries(days):
    if days == 2:
        date_filter = "when:2d"
    elif days == 7:
        date_filter = "when:7d"
    else:
        past_date = (date.today() - timedelta(days=90)).strftime("%Y-%m-%d")
        date_filter = f"after:{past_date}"

    queries = [
        f"franchise cricket league {date_filter}",
        f"new t20 league franchise {date_filter}",
        f"city-based cricket league {date_filter}",
        f"franchise rugby league {date_filter}",
        f"franchise football league {date_filter}"
    ]

    return queries

def fetch_sports(days=2):
    seen_links = {i["link"] for i in load_json(SEEN_FILE, []) if isinstance(i, dict)}
    new_links = []
    results = []

    queries = build_queries(days)

    for q in queries:
        try:
            url = f"https://news.google.com/rss/search?q={quote(q)}&hl=en-US&gl=US&ceid=US:en"
            feed = feedparser.parse(url)

            for e in feed.entries[:30]:

                title = e.title

                if any(x in title.lower() for x in EXCLUDE_KEYWORDS):
                    continue

                if not any(x in title.lower() for x in FRANCHISE_REQUIRED):
                    continue

                if not (
                    any(x in title.lower() for x in CRICKET_KEYWORDS)
                    or any(x in title.lower() for x in EMERGING_KEYWORDS)
                ):
                    continue

                if e.link in seen_links:
                    continue

                signature = generate_signature(title)
                if is_duplicate(signature):
                    continue

                tier = classify_tier(title)

                results.append({
                    "title": title,
                    "link": e.link,
                    "tier": tier
                })

                register_signature(signature)
                new_links.append({"link": e.link, "date": datetime.utcnow().isoformat()})

        except:
            continue

    if new_links:
        existing = load_json(SEEN_FILE, [])
        save_json_atomic(SEEN_FILE, existing + new_links)

    return results

# ==========================================================
# 7. REPORT FORMATTER
# ==========================================================

def send_grouped_report(chat_id, leads):

    if not leads:
        bot.send_message(chat_id, "SPORTS RADAR\nNo franchise league signals detected.")
        return

    tiers = {"A": [], "A-": [], "B": [], "C": []}

    for l in leads:
        tiers[l["tier"]].append(l)

    message = "SPORTS RADAR - FRANCHISE LEAGUES ONLY\n\n"

    for key in ["A", "A-", "B", "C"]:
        if tiers[key]:
            message += f"Tier {key} ({len(tiers[key])})\n"
            for i, item in enumerate(tiers[key], 1):
                message += f"{i}) {item['title']}\n"
            message += "\n"

    bot.send_message(chat_id, message)

# ==========================================================
# 8. COMMANDS
# ==========================================================

@bot.message_handler(commands=["leads-sports"])
def leads_sports(m):
    if m.from_user.id != ADMIN_ID:
        return

    bot.send_message(m.chat.id, "Scanning franchise league radar (48h + 7d buffer)...")

    fresh = fetch_sports(days=2)
    buffer = fetch_sports(days=7)

    combined = fresh + buffer

    send_grouped_report(m.chat.id, combined)

@bot.message_handler(commands=["bootstrap-archive"])
def bootstrap_archive(m):
    if m.from_user.id != ADMIN_ID:
        return

    bot.send_message(m.chat.id, "Archive bootstrap started. Estimated time 60-120 seconds.")

    def run():
        archive = fetch_sports(days=90)
        send_grouped_report(m.chat.id, archive)

    threading.Thread(target=run).start()

# ==========================================================
# 9. RUNNER
# ==========================================================

if __name__ == "__main__":
    delay = 5
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=10)
            delay = 5
        except Exception as e:
            logging.error(f"Crash: {e}")
            time.sleep(delay)
            delay = min(delay * 2, 300)
