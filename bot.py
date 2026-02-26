import os
import sys
import json
import time
import logging
import threading
import requests
import re

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
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                logging.error(f"Corrupted JSON detected in {path}. Resetting file.")
                return default
    except Exception as e:
        logging.error(f"Read error for {path}: {e}")
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
    "league","t20","auction","draft",
    "franchise","season","broadcast deal","sponsorship deal"
]

CRICKET_PRIORITY = [
    "ipl","t20","cricket","big bash","psl","cpl"
]

EXCLUDE = [
    "nfl","nba","university","college","youth","school"
]

def is_valid_league(text):
    t = text.lower()
    if any(x in t for x in EXCLUDE):
        return False
    if any(k in t for k in FRANCHISE_KEYWORDS):
        return True
    return False

def calculate_score(text):
    score = 50
    t = text.lower()
    if any(k in t for k in CRICKET_PRIORITY):
        score += 30
    if "auction" in t or "draft" in t:
        score += 20
    return min(score, 100)

def signature(text):
    words = sorted([w for w in text.lower().split() if len(w) > 4])
    return "-".join(words[:5])

# ============================================================
# DISCOVERY ENGINE (RSS)
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

            queue[sig] = {
                "title": title,
                "link": link,
                "score": calculate_score(title),
                "released": False,
                "date": datetime.utcnow().isoformat(),
                "source": "rss"
            }

            seen.append({
                "link": link,
                "date": datetime.utcnow().isoformat()
            })

    save_json_atomic(SEEN_FILE, seen)
    save_json_atomic(QUEUE_FILE, queue)

    return scanned

# ============================================================
# PHASE 2.14 EXTENDED DISCOVERY
# ============================================================

def http_fetch(url, timeout=15):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (SaikatOS Radar 2.14)"}
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.text
    except:
        pass
    return ""

def google_index_scan(query):
    results = []
    url = f"https://www.google.com/search?q={quote(query)}&num=10"
    html = http_fetch(url)
    if not html:
        return results
    matches = re.findall(r'/url\?q=(https?://[^&]+)&', html)
    for link in matches:
        if "google" not in link:
            results.append(link)
    return list(set(results))

def cricbuzz_fixture_scan():
    results = []
    html = http_fetch("https://www.cricbuzz.com/cricket-schedule")
    if not html:
        return results
    matches = re.findall(r'href="(/cricket-match/[^"]+)"', html)
    for m in matches[:15]:
        results.append(("Cricbuzz Fixture", "https://www.cricbuzz.com" + m))
    return results

def detect_revenue_angle(text):
    t = text.lower()
    if "sponsor" in t or "brand partner" in t:
        return "sponsorship"
    if "broadcast" in t or "streaming" in t:
        return "media_rights"
    if "ticket" in t:
        return "ticketing"
    if "auction" in t or "draft" in t:
        return "player_market"
    return "general"

def detect_decision_signal(text):
    roles = ["ceo","director","head","commercial","marketing","sponsorship"]
    return [r for r in roles if r in text.lower()]

def extended_discover(days):
    queue = load_json(QUEUE_FILE, {})
    seen = load_json(SEEN_FILE, [])
    seen_links = {x["link"] for x in seen}

    queries = [
        "new franchise league india",
        "league expansion cricket",
        "t20 league announcement",
        "sports broadcast deal india",
    ]

    for q in queries:
        links = google_index_scan(q)
        for link in links:
            if link in seen_links:
                continue

            html = http_fetch(link)
            if not html:
                continue
            if not is_valid_league(html):
                continue

            sig = signature(link)
            if sig in queue:
                continue

            score = calculate_score(html)
            revenue = detect_revenue_angle(html)
            roles = detect_decision_signal(html)

            queue[sig] = {
                "title": q,
                "link": link,
                "score": score,
                "released": False,
                "date": datetime.utcnow().isoformat(),
                "source": "google",
                "revenue_angle": revenue,
                "decision_roles": roles
            }

            seen.append({
                "link": link,
                "date": datetime.utcnow().isoformat()
            })

    fixtures = cricbuzz_fixture_scan()
    for title, link in fixtures:
        if link in seen_links:
            continue

        sig = signature(link)
        if sig in queue:
            continue

        queue[sig] = {
            "title": title,
            "link": link,
            "score": 70,
            "released": False,
            "date": datetime.utcnow().isoformat(),
            "source": "cricbuzz",
            "revenue_angle": "media_rights",
            "decision_roles": []
        }

        seen.append({
            "link": link,
            "date": datetime.utcnow().isoformat()
        })

    save_json_atomic(SEEN_FILE, seen)
    save_json_atomic(QUEUE_FILE, queue)

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
    discover(7)
    extended_discover(7)
    report = build_report(limit=20)

    if not report:
        safe_send(chat_id, "Scan complete. No new franchise league announcements detected.")
        return

    safe_send(chat_id, report)

def run_bootstrap(chat_id):
    safe_send(chat_id, "Running 90-day archive scan. This may take 30-60 seconds.")
    discover(90)
    extended_discover(90)
    report = build_report(limit=9999)

    if not report:
        safe_send(chat_id, "Archive scan complete. No new historical leagues found.")
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
