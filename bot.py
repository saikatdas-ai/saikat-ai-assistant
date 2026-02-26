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
# STORAGE (PORTABLE)
# ============================================================

DATA_DIR = os.getenv("DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)

SEEN_FILE = os.path.join(DATA_DIR, "seen_links.json")
QUEUE_FILE = os.path.join(DATA_DIR, "league_queue.json")
META_FILE = os.path.join(DATA_DIR, "meta.json")

def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                logging.error(f"Corrupted JSON in {path}. Resetting.")
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
# SAFETY
# ============================================================

def safe_send(chat_id, text):
    MAX = 3500
    for i in range(0, len(text), MAX):
        try:
            bot.send_message(chat_id, text[i:i+MAX])
        except Exception as e:
            logging.error(f"Send failed: {e}")

def escape_md(text):
    return text.replace("[", "").replace("]", "").replace("(", "").replace(")", "")

# ============================================================
# SIGNAL CONFIG
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

# ============================================================
# TEXT CLEANING
# ============================================================

def extract_visible_text(html):
    html = re.sub(r"<script.*?>.*?</script>", "", html, flags=re.DOTALL)
    html = re.sub(r"<style.*?>.*?</style>", "", html, flags=re.DOTALL)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"\s+", " ", html)
    return html.strip()

# ============================================================
# CORE LOGIC
# ============================================================

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

def signature(text, link):
    domain = urlparse(link).netloc
    raw = f"{text}-{domain}"
    return hashlib.md5(raw.encode()).hexdigest()

# ============================================================
# TAGGING + CONFIDENCE
# ============================================================

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

def calculate_confidence(text, source, revenue, roles):
    score = 40
    t = text.lower()
    score += sum(1 for k in CRICKET_PRIORITY if k in t) * 10
    if revenue == "media_rights":
        score += 20
    elif revenue == "sponsorship":
        score += 15
    elif revenue == "player_market":
        score += 10
    score += len(roles) * 5
    if source == "rss":
        score += 10
    elif source == "google":
        score += 5
    elif source == "cricbuzz":
        score += 15
    return min(score, 100)

def classify_priority(conf):
    if conf >= 75:
        return "HIGH"
    if conf >= 55:
        return "MEDIUM"
    return "LOW"

# ============================================================
# NETWORK HELPERS
# ============================================================

def http_fetch(url, timeout=10):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.text
    except requests.Timeout:
        logging.warning(f"Timeout fetching {url}")
    except Exception as e:
        logging.warning(f"HTTP error {url}: {e}")
    return ""

# ============================================================
# GOOGLE WITH COOLDOWN + BLOCK DETECTION
# ============================================================

def google_allowed():
    meta = load_json(META_FILE, {})
    last = meta.get("last_google_scan")
    if not last:
        return True
    last_time = datetime.fromisoformat(last)
    return (datetime.now(timezone.utc) - last_time) > timedelta(minutes=5)

def mark_google_scan():
    meta = load_json(META_FILE, {})
    meta["last_google_scan"] = datetime.now(timezone.utc).isoformat()
    save_json_atomic(META_FILE, meta)

def google_index_scan(query):
    if not google_allowed():
        logging.info("Google cooldown active. Skipping.")
        return []

    url = f"https://www.google.com/search?q={quote(query)}&num=10"
    html = http_fetch(url)

    if not html:
        return []

    if "unusual traffic" in html.lower() or "verify you are human" in html.lower():
        logging.warning("Google blocked or CAPTCHA detected.")
        return []

    mark_google_scan()

    matches = re.findall(r'/url\?q=(https?://[^&]+)&', html)
    return list(set(matches))

# ============================================================
# FIXTURE SCANNING WITH FALLBACK
# ============================================================

def cricbuzz_fixture_scan():
    html = http_fetch("https://www.cricbuzz.com/cricket-schedule", timeout=10)
    if not html:
        return []
    matches = re.findall(r'href="(/cricket-match/[^"]+)"', html)
    return [("Cricbuzz Fixture", "https://www.cricbuzz.com" + m) for m in matches[:15]]

def espn_fixture_fallback():
    html = http_fetch("https://www.espncricinfo.com/ci/content/match/fixtures_futures.html", timeout=10)
    if not html:
        return []
    matches = re.findall(r'href="(/series/[^"]+)"', html)
    return [("ESPN Fixture", "https://www.espncricinfo.com" + m) for m in matches[:10]]

# ============================================================
# RSS DISCOVERY
# ============================================================

def fetch_rss(query, days):
    results = []
    past = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    url = f"https://news.google.com/rss/search?q={quote(query)}+after:{past}&hl=en-IN&gl=IN&ceid=IN:en"
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
        "broadcast deal league"
    ]

    seen = load_json(SEEN_FILE, [])
    seen_links = {x["link"] for x in seen}
    queue = load_json(QUEUE_FILE, {})

    for q in queries:
        for title, link in fetch_rss(q, days):
            if link in seen_links:
                continue
            if not is_valid_league(title):
                continue

            sig = signature(title, link)
            if sig in queue:
                continue

            revenue = detect_revenue_angle(title)
            roles = detect_decision_signal(title)
            conf = calculate_confidence(title, "rss", revenue, roles)

            queue[sig] = {
                "title": title,
                "link": link,
                "score": calculate_score(title),
                "confidence": conf,
                "priority": classify_priority(conf),
                "released": False,
                "date": datetime.now(timezone.utc).isoformat(),
                "source": "rss",
                "revenue_angle": revenue,
                "decision_roles": roles
            }

            seen.append({"link": link, "date": datetime.now(timezone.utc).isoformat()})

    save_json_atomic(SEEN_FILE, seen)
    save_json_atomic(QUEUE_FILE, queue)

# ============================================================
# EXTENDED DISCOVERY
# ============================================================

def extended_discover():
    queue = load_json(QUEUE_FILE, {})
    seen = load_json(SEEN_FILE, [])
    seen_links = {x["link"] for x in seen}

    queries = ["league expansion cricket", "sports broadcast deal india"]

    for q in queries:
        for link in google_index_scan(q):
            if link in seen_links:
                continue
            html = http_fetch(link)
            if not html:
                continue

            text = extract_visible_text(html)
            if not is_valid_league(text):
                continue

            sig = signature(text[:100], link)
            if sig in queue:
                continue

            revenue = detect_revenue_angle(text)
            roles = detect_decision_signal(text)
            conf = calculate_confidence(text, "google", revenue, roles)

            queue[sig] = {
                "title": q,
                "link": link,
                "score": calculate_score(text),
                "confidence": conf,
                "priority": classify_priority(conf),
                "released": False,
                "date": datetime.now(timezone.utc).isoformat(),
                "source": "google",
                "revenue_angle": revenue,
                "decision_roles": roles
            }

            seen.append({"link": link, "date": datetime.now(timezone.utc).isoformat()})

    fixtures = cricbuzz_fixture_scan()
    if not fixtures:
        fixtures = espn_fixture_fallback()

    for title, link in fixtures:
        if link in seen_links:
            continue

        sig = signature(title, link)
        if sig in queue:
            continue

        conf = calculate_confidence(title, "cricbuzz", "media_rights", [])

        queue[sig] = {
            "title": title,
            "link": link,
            "score": 70,
            "confidence": conf,
            "priority": classify_priority(conf),
            "released": False,
            "date": datetime.now(timezone.utc).isoformat(),
            "source": "fixture",
            "revenue_angle": "media_rights",
            "decision_roles": []
        }

        seen.append({"link": link, "date": datetime.now(timezone.utc).isoformat()})

    save_json_atomic(SEEN_FILE, seen)
    save_json_atomic(QUEUE_FILE, queue)

# ============================================================
# DELIVERY
# ============================================================

def build_report(limit=20):
    queue = load_json(QUEUE_FILE, {})
    items = [v for v in queue.values() if not v.get("released")]

    if not items:
        return None

    items.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    selected = items[:limit]

    report = "Sports Radar Report\n\n"

    for item in selected:
        title = escape_md(item.get("title", "Untitled"))
        link = item.get("link", "#")
        priority = item.get("priority", "NA")
        conf = item.get("confidence", 0)
        revenue = item.get("revenue_angle", "general")

        report += f"[{priority}] ({conf}) [{title}]({link}) - {revenue}\n\n"
        item["released"] = True

    save_json_atomic(QUEUE_FILE, queue)
    return report

# ============================================================
# COMMANDS
# ============================================================

def run_manual(chat_id):
    safe_send(chat_id, "Sports Radar running...")
    discover(7)
    extended_discover()
    report = build_report(20)
    if not report:
        safe_send(chat_id, "No new leads.")
    else:
        safe_send(chat_id, report)

@bot.message_handler(commands=["leads-sports"])
def handle_leads(message):
    if message.from_user.id != ADMIN_ID:
        return
    threading.Thread(target=run_manual, args=(message.chat.id,)).start()

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
