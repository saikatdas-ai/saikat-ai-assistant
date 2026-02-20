import os
import sys
import time
import json
import logging
import threading
import feedparser
import telebot
import schedule
import difflib
from urllib.parse import quote
from datetime import datetime, timedelta, date

import google.generativeai as genai


# ==========================================================
# 1. SAFE CONFIG BOOT
# ==========================================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
ADMIN_ID = os.environ.get("ADMIN_USER_ID")

if not BOT_TOKEN or not GEMINI_KEY or not ADMIN_ID:
    logging.critical("Missing TELEGRAM_TOKEN / GEMINI_API_KEY / ADMIN_USER_ID")
    sys.exit(1)

try:
    ADMIN_ID = int(ADMIN_ID)
except:
    logging.critical("ADMIN_USER_ID must be integer")
    sys.exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

genai.configure(api_key=GEMINI_KEY)


# Intelligent Gemini model selection
def select_best_model():
    try:
        models = genai.list_models()
        candidates = [
            m.name for m in models
            if "generateContent" in getattr(m, "supported_generation_methods", [])
        ]

        priority = [
            "models/gemini-2.5-flash",
            "models/gemini-2.0-flash",
            "models/gemini-2.0-pro",
            "models/gemini-1.5-flash",
        ]

        for p in priority:
            if p in candidates:
                logging.info("Using Gemini model: " + p)
                return p

        if candidates:
            logging.info("Using fallback Gemini model: " + candidates[0])
            return candidates[0]

    except Exception as e:
        logging.error("Model discovery failed: " + str(e))

    return "models/gemini-1.5-flash"


model = genai.GenerativeModel(select_best_model())


# ==========================================================
# 2. STORAGE
# ==========================================================

DATA_DIR = "/app/data"
SEEN_FILE = os.path.join(DATA_DIR, "seen_links.json")
QUEUE_FILE = os.path.join(DATA_DIR, "pending_leagues.json")

os.makedirs(DATA_DIR, exist_ok=True)


def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except Exception as e:
        logging.error("Read error " + path + " : " + str(e))
    return default


def save_json_atomic(path, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception as e:
        logging.error("Write error " + path + " : " + str(e))


# ==========================================================
# 3. DISCOVERY LOGIC
# ==========================================================

SPORT_PRIORITY = {
    "cricket": 50,
    "kabaddi": 40,
    "football": 40,
    "hockey": 35,
    "motorsport": 35,
    "tennis": 25,
    "volleyball": 20,
    "basketball": 20,
}

FRANCHISE_KEYWORDS = [
    "league",
    "franchise",
    "auction",
    "draft",
    "season",
    "teams",
    "fixtures",
    "sponsorship",
    "broadcast",
]

INDIA_WEIGHT_WORDS = [
    "india",
    "mumbai",
    "delhi",
    "bangalore",
    "chennai",
    "kolkata",
    "hyderabad",
    "pune",
]


def calculate_score(title):
    t = title.lower()
    score = 0

    # sport weight
    for sport, weight in SPORT_PRIORITY.items():
        if sport in t:
            score += weight

    # franchise structure
    for word in FRANCHISE_KEYWORDS:
        if word in t:
            score += 15

    # India weight
    for word in INDIA_WEIGHT_WORDS:
        if word in t:
            score += 10

    return score


def signature(title):
    words = [w for w in title.lower().split() if len(w) > 4]
    words.sort()
    return "_".join(words[:5])


# ==========================================================
# 4. MULTI-SOURCE DISCOVERY
# ==========================================================

def discover_sources(days_back):
    past_date = (date.today() - timedelta(days=days_back)).strftime('%Y-%m-%d')

    queries = [
        f'"franchise league" after:{past_date}',
        f'"league season" after:{past_date}',
        f'"league auction" after:{past_date}',
        f'"league draft" after:{past_date}',
        f'"T20 league" after:{past_date}',
        f'"motorsport championship" after:{past_date}',
        f'"kabaddi league" after:{past_date}',
        f'"football league" after:{past_date}',
        f'"tennis league" after:{past_date}',
        f'"volleyball league" after:{past_date}',
    ]

    seen = load_json(SEEN_FILE, [])
    seen_links = {i["link"] for i in seen if isinstance(i, dict)}

    queue = load_json(QUEUE_FILE, {})

    scanned = 0
    new_items = 0

    for q in queries:
        try:
            url = f"https://news.google.com/rss/search?q={quote(q)}&hl=en-IN&gl=IN&ceid=IN:en"
            feed = feedparser.parse(url)

            for e in feed.entries[:20]:
                scanned += 1

                if e.link in seen_links:
                    continue

                sig = signature(e.title)
                if sig in queue:
                    continue

                score = calculate_score(e.title)

                queue[sig] = {
                    "title": e.title,
                    "link": e.link,
                    "score": score,
                    "first_seen": datetime.utcnow().isoformat(),
                    "released": False,
                }

                new_items += 1

                seen.append({
                    "link": e.link,
                    "date": datetime.utcnow().isoformat()
                })

        except Exception as e:
            logging.error("Feed error: " + str(e))

    save_json_atomic(SEEN_FILE, seen)
    save_json_atomic(QUEUE_FILE, queue)

    return scanned, new_items


# ==========================================================
# 5. DELIVERY ENGINE
# ==========================================================

def deliver_report(cap=20):
    queue = load_json(QUEUE_FILE, {})

    pending = [
        v for v in queue.values()
        if not v.get("released")
    ]

    pending.sort(key=lambda x: x["score"], reverse=True)

    deliver = pending[:cap]

    for item in deliver:
        for k, v in queue.items():
            if v["title"] == item["title"]:
                queue[k]["released"] = True

    save_json_atomic(QUEUE_FILE, queue)

    return deliver


# ==========================================================
# 6. COMMANDS
# ==========================================================

@bot.message_handler(commands=["leads-sports"])
def manual_sports(m):
    if m.from_user.id != ADMIN_ID:
        return

    bot.send_message(
        m.chat.id,
        "Sports Radar running. Checking 48h + 7d buffer. Estimated 15-30 seconds."
    )

    discover_sources(7)

    results = deliver_report(cap=20)

    if not results:
        bot.send_message(m.chat.id, "No new leagues detected.")
        return

    text = "SPORTS REPORT\n\n"
    for r in results:
        text += f"({r['score']}) {r['title']}\n{r['link']}\n\n"

    bot.send_message(m.chat.id, text)


@bot.message_handler(commands=["bootstrap-archive"])
def archive_scan(m):
    if m.from_user.id != ADMIN_ID:
        return

    bot.send_message(
        m.chat.id,
        "Running 90-day archive scan. This may take 30-60 seconds."
    )

    scanned, new_items = discover_sources(90)

    queue = load_json(QUEUE_FILE, {})
    pending = list(queue.values())

    pending.sort(key=lambda x: x["score"], reverse=True)

    text = f"90 Day Scan Completed\nScanned: {scanned}\n\n"

    for r in pending[:50]:
        text += f"({r['score']}) {r['title']}\n{r['link']}\n\n"

    bot.send_message(m.chat.id, text)


# ==========================================================
# 7. AUTO 10AM RUN (CAPPED 20)
# ==========================================================

def auto_sports():
    try:
        discover_sources(7)
        results = deliver_report(cap=20)

        if results:
            text = "10AM SPORTS AUTO REPORT\n\n"
            for r in results:
                text += f"({r['score']}) {r['title']}\n{r['link']}\n\n"

            bot.send_message(ADMIN_ID, text)
        else:
            bot.send_message(ADMIN_ID, "10AM SPORTS: No new leagues.")

    except Exception as e:
        logging.error("Auto run failed: " + str(e))


def scheduler_loop():
    schedule.every().day.at("10:00").do(auto_sports)

    while True:
        schedule.run_pending()
        time.sleep(30)


# ==========================================================
# 8. SELF-HEALING RUNNER
# ==========================================================

if __name__ == "__main__":

    threading.Thread(target=scheduler_loop, daemon=True).start()

    delay = 5

    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=10)
            delay = 5
        except Exception as e:
            logging.error("Polling crash: " + str(e))
            time.sleep(delay)
            delay = min(delay * 2, 300)
