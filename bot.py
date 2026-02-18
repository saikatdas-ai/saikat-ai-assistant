SAIKAT OS - Diagnostic Visibility + Safe Exploration Build

Audited, regression-fixed, Phase-3-ready scanner

import os import sys import telebot import google.generativeai as genai import feedparser import time import json import logging import difflib from urllib.parse import quote from datetime import datetime, date, timedelta

==========================================================

1. CONFIG + SAFE BOOT

==========================================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN") GEMINI_KEY = os.environ.get("GEMINI_API_KEY") ADMIN_ID = os.environ.get("ADMIN_USER_ID")

if not BOT_TOKEN or not GEMINI_KEY or not ADMIN_ID: logging.critical("Missing TELEGRAM_TOKEN / GEMINI_API_KEY / ADMIN_USER_ID") sys.exit(1)

ADMIN_ID = int(ADMIN_ID)

bot = telebot.TeleBot(BOT_TOKEN)

genai.configure(api_key=GEMINI_KEY)

--- Intelligent model auto-selection (future-proof) ---

def select_best_model(): try: models = genai.list_models() # Prefer newest Gemini models that support generateContent candidates = [m.name for m in models if "generateContent" in m.supported_generation_methods]

# Priority order: 2.x flash → 2.x → 1.5 flash → fallback
    priority = [
        "models/gemini-2.5-flash",
        "models/gemini-2.0-flash",
        "models/gemini-2.0-pro",
        "models/gemini-1.5-flash",
    ]

    for p in priority:
        if p in candidates:
            logging.info(f"Using Gemini model: {p}")
            return p

    # If none matched, just use first valid candidate
    if candidates:
        logging.info(f"Using fallback Gemini model: {candidates[0]}")
        return candidates[0]

except Exception as e:
    logging.error(f"Model discovery failed: {e}")

# Absolute safe fallback
return "models/gemini-1.5-flash"

model = genai.GenerativeModel(select_best_model())

==========================================================

2. STORAGE (ATOMIC + PRUNED + CRM-SAFE)

==========================================================

DATA_DIR = "/app/data" SEEN_FILE = os.path.join(DATA_DIR, "seen_links.json") FOLLOW_FILE = os.path.join(DATA_DIR, "followups.json")

os.makedirs(DATA_DIR, exist_ok=True)

def load_json(path, default): try: if os.path.exists(path): with open(path, "r") as f: return json.load(f) except Exception as e: logging.error(f"Read error {path}: {e}") return default

def save_json_atomic(path, data): tmp = path + ".tmp" try: with open(tmp, "w") as f: json.dump(data, f) os.replace(tmp, path) except Exception as e: logging.error(f"Write error {path}: {e}")

------------------ PRUNED PERFORMANCE MEMORY ------------------

def prune_seen(days=30): data = load_json(SEEN_FILE, []) cutoff = datetime.utcnow() - timedelta(days=days)

fresh = [
    i for i in data
    if isinstance(i, dict)
    and "date" in i
    and datetime.fromisoformat(i["date"]).replace(tzinfo=None) > cutoff
]

save_json_atomic(SEEN_FILE, fresh)

def get_seen_links(): prune_seen() return {i["link"] for i in load_json(SEEN_FILE, []) if isinstance(i, dict)}

def add_seen_links(links): existing = load_json(SEEN_FILE, []) now = datetime.utcnow().isoformat()

for l in links:
    existing.append({"link": l, "date": now})

save_json_atomic(SEEN_FILE, existing)

------------------ PERMANENT CRM MEMORY ------------------

def load_followups(): return load_json(FOLLOW_FILE, {})

def save_followups(db): save_json_atomic(FOLLOW_FILE, db)

def register_followup(title, link): db = load_followups()

if link not in db:
    db[link] = {
        "title": title,
        "first_seen": date.today().isoformat(),
        "last_contact": None,
        "status": "new",
        "notes": "",
        "deal_value": None,
    }

save_followups(db)

==========================================================

3. DETERMINISTIC SCORING (LOW-GUARD SAFE)

==========================================================

THRESHOLD = 10

KEYWORDS = { "tender": ["tender", "rfp", "bid", "contract", "procurement"], "win": ["won", "wins", "bags", "secures", "lands", "mandate"], "appoint": ["appointed", "names", "hires"], "partner": ["partner", "sponsorship"], "launch": ["launch", "campaign", "announce"], }

def calculate_score(title, category): t = title.lower() s = 10

if any(w in t for w in KEYWORDS["tender"]): s += 40
if any(w in t for w in KEYWORDS["win"]): s += 30
if any(w in t for w in KEYWORDS["appoint"]): s += 20
if category == "SPORTS" and ("bcci" in t or "ipl" in t): s += 20

return min(s, 100)

==========================================================

4. SMART DEDUPE (RESTORED + SAFE)

==========================================================

COMMON = {"india", "campaign", "launch", "announces", "ipl"}

def similar(a, b): return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() > 0.85

def same_brand(a, b): wa = {w for w in a.lower().split() if w not in COMMON} wb = {w for w in b.lower().split() if w not in COMMON} return len(wa & wb) >= 1

==========================================================

5. FUNNEL SCAN + DIAGNOSTICS

==========================================================

def fetch_funnel(category): past_date = (date.today() - timedelta(days=90)).strftime('%Y-%m-%d')

if category == "SPORTS":
    queries = [
        f'"BCCI" partner after:{past_date}',
        f'"IPL" sponsorship after:{past_date}',
        f'"Sports Authority of India" tender after:{past_date}',
    ]
    label = "SPORTS"
else:
    queries = [
        f'"won creative mandate" India after:{past_date}',
        f'"appointed" "Creative Director" India after:{past_date}',
        f'"campaign launch" TVC India after:{past_date}',
    ]
    label = "ADS"

seen = get_seen_links()
titles = []
new_links = set()
leads = []

scanned = 0

for q in queries:
    try:
        url = f"https://news.google.com/rss/search?q={quote(q)}&hl=en-IN&gl=IN&ceid=IN:en"
        feed = feedparser.parse(url)

        for e in feed.entries[:10]:
            scanned += 1

            if e.link in seen:
                continue

            if any(similar(e.title, t) and same_brand(e.title, t) for t in titles):
                continue

            sc = calculate_score(e.title, label)

            if sc >= THRESHOLD:
                leads.append({"title": e.title, "link": e.link, "score": sc, "cat": label})
                register_followup(e.title, e.link)

            titles.append(e.title)
            new_links.add(e.link)

    except Exception as e:
        logging.error(f"Feed error {q}: {e}")

if new_links:
    add_seen_links(new_links)

leads.sort(key=lambda x: x["score"], reverse=True)

diagnostics = {
    "scanned": scanned,
    "kept": len(leads),
    "threshold": THRESHOLD,
}

return leads[:10], diagnostics

==========================================================

6. AI REPORT

==========================================================

def generate_report(leads): if not leads: return "No viable leads found."

prompt = f"Write concise outreach strategy for Saikat Das:\n{json.dumps(leads)}"

try:
    r = model.generate_content(prompt, request_options={'timeout': 45})
    return r.text
except Exception as e:
    logging.error(e)
    return "AI error."

==========================================================

7. COMMANDS

==========================================================

def admin_only(fn): def wrap(m): if m.from_user.id == ADMIN_ID: return fn(m) return wrap

@bot.message_handler(commands=["start"]) @admin_only def start_cmd(m): bot.send_message(m.chat.id, f"LOW-GUARD MODE ACTIVE (threshold={THRESHOLD})\nCommands: /leads-sports /leads-ad")

@bot.message_handler(commands=["leads-sports"]) @admin_only def sports_cmd(m): bot.send_message(m.chat.id, "Scanning SPORTS funnel…") leads, diag = fetch_funnel("SPORTS")

report = generate_report(leads)

bot.send_message(
    m.chat.id,
    f"SPORTS DIAGNOSTIC\nScanned: {diag['scanned']}\nKept: {diag['kept']}\nThreshold: {diag['threshold']}\n\n{report}",
)

@bot.message_handler(commands=["leads-ad"]) @admin_only def ads_cmd(m): bot.send_message(m.chat.id, "Scanning ADS funnel…") leads, diag = fetch_funnel("ADS")

report = generate_report(leads)

bot.send_message(
    m.chat.id,
    f"ADS DIAGNOSTIC\nScanned: {diag['scanned']}\nKept: {diag['kept']}\nThreshold: {diag['threshold']}\n\n{report}",
)

==========================================================

8. RUNNER (SELF-HEALING)

==========================================================

if name == "main": delay = 5

while True:
    try:
        bot.infinity_polling(timeout=20, long_polling_timeout=10)
        delay = 5
    except Exception as e:
        logging.error(f"Crash: {e}")
        time.sleep(delay)
        delay = min(delay * 2, 300)
