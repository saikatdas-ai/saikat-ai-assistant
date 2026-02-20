import os
import sys
import json
import time
import logging
import threading
import difflib
import requests

import telebot
import feedparser

from urllib.parse import quote
from datetime import datetime, timedelta, date

# ============================================================
# CONFIG
# ============================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_USER_ID")

if not BOT_TOKEN or not ADMIN_ID:
    logging.critical("Missing TELEGRAM_TOKEN or ADMIN_USER_ID")
    sys.exit(1)

ADMIN_ID = int(ADMIN_ID)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

# ============================================================
# STORAGE
# ============================================================

DATA_DIR = "/app/data"
SEEN_FILE = os.path.join(DATA_DIR, "seen_links.json")
QUEUE_FILE = os.path.join(DATA_DIR, "league_queue.json")

os.makedirs(DATA_DIR, exist_ok=True)


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


# ============================================================
# SAFETY UTILITIES
# ============================================================

def safe_send(chat_id, text):
    MAX = 3500
    chunks = [text[i:i+MAX] for i in range(0, len(text), MAX)]
    for chunk in chunks:
        try:
            bot.send_message(chat_id, chunk)
        except Exception as e:
            logging.error(f"Send failed: {e}")


def escape_md(text):
    return text.replace("[", "").replace("]", "").replace("(", "").replace(")", "")


# ============================================================
# LEAGUE DETECTION ENGINE
# ============================================================

FRANCHISE_KEYWORDS = [
    "league",
    "t20",
    "auction",
    "draft",
    "franchise",
    "season",
    "broadcast deal",
    "sponsorship deal",
]

CRICKET_PRIORITY = [
    "ipl",
    "t20",
    "cricket",
    "big bash",
    "psl",
    "cpl",
]

EXCLUDE = [
    "nfl",
    "nba",
    "university",
    "college",
    "youth",
    "school",
]


def is_valid_league(title):
    t = title.lower()
    if any(x in t for x in EXCLUDE):
        return False
    if any(k in t for k in FRANCHISE_KEYWORDS):
        return True
    return False


def calculate_score(title):
    score = 50
    t = title.lower()
    if any(k in t for k in CRICKET_PRIORITY):
        score += 30
    if "auction" in t or "draft" in t:
        score += 20
    return min(score, 100)


def signature(title):
    words = sorted([w for w in title.lower().split() if len(w) > 4])
    return "-".join(words[:5])


# ============================================================
# DISCOVERY ENGINE
# ============================================================

def fetch_rss(query, days):
    results = []
    past_date = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    url = f"https://news.google.com/rss/search?q={quote(query)}+after:{past_date}&hl=en-IN&gl=IN&ceid=IN:en"

    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:25]:
            results.append((entry.title, entry.link))
    except:
        pass

    return results


def discover(days):
    queries = [
        "franchise league india",
        "t20 league season",
        "league auction cricket",
        "state t20 league",
        "broadcast deal league",
        "motorsport championship india",
    ]

    seen = load_json(SEEN_FILE, [])
    seen_links = {x["link"] for x in seen}

    queue = load_json(QUEUE_FILE, {})

    scanned = 0

    for q in queries:
        entries = fetch_rss(q, days)
        for title, link in entries:
            scanned += 1
            if link in seen_links:
                continue
            if not is_valid_league(title):
                continue

            sig = signature(title)
            if sig in queue:
                continue

            score = calculate_score(title)

            queue[sig] = {
                "title": title,
                "link": link,
                "score": score,
                "released": False,
                "date": datetime.utcnow().isoformat()
            }

            seen.append({
                "link": link,
                "date": datetime.utcnow().isoformat()
            })

    save_json_atomic(SEEN_FILE, seen)
    save_json_atomic(QUEUE_FILE, queue)

    return scanned


# ============================================================
# DELIVERY ENGINE
# ============================================================

def build_report(limit=20):
    queue = load_json(QUEUE_FILE, {})
    items = [v for v in queue.values() if not v["released"]]

    if not items:
        return None

    items.sort(key=lambda x: x["score"], reverse=True)

    selected = items[:limit]

    report = "Sports Radar Report\n\n"

    for item in selected:
        title = escape_md(item["title"])
        report += f"({item['score']}) [{title}]({item['link']})\n\n"

        item["released"] = True

    save_json_atomic(QUEUE_FILE, queue)

    return report


# ============================================================
# COMMAND HANDLERS
# ============================================================

def run_manual_sports(chat_id):
    safe_send(chat_id, "Sports Radar running. Checking 48h + 7d buffer. Estimated 15-30 seconds.")

    scanned = discover(7)

    report = build_report(limit=20)

    if not report:
        safe_send(chat_id, f"Scan complete. Scanned: {scanned}. No new franchise league announcements detected.")
        return

    safe_send(chat_id, report)


def run_bootstrap(chat_id):
    safe_send(chat_id, "Running 90-day archive scan. This may take 30-60 seconds.")

    scanned = discover(90)

    report = build_report(limit=9999)

    if not report:
        safe_send(chat_id, f"Archive scan complete. Scanned: {scanned}. No new historical leagues found.")
        return

    safe_send(chat_id, report)


@bot.message_handler(commands=["leads-sports"])
def manual_sports(message):
    if message.from_user.id != ADMIN_ID:
        return
    threading.Thread(target=run_manual_sports, args=(message.chat.id,)).start()


@bot.message_handler(commands=["bootstrap-archive"])
def bootstrap_archive(message):
    if message.from_user.id != ADMIN_ID:
        return
    threading.Thread(target=run_bootstrap, args=(message.chat.id,)).start()


# ============================================================
# RUNNER
# ============================================================

if __name__ == "__main__":
    delay = 5
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=10)
            delay = 5
        except Exception as e:
            logging.error(f"Polling crash: {e}")
            time.sleep(delay)
            delay = min(delay * 2, 300)
