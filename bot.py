import os
import sys
import json
import time
import logging
import threading
import requests
import re
import hashlib

import telebot
import feedparser

from urllib.parse import quote, urlparse
from datetime import datetime, timedelta, date, timezone

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

# ⚠️ Removed Markdown to prevent Telegram entity crashes
bot = telebot.TeleBot(BOT_TOKEN)

DATA_DIR = os.getenv("DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)

SEEN_FILE = os.path.join(DATA_DIR, "seen_links.json")
QUEUE_FILE = os.path.join(DATA_DIR, "league_queue.json")
META_FILE = os.path.join(DATA_DIR, "meta.json")

# ============================================================
# STORAGE UTILITIES
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
# NETWORK UTILITIES
# ============================================================

def http_fetch(url, timeout=10):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0"
        }
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.text
    except:
        pass
    return ""

def extract_visible_text(html):
    html = re.sub(r"<script.*?>.*?</script>", "", html, flags=re.DOTALL)
    html = re.sub(r"<style.*?>.*?</style>", "", html, flags=re.DOTALL)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()

def generate_sig(text, link):
    raw = f"{text[:60]}-{urlparse(link).netloc}"
    return hashlib.md5(raw.encode()).hexdigest()

# ============================================================
# GOOGLE GUARD (CATEGORY BASED)
# ============================================================

def google_allowed(category):
    meta = load_json(META_FILE, {})
    key = f"last_google_{category}"
    last = meta.get(key)
    if not last:
        return True
    return (datetime.now(timezone.utc) - datetime.fromisoformat(last)) > timedelta(minutes=5)

def mark_google_scan(category):
    meta = load_json(META_FILE, {})
    meta[f"last_google_{category}"] = datetime.now(timezone.utc).isoformat()
    save_json_atomic(META_FILE, meta)

def google_search(query, category="default"):
    if not google_allowed(category):
        logging.info(f"Google cooldown active for {category}")
        return []

    html = http_fetch(f"https://www.google.com/search?q={quote(query)}&num=10")

    if not html or "unusual traffic" in html.lower():
        logging.warning("Google blocked or empty response")
        return []

    mark_google_scan(category)

    return list(set(re.findall(r'/url\?q=(https?://[^&]+)&', html)))

# ============================================================
# SPONSOR INTELLIGENCE 2.18
# ============================================================

IPL_TEAMS = [
    "Lucknow Super Giants", "LSG",
    "Mumbai Indians", "Chennai Super Kings",
    "Royal Challengers Bangalore",
    "Kolkata Knight Riders",
    "Sunrisers Hyderabad",
    "Delhi Capitals",
    "Punjab Kings",
    "Rajasthan Royals"
]

SPONSOR_TERMS = [
    "title sponsor", "associate sponsor", "official partner",
    "principal sponsor", "strategic partner", "jersey sponsor",
    "powered by"
]

def detect_deal_type(text):
    t = text.lower()
    if "title sponsor" in t:
        return "TITLE SPONSOR"
    if "associate sponsor" in t:
        return "ASSOCIATE SPONSOR"
    if "official partner" in t:
        return "OFFICIAL PARTNER"
    return "SPONSOR DEAL"

def extract_entities(text):
    teams = []
    for team in IPL_TEAMS:
        if team.lower() in text.lower():
            teams.append(team)

    # Brand detection: words before "sponsor" keyword
    brands = []
    matches = re.findall(r"([A-Z][A-Za-z0-9&]{2,30})\s+(?:joins|becomes|announced|signs|as)", text)
    brands.extend(matches)

    return list(set(brands)), list(set(teams))

def discover_sponsor_intel():
    queries = [
        'site:linkedin.com/posts "IPL sponsor"',
        'site:linkedin.com/posts "title sponsor IPL"',
        'site:linkedin.com/posts "Lucknow Super Giants" sponsor'
    ]

    seen = load_json(SEEN_FILE, [])
    seen_links = {x["link"] for x in seen}
    queue = load_json(QUEUE_FILE, {})

    for q in queries:
        links = google_search(q, category="sponsor")

        for link in links:
            if link in seen_links:
                continue

            html = http_fetch(link)
            if not html:
                continue

            text = extract_visible_text(html)

            if not any(term in text.lower() for term in SPONSOR_TERMS):
                continue

            brands, teams = extract_entities(text)

            sig = generate_sig(text, link)
            if sig in queue:
                continue

            deal_type = detect_deal_type(text)

            queue[sig] = {
                "type": "sponsor",
                "deal_type": deal_type,
                "brands": brands,
                "teams": teams,
                "link": link,
                "score": 95 if "title" in deal_type.lower() else 85,
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

def build_report(category):
    queue = load_json(QUEUE_FILE, {})
    items = [v for v in queue.values() if not v.get("released") and v.get("type") == category]

    if not items:
        return None

    items.sort(key=lambda x: x.get("score", 0), reverse=True)

    report = f"{category.upper()} RADAR REPORT\n\n"

    for item in items:
        report += f"🚀 {item.get('deal_type')}\n"
        report += f"Brand: {', '.join(item.get('brands') or ['Not detected'])}\n"
        report += f"Team: {', '.join(item.get('teams') or ['Not detected'])}\n"
        report += f"Score: {item.get('score')}\n"
        report += f"Link: {item.get('link')}\n"

        # Outreach suggestion layer
        report += "\nSuggested Action:\n"
        report += "1. Search LinkedIn for Brand Marketing Head\n"
        report += "2. Search Agency handling this team\n"
        report += "3. Pitch campaign + match-day hybrid shoot\n\n"

        item["released"] = True

    save_json_atomic(QUEUE_FILE, queue)
    return report

# ============================================================
# COMMAND HANDLER
# ============================================================

@bot.message_handler(commands=["leads-ad"])
def handle_ads(message):
    if message.from_user.id != ADMIN_ID:
        return

    def task():
        bot.send_message(message.chat.id, "Scanning Sponsor Intelligence...")
        discover_sponsor_intel()
        rep = build_report("sponsor")
        bot.send_message(message.chat.id, rep if rep else "No new sponsor deals detected.")

    threading.Thread(target=task).start()

# ============================================================
# RUNNER
# ============================================================

if __name__ == "__main__":
    logging.info("SAIKAT OS 2.18 ONLINE")
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=10)
        except Exception as e:
            logging.error(f"Polling error: {e}")
            time.sleep(10)
