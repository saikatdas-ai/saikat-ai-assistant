import os
import sys
import telebot
import google.generativeai as genai
import feedparser
import time
import json
import logging
import difflib
from urllib.parse import quote
from datetime import datetime, date, timedelta

# --- 1. CONFIG & SAFE BOOT ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
ADMIN_ID = os.environ.get("ADMIN_USER_ID")

if not BOT_TOKEN or not GEMINI_KEY or not ADMIN_ID:
    logging.critical("Missing TELEGRAM_TOKEN / GEMINI_API_KEY / ADMIN_USER_ID")
    sys.exit(1)

ADMIN_ID = int(ADMIN_ID)
bot = telebot.TeleBot(BOT_TOKEN)
genai.configure(api_key=GEMINI_KEY)

def select_best_model():
    try:
        models = genai.list_models()
        candidates = [m.name for m in models if "generateContent" in m.supported_generation_methods]
        priority = ["models/gemini-2.0-flash", "models/gemini-1.5-flash"]
        for p in priority:
            if p in candidates: return p
        return candidates[0] if candidates else "models/gemini-1.5-flash"
    except: return "models/gemini-1.5-flash"

model = genai.GenerativeModel(select_best_model())

# --- 2. STORAGE ---
DATA_DIR = "/app/data"
SEEN_FILE = os.path.join(DATA_DIR, "seen_links.json")
FOLLOW_FILE = os.path.join(DATA_DIR, "followups.json")
os.makedirs(DATA_DIR, exist_ok=True)

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r") as f: return json.load(f)
        except: pass
    return default

def save_json_atomic(path, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f: json.dump(data, f)
        os.replace(tmp, path)
    except Exception as e: logging.error(f"Write error: {e}")

# --- 3. DEDUPE & SCORING ---
THRESHOLD = 10
COMMON = {"india", "campaign", "launch", "announces", "ipl"}

def similar(a, b): return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() > 0.85

def same_brand(a, b):
    wa = {w for w in a.lower().split() if w not in COMMON and len(w) > 3}
    wb = {w for w in b.lower().split() if w not in COMMON and len(w) > 3}
    return len(wa & wb) >= 1

def calculate_score(title, category):
    t = title.lower()
    s = 10
    if any(w in t for w in ["tender", "rfp", "bid"]): s += 40
    if any(w in t for w in ["won", "wins", "mandate"]): s += 30
    if category == "SPORTS" and ("bcci" in t or "ipl" in t): s += 20
    return min(s, 100)

# --- 4. FUNNEL SCAN ---
def fetch_funnel(category):
    past_date = (date.today() - timedelta(days=90)).strftime('%Y-%m-%d')
    queries = {
        "SPORTS": [f'"BCCI" after:{past_date}', f'"IPL" sponsorship after:{past_date}', f'tender "sports" after:{past_date}'],
        "ADS": [f'"won creative mandate" after:{past_date}', f'"Creative Director" appointed after:{past_date}']
    }[category]

    seen = {i["link"] for i in load_json(SEEN_FILE, []) if isinstance(i, dict)}
    titles = []
    leads = []
    new_links = []
    scanned = 0

    for q in queries:
        try:
            url = f"https://news.google.com/rss/search?q={quote(q)}&hl=en-IN&gl=IN&ceid=IN:en"
            feed = feedparser.parse(url)
            for e in feed.entries[:10]:
                scanned += 1
                if e.link in seen: continue
                if any(similar(e.title, t) and same_brand(e.title, t) for t in titles): continue
                
                sc = calculate_score(e.title, category)
                if sc >= THRESHOLD:
                    leads.append({"title": e.title, "link": e.link, "score": sc})
                    # Save to permanent CRM
                    db = load_json(FOLLOW_FILE, {})
                    if e.link not in db: db[e.link] = {"title": e.title, "seen": date.today().isoformat()}
                    save_json_atomic(FOLLOW_FILE, db)
                
                titles.append(e.title)
                new_links.append({"link": e.link, "date": datetime.utcnow().isoformat()})
        except: continue

    if new_links:
        existing = load_json(SEEN_FILE, [])
        save_json_atomic(SEEN_FILE, existing + new_links)
    
    leads.sort(key=lambda x: x["score"], reverse=True)
    return leads[:10], scanned

# --- 5. COMMANDS ---
@bot.message_handler(commands=["leads-sports"])
def sports_cmd(m):
    if m.from_user.id != ADMIN_ID: return
    bot.send_message(m.chat.id, "🔍 Searching 90-day Sports Archives...")
    leads, count = fetch_funnel("SPORTS")
    report = f"Scanned: {count} | Kept: {len(leads)}\n\n" + "\n\n".join([f"({l['score']}) {l['title']}\n{l['link']}" for l in leads])
    bot.send_message(m.chat.id, report)

@bot.message_handler(commands=["leads-ad"])
def ads_cmd(m):
    if m.from_user.id != ADMIN_ID: return
    bot.send_message(m.chat.id, "🔍 Searching 90-day Advertising Archives...")
    leads, count = fetch_funnel("ADS")
    report = f"Scanned: {count} | Kept: {len(leads)}\n\n" + "\n\n".join([f"({l['score']}) {l['title']}\n{l['link']}" for l in leads])
    bot.send_message(m.chat.id, report)

# --- 6. RUNNER ---
if __name__ == "__main__":
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
