import os
import sys
import json
import time
import logging
import threading
import requests
import re
import hashlib
import feedparser

from urllib.parse import quote
from datetime import datetime, timezone

import telebot

# ============================================================
# CORE CONFIG
# ============================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_USER_ID")

if not BOT_TOKEN or not ADMIN_ID:
    logging.critical("Missing TELEGRAM_TOKEN or ADMIN_USER_ID")
    sys.exit(1)

ADMIN_ID = int(ADMIN_ID)
bot = telebot.TeleBot(BOT_TOKEN)

DATA_DIR = os.getenv("DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)

SEEN_FILE = os.path.join(DATA_DIR, "seen_links.json")
QUEUE_FILE = os.path.join(DATA_DIR, "commercial_queue.json")
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.json")

# ============================================================
# STORAGE
# ============================================================

def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except:
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
# NETWORK
# ============================================================

def http_fetch(url, timeout=10):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.text
    except:
        pass
    return ""

def extract_text(html):
    html = re.sub(r"<script.*?>.*?</script>", "", html, flags=re.DOTALL)
    html = re.sub(r"<style.*?>.*?</style>", "", html, flags=re.DOTALL)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()

def generate_sig(text):
    return hashlib.md5(text[:80].encode()).hexdigest()

# ============================================================
# RSS SOURCES (COMMERCIAL TRADE)
# ============================================================

RSS_FEEDS = [
    "https://www.exchange4media.com/rss.xml",
    "https://www.afaqs.com/rss.xml",
    "https://www.campaignindia.in/rss",
    "https://economictimes.indiatimes.com/marketing/rssfeeds/13352306.cms"
]

SPONSOR_TERMS = [
    "title sponsor", "associate sponsor",
    "official partner", "brand ambassador",
    "campaign launch", "media mandate",
    "creative mandate", "agency of record",
    "strategic partner"
]

AGENCY_TERMS = [
    "Ogilvy", "DDB", "Publicis", "GroupM",
    "Wavemaker", "Madison", "Havas",
    "Leo Burnett", "FCB", "Mudra"
]

# ============================================================
# CORE DISCOVERY
# ============================================================

def discover_commercial():
    watchlist = load_json(WATCHLIST_FILE, {"athletes": [], "teams": []})
    athletes = watchlist.get("athletes", [])
    teams = watchlist.get("teams", [])

    seen = load_json(SEEN_FILE, [])
    seen_links = {x["link"] for x in seen}
    queue = load_json(QUEUE_FILE, {})

    for feed_url in RSS_FEEDS:
        feed = feedparser.parse(feed_url)

        for entry in feed.entries[:30]:

            title = entry.title
            link = entry.link
            summary = getattr(entry, "summary", "")

            if link in seen_links:
                continue

            combined = (title + " " + summary).lower()

            if not any(term in combined for term in SPONSOR_TERMS):
                continue

            deal_type = "COMMERCIAL DEAL"
            detected_athletes = []
            detected_teams = []
            detected_agencies = []

            for a in athletes:
                if a.lower() in combined:
                    detected_athletes.append(a)
                    deal_type = "ATHLETE ENDORSEMENT"

            for t in teams:
                if t.lower() in combined:
                    detected_teams.append(t)
                    deal_type = "TEAM SPONSOR"

            for agency in AGENCY_TERMS:
                if agency.lower() in combined:
                    detected_agencies.append(agency)

            score = 80
            if "title sponsor" in combined:
                score = 95
            if detected_athletes:
                score += 5
            if detected_teams:
                score += 5

            sig = generate_sig(title + link)
            if sig in queue:
                continue

            queue[sig] = {
                "type": deal_type,
                "title": title,
                "link": link,
                "athletes": detected_athletes,
                "teams": detected_teams,
                "agencies": detected_agencies,
                "score": min(score, 100),
                "released": False,
                "date": datetime.now(timezone.utc).isoformat()
            }

            seen.append({
                "link": link,
                "date": datetime.now(timezone.utc).isoformat()
            })

    save_json_atomic(SEEN_FILE, seen)
    save_json_atomic(QUEUE_FILE, queue)

# ============================================================
# DELIVERY
# ============================================================

def build_report():
    queue = load_json(QUEUE_FILE, {})
    items = [v for v in queue.values() if not v.get("released")]

    if not items:
        return None

    items.sort(key=lambda x: x.get("score", 0), reverse=True)

    report = "COMMERCIAL RADAR REPORT\n\n"

    for item in items:

        report += f"Type: {item.get('type')}\n"
        report += f"Title: {item.get('title')}\n"

        if item.get("athletes"):
            report += f"Athlete: {', '.join(item.get('athletes'))}\n"

        if item.get("teams"):
            report += f"Team: {', '.join(item.get('teams'))}\n"

        if item.get("agencies"):
            report += f"Agency: {', '.join(item.get('agencies'))}\n"

        report += f"Score: {item.get('score')}\n"
        report += f"Link: {item.get('link')}\n"

        report += "\nSuggested Action:\n"
        report += "1. Find brand marketing head\n"
        report += "2. Check campaign timeline\n"
        report += "3. Pitch athlete + match hybrid shoot\n\n"

        item["released"] = True

    save_json_atomic(QUEUE_FILE, queue)
    return report

# ============================================================
# COMMAND
# ============================================================

@bot.message_handler(commands=["leads-ad"])
def handle_ads(message):
    if message.from_user.id != ADMIN_ID:
        return

    def task():
        bot.send_message(message.chat.id, "Scanning Commercial Intelligence...")
        discover_commercial()
        report = build_report()
        bot.send_message(message.chat.id, report if report else "No new commercial deals detected.")

    threading.Thread(target=task).start()

# ============================================================
# RUNNER
# ============================================================

if __name__ == "__main__":
    logging.info("SAIKAT OS 2.19 COMMERCIAL ENGINE ONLINE")
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=10)
        except Exception as e:
            logging.error(f"Polling error: {e}")
            time.sleep(10)
