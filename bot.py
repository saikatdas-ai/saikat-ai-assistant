import os
import sys
import time
import json
import logging
import threading
import difflib
from datetime import datetime, timedelta, date
from urllib.parse import quote

import telebot
import google.generativeai as genai
import feedparser
import schedule
import pytz

# ==========================================================
# 1. BOOT + ENV SAFETY
# ==========================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
ADMIN_ID = os.environ.get("ADMIN_USER_ID")

if not BOT_TOKEN or not GEMINI_KEY or not ADMIN_ID:
    logging.error("Missing TELEGRAM_TOKEN / GEMINI_API_KEY / ADMIN_USER_ID")
    while True:
        time.sleep(60)

try:
    ADMIN_ID = int(ADMIN_ID)
except Exception:
    logging.error("ADMIN_USER_ID must be integer")
    while True:
        time.sleep(60)

bot = telebot.TeleBot(BOT_TOKEN)
genai.configure(api_key=GEMINI_KEY)

# ==========================================================
# 2. GEMINI AUTO MODEL SELECTION
# ==========================================================

def select_best_model():
    try:
        models = genai.list_models()
        candidates = [m.name for m in models if "generateContent" in m.supported_generation_methods]
        priority = [
            "models/gemini-2.5-flash",
            "models/gemini-2.0-flash",
            "models/gemini-2.0-pro",
            "models/gemini-1.5-flash"
        ]
        for p in priority:
            if p in candidates:
                logging.info(f"Using Gemini model: {p}")
                return p
        if candidates:
            return candidates[0]
    except Exception as e:
        logging.error(f"Model selection error: {e}")
    return "models/gemini-1.5-flash"

model = genai.GenerativeModel(select_best_model())

# ==========================================================
# 3. STORAGE (ATOMIC)
# ==========================================================

DATA_DIR = "/app/data"
os.makedirs(DATA_DIR, exist_ok=True)

SPORTS_SEEN = os.path.join(DATA_DIR, "sports_seen.json")
ADS_SEEN = os.path.join(DATA_DIR, "ads_seen.json")
SIGNATURES = os.path.join(DATA_DIR, "signatures.json")
FOLLOWUPS = os.path.join(DATA_DIR, "followups.json")
STATE = os.path.join(DATA_DIR, "state.json")

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
        logging.error(f"Write error {path}: {e}")

# ==========================================================
# 4. SIGNATURE DEDUPE
# ==========================================================

STOPWORDS = {"india","league","season","announces","launch","campaign","sports","tournament"}

def generate_signature(title):
    words = [w.lower() for w in title.split() if len(w) > 3 and w.lower() not in STOPWORDS]
    core = words[:4]
    return "_".join(sorted(core))

def is_duplicate(signature):
    db = load_json(SIGNATURES, {})
    return signature in db

def register_signature(signature):
    db = load_json(SIGNATURES, {})
    db[signature] = datetime.utcnow().isoformat()
    save_json_atomic(SIGNATURES, db)

# ==========================================================
# 5. SCORING
# ==========================================================

THRESHOLD = 10

INDIA_KEYWORDS = [
    "india","mumbai","delhi","bangalore","chennai",
    "gurgaon","hyderabad","kolkata","pune","goa","ahmedabad"
]

CRICKET_GLOBAL = ["cricket","t20","franchise","premier league"]

def calculate_sports_score(title):
    t = title.lower()
    score = 10

    if any(x in t for x in ["inaugural","season 1","returns","auction","draft","franchise"]):
        score += 40

    if any(x in t for x in ["rpf","tender","expression of interest","bcci","sports authority"]):
        score += 35

    if any(x in t for x in ["title sponsor","presenting partner","associate sponsor"]):
        score += 20

    if any(x in t for x in CRICKET_GLOBAL):
        score += 30

    if any(x in t for x in INDIA_KEYWORDS):
        score += 15

    return min(score, 100)

def calculate_ads_score(title):
    t = title.lower()
    score = 10
    if any(x in t for x in ["mandate","account win","bags","retains","pitch win"]):
        score += 40
    if any(x in t for x in INDIA_KEYWORDS):
        score += 15
    return min(score, 100)

# ==========================================================
# 6. FETCH ENGINE
# ==========================================================

def fetch_queries(queries, scorer, seen_file):
    seen_links = {i["link"] for i in load_json(seen_file, []) if isinstance(i, dict)}
    new_links = []
    results = []

    for q in queries:
        try:
            url = f"https://news.google.com/rss/search?q={quote(q)}&hl=en-IN&gl=IN&ceid=IN:en"
            feed = feedparser.parse(url)

            for e in feed.entries[:15]:
                if e.link in seen_links:
                    continue

                signature = generate_signature(e.title)
                if is_duplicate(signature):
                    continue

                score = scorer(e.title)
                if score >= THRESHOLD:
                    results.append({
                        "title": e.title,
                        "link": e.link,
                        "score": score
                    })
                    register_signature(signature)

                new_links.append({"link": e.link, "date": datetime.utcnow().isoformat()})

        except:
            continue

    if new_links:
        existing = load_json(seen_file, [])
        save_json_atomic(seen_file, existing + new_links)

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:10]

# ==========================================================
# 7. SPORTS ENGINE
# ==========================================================

def sports_fresh_queries():
    return [
        "league announced india when:2d",
        "season 1 sports india when:2d",
        "franchise league india when:2d",
        "bcci announcement when:2d",
        "cricket league global when:2d"
    ]

def sports_archive_queries():
    past_date = (date.today() - timedelta(days=90)).strftime("%Y-%m-%d")
    return [
        f"league announced india after:{past_date}",
        f"franchise league india after:{past_date}",
        f"bcci tender after:{past_date}",
        f"cricket league global after:{past_date}"
    ]

# ==========================================================
# 8. ADVERTISING ENGINE
# ==========================================================

def ads_fresh_queries():
    return [
        "creative mandate india when:2d",
        "account win india when:2d",
        "integrated mandate india when:2d"
    ]

def ads_archive_queries():
    past_date = (date.today() - timedelta(days=90)).strftime("%Y-%m-%d")
    return [
        f"creative mandate india after:{past_date}",
        f"account win india after:{past_date}"
    ]

# ==========================================================
# 9. REPORT FORMATTER (COMPACT)
# ==========================================================

def send_compact_report(chat_id, title, leads):
    if not leads:
        bot.send_message(chat_id, f"{title}\nNo qualifying leads found.")
        return

    text = f"{title}\n\n"
    for i, l in enumerate(leads, 1):
        text += f"{i}) ({l['score']}) {l['title']}\n"

    bot.send_message(chat_id, text)

# ==========================================================
# 10. COMMANDS
# ==========================================================

@bot.message_handler(commands=["leads-sports"])
def leads_sports(m):
    if m.from_user.id != ADMIN_ID:
        return
    bot.send_message(m.chat.id, "Scanning fresh sports radar...")
    leads = fetch_queries(sports_fresh_queries(), calculate_sports_score, SPORTS_SEEN)
    send_compact_report(m.chat.id, "SPORTS RADAR REPORT", leads)

@bot.message_handler(commands=["leads-ad"])
def leads_ads(m):
    if m.from_user.id != ADMIN_ID:
        return
    bot.send_message(m.chat.id, "Scanning fresh advertising radar...")
    leads = fetch_queries(ads_fresh_queries(), calculate_ads_score, ADS_SEEN)
    send_compact_report(m.chat.id, "ADVERTISING RADAR REPORT", leads)

@bot.message_handler(commands=["bootstrap-archive"])
def bootstrap_archive(m):
    if m.from_user.id != ADMIN_ID:
        return
    bot.send_message(m.chat.id, "Archive bootstrap initiated. Estimated time 60-120 seconds. Please wait...")
    def run():
        leads = fetch_queries(sports_archive_queries(), calculate_sports_score, SPORTS_SEEN)
        send_compact_report(m.chat.id, "SPORTS ARCHIVE REPORT", leads)
    threading.Thread(target=run).start()

# ==========================================================
# 11. SCHEDULER
# ==========================================================

def scheduler_loop():
    ist = pytz.timezone("Asia/Kolkata")
    schedule.every().day.at("10:00").do(lambda: send_compact_report(ADMIN_ID, "AUTO SPORTS RADAR", fetch_queries(sports_fresh_queries(), calculate_sports_score, SPORTS_SEEN)))
    schedule.every().day.at("10:15").do(lambda: send_compact_report(ADMIN_ID, "AUTO ADVERTISING RADAR", fetch_queries(ads_fresh_queries(), calculate_ads_score, ADS_SEEN)))
    while True:
        schedule.run_pending()
        time.sleep(30)

threading.Thread(target=scheduler_loop, daemon=True).start()

# ==========================================================
# 12. SELF-HEALING RUNNER
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
