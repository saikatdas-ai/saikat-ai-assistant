# SAIKAT OS — 10/10 PRODUCTION BUILD (Phase‑2.5 hardened, Phase‑3 ready)
# Includes:
# - Deterministic scoring
# - Atomic persistence
# - 30‑day lead pruning (performance)
# - PERMANENT follow‑up memory (conversion safety)
# - Idempotent scheduler
# - Crash‑safe runner

import os
import sys
import telebot
import google.generativeai as genai
import feedparser
import time
import json
import logging
import threading
import schedule
import pytz
import difflib
from urllib.parse import quote
from datetime import datetime, date, timedelta

# ==========================================================
# 1. CONFIG
# ==========================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
ADMIN_ID = os.environ.get("ADMIN_USER_ID")

if not BOT_TOKEN or not GEMINI_KEY or not ADMIN_ID:
    logging.critical("Missing env configuration. Exiting.")
    sys.exit(1)

ADMIN_ID = int(ADMIN_ID)

bot = telebot.TeleBot(BOT_TOKEN)

genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel("models/gemini-1.5-flash")

# ==========================================================
# 2. STORAGE LAYERS
# ==========================================================
DATA_DIR = "/app/data"
SEEN_FILE = os.path.join(DATA_DIR, "seen_links.json")
STATE_FILE = os.path.join(DATA_DIR, "state.json")
FOLLOW_FILE = os.path.join(DATA_DIR, "followups.json")  # permanent CRM memory

os.makedirs(DATA_DIR, exist_ok=True)


def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except Exception as e:
        logging.error(f"Read error {path}: {e}")
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
# 3. PERFORMANCE MEMORY (30‑DAY PRUNE)
# ==========================================================

def prune_old_links(days=30):
    data = load_json(SEEN_FILE, [])
    cutoff = datetime.utcnow() - timedelta(days=days)

    fresh = [
        item for item in data
        if isinstance(item, dict)
        and "date" in item
        and datetime.fromisoformat(item["date"]).replace(tzinfo=None) > cutoff
    ]

    save_json_atomic(SEEN_FILE, fresh)


def get_seen_links():
    prune_old_links()
    return {item["link"] for item in load_json(SEEN_FILE, [])}


def add_seen_links(links):
    existing = load_json(SEEN_FILE, [])
    now = datetime.utcnow().isoformat()

    for link in links:
        existing.append({"link": link, "date": now})

    save_json_atomic(SEEN_FILE, existing)

# ==========================================================
# 4. PERMANENT FOLLOW‑UP MEMORY (NO PRUNING)
# ==========================================================

def load_followups():
    return load_json(FOLLOW_FILE, {})


def save_followups(data):
    save_json_atomic(FOLLOW_FILE, data)


def register_followup(title, link):
    db = load_followups()

    if link not in db:
        db[link] = {
            "title": title,
            "first_seen": date.today().isoformat(),
            "last_contact": None,
            "status": "new"  # new / contacted / replied / booked
        }

    save_followups(db)

# ==========================================================
# 5. DETERMINISTIC SCORING
# ==========================================================
KEYWORDS = {
    "tender": ["tender", "rfp", "bid", "contract"],
    "win": ["won", "wins", "bags", "secures", "lands"],
    "appoint": ["appointed", "names", "hires"],
    "partner": ["partner", "collaboration"],
    "launch": ["launch", "unveil", "announce"],
}


def contains(text, words):
    return any(w in text for w in words)


def score(title, category):
    t = title.lower()
    s = 50

    if contains(t, KEYWORDS["tender"]): s += 30
    if contains(t, KEYWORDS["win"]): s += 25
    if contains(t, KEYWORDS["appoint"]): s += 20
    if contains(t, KEYWORDS["partner"]): s += 15
    if contains(t, KEYWORDS["launch"]): s += 15

    if category == "SPORTS" and ("bcci" in t or "ipl" in t):
        s += 10

    return min(s, 100)

# ==========================================================
# 6. SIGNAL FETCH + SMART DEDUPE
# ==========================================================

def similar(a, b):
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() > 0.85

COMMON_WORDS = {"india", "launch", "announces", "campaign", "ipl"}


def same_brand(a, b):
    wa = {w for w in a.lower().split() if w not in COMMON_WORDS}
    wb = {w for w in b.lower().split() if w not in COMMON_WORDS}
    return len(wa & wb) >= 1


def fetch_leads():
    queries = [
        '"Sports Authority of India" tender',
        '"BCCI" partner announced',
        '"IPL" sponsorship',
        '"won creative mandate" India',
        '"appointed" "Creative Director" India',
        '"campaign launch" TVC India',
    ]

    seen = get_seen_links()
    titles = []
    new_links = set()
    leads = []

    for q in queries:
        try:
            url = f"https://news.google.com/rss/search?q={quote(q)}&hl=en-IN&gl=IN&ceid=IN:en"
            feed = feedparser.parse(url)

            for e in feed.entries[:3]:
                if e.link in seen:
                    continue

                if any(similar(e.title, t) and same_brand(e.title, t) for t in titles):
                    continue

                category = "SPORTS" if any(k in q for k in ["BCCI", "IPL", "tender"]) else "ADS"
                sc = score(e.title, category)

                if sc >= 60:
                    leads.append({"title": e.title, "link": e.link, "score": sc})
                    register_followup(e.title, e.link)

                titles.append(e.title)
                new_links.add(e.link)

        except Exception as e:
            logging.error(f"Feed error {q}: {e}")

    if new_links:
        add_seen_links(new_links)

    leads.sort(key=lambda x: x["score"], reverse=True)
    return leads[:5]

# ==========================================================
# 7. AI REPORT (TEXT ONLY)
# ==========================================================

def generate_report(leads):
    if not leads:
        return None

    prompt = f"""
Write concise business strategy for these leads for photographer Saikat Das.
Leads:
{json.dumps(leads, indent=2)}
Plain text only.
"""

    try:
        r = model.generate_content(prompt, request_options={'timeout': 45})
        return r.text
    except Exception as e:
        logging.error(f"AI error: {e}")
        return None

# ==========================================================
# 8. SCOUT RUN
# ==========================================================

def update_last_run():
    save_json_atomic(STATE_FILE, {"last_run": date.today().isoformat()})


def needs_catchup():
    return load_json(STATE_FILE, {}).get("last_run") != date.today().isoformat()


def run_scout(catchup=False):
    leads = fetch_leads()

    update_last_run()  # idempotent BEFORE send

    if not leads:
        return

    report = generate_report(leads)
    if report:
        prefix = "CATCHUP" if catchup else "DAILY"
        bot.send_message(ADMIN_ID, f"{prefix} REPORT\n\n{report}")

# ==========================================================
# 9. SCHEDULER
# ==========================================================

def scheduler():
    ist = pytz.timezone("Asia/Kolkata")
    schedule.every().day.at("10:00").tz(ist).do(run_scout)

    while True:
        schedule.run_pending()
        time.sleep(60)


threading.Thread(target=scheduler, daemon=True).start()

# ==========================================================
# 10. COMMANDS
# ==========================================================

def admin_only(fn):
    def wrap(m):
        if m.from_user.id == ADMIN_ID:
            return fn(m)
    return wrap


@bot.message_handler(commands=["start"])
@admin_only
def start(m):
    bot.send_message(m.chat.id, "SAIKAT OS READY")

    if needs_catchup():
        bot.send_message(m.chat.id, "Running missed scout…")
        run_scout(catchup=True)


@bot.message_handler(commands=["scout"])
@admin_only
def scout(m):
    run_scout(catchup=True)

# ==========================================================
# 11. RUNNER (SELF‑HEALING)
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
