# bot.py -- SAIKAT OS Phase 2.27 (Modular / Android & Cloud Ready)
import os
import sys
import json
import time
import logging
import threading
import requests
import re
import hashlib
import calendar
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import feedparser
from urllib.parse import quote
import telebot

# ============================================================
# 1. CORE SYSTEM INITIALIZATION
# ============================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# These will load from Environment Variables on Railway
# or can be hardcoded in a .env file on your Poco X6
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_USER_ID")
DATA_DIR = os.getenv("DATA_DIR", "./data")

if not BOT_TOKEN or not ADMIN_ID:
    logging.critical("FATAL: Environment Variables Missing.")
    sys.exit(1)

ADMIN_ID = int(ADMIN_ID)
bot = telebot.TeleBot(BOT_TOKEN)

# Ensure data directory is portable (Android vs Cloud)
os.makedirs(DATA_DIR, exist_ok=True)

SEEN_PATH = os.path.join(DATA_DIR, "seen_links.json")
QUEUE_PATH = os.path.join(DATA_DIR, "commercial_queue.json")
WATCH_PATH = os.path.join(DATA_DIR, "watchlist.json")

# Multi-Platform Performance Tunables
MAX_SEEN = 5000               # RAM cap for the Poco X6 (Safe for 12GB)
MAX_QUEUE = 1500              # Sync cap
DEEP_FETCH_LIMIT = 8          # Bandwidth protection
PURGE_DAYS = 30               # Data hygiene

# State Structures
scan_lock = threading.Lock()
seen_lru = OrderedDict()
queue_lru = OrderedDict()

# ============================================================
# 2. THE STORAGE MODULE (Atomic & Self-Healing)
# ============================================================
def load_json_safe(path, default):
    if not os.path.exists(path): return default
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return data if data else default
    except:
        return default

def save_json_atomic(path, data):
    # This prevents file corruption if the Poco X6 battery dies
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(data, f)
    os.replace(tmp, path)

def persist_state():
    # Purge logic to keep the Poco X6 disk clean
    cutoff = datetime.now(timezone.utc) - timedelta(days=PURGE_DAYS)
    
    clean_seen = [s for s in list(seen_lru.values()) if datetime.fromisoformat(s['date']) >= cutoff]
    
    clean_queue = {}
    for k, v in dict(queue_lru).items():
        if not v.get("released") or datetime.fromisoformat(v['date']) >= cutoff:
            clean_queue[k] = v

    save_json_atomic(SEEN_PATH, clean_seen)
    save_json_atomic(QUEUE_PATH, clean_queue)

def startup():
    global seen_lru, queue_lru
    s_list = load_json_safe(SEEN_PATH, [])
    if isinstance(s_list, list):
        for s in s_list[-MAX_SEEN:]: 
            if isinstance(s, dict) and "link" in s: seen_lru[s["link"]] = s
    
    q_dict = load_json_safe(QUEUE_PATH, {})
    if isinstance(q_dict, dict):
        # Restore chronology
        sorted_q = sorted(q_dict.items(), key=lambda x: x[1].get("date", ""))
        for k, v in sorted_q[-MAX_QUEUE:]: queue_lru[k] = v

startup()

# ============================================================
# 3. CONTEXTUAL ENGINES (Regex & Logic)
# ============================================================
def compile_re(items):
    if not items: return None
    clean = [re.escape(str(i)) for i in items if i]
    return re.compile(r"\b(?:" + "|".join(clean) + r")\b", re.IGNORECASE)

def http_fetch(url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 SAIKAT-OS/2.27"}, timeout=10)
        return r.text if r.status_code == 200 else ""
    except: return ""

def extract_content(html):
    if not html: return ""
    html = re.sub(r"<(script|style).*?>.*?</\1>", " ", html, flags=re.DOTALL|re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()

# ============================================================
# 4. DISCOVERY ENGINE (The "Titan" Brain)
# ============================================================
RSS_FEEDS = [
    "https://www.exchange4media.com/rss.xml", "https://www.afaqs.com/rss.xml",
    "https://www.campaignindia.in/rss", "https://economictimes.indiatimes.com/marketing/rssfeeds/13352306.cms",
    "https://www.medianews4u.com/feed", "https://sportsmintmedia.com/feed/",
    "https://www.bestmediainfo.com/feed/", "https://www.adgully.com/rss.php"
]

def discover(mode="ad"):
    wl = load_json_safe(WATCH_PATH, {})
    
    # Modular Target Building
    targets = (wl.get("athletes", []) + wl.get("teams", []) + 
               wl.get("brands", []) + wl.get("conglomerates", []) + 
               wl.get("execution_partners", []))
    
    re_ent = compile_re(targets)
    re_ag = compile_re(wl.get("agencies", []))
    re_money = compile_re(wl.get("commercial_keywords", []) + ["partner", "mandate", "signs", "inks"])
    re_home = re.compile(r"\b(Kolkata|Howrah|ITC|Berger|Lux|Exide)\b", re.I)

    for feed_url in RSS_FEEDS:
        feed = feedparser.parse(feed_url)
        deep_count = 0
        for entry in feed.entries[:25]:
            link = getattr(entry, "link", "")
            if not link or link in seen_lru: continue

            title = getattr(entry, "title", "")
            summary = getattr(entry, "summary", "")
            text = (title + " " + summary).lower()

            # --- GATE LOGIC (Precision Strike) ---
            has_money = re_money.search(text) if re_money else False
            has_ag = re_ag.search(text) if re_ag else False
            has_ent = re_ent.search(text) if re_ent else False

            # Allow high-value signals only
            if mode == "ad":
                if not (has_money and (has_ent or has_ag)): continue
            
            # Deep Fetch Contextualization
            deep_text = ""
            if deep_count < DEEP_FETCH_LIMIT:
                deep_text = extract_content(http_fetch(link)).lower()
                deep_count += 1
            
            full_text = text + " " + deep_text
            sig = hashlib.md5(title.lower().encode()).hexdigest()
            if sig in queue_lru: continue

            # --- DYNAMIC SCORING ---
            score = 60
            if has_ag: score += 20
            if "mandate" in full_text: score += 10
            if re_home.search(full_text): score += 5 # Kolkata Bonus
            
            # High Priority Campaign Formula
            if (has_ent or re_ent.search(full_text)) and (has_ag or re_ag.search(full_text)):
                score = 100

            queue_lru[sig] = {
                "title": title, "link": link, "score": min(score, 100),
                "released": False, "date": datetime.now(timezone.utc).isoformat(),
                "type": mode.upper()
            }
            seen_lru[link] = {"link": link, "date": datetime.now(timezone.utc).isoformat()}
            if len(seen_lru) > MAX_SEEN: seen_lru.popitem(last=False)

    persist_state()

# ============================================================
# 5. OUTREACH & DELIVERY (LinkedIn Engine)
# ============================================================
def build_report():
    items = [v for v in queue_lru.values() if not v.get("released")]
    if not items: return None

    items.sort(key=lambda x: x.get("score", 0), reverse=True)
    report = "🚀 SAIKAT OS 2.27 RADAR\n" + "="*25 + "\n\n"
    
    for it in items:
        # Smart Brand Guessing
        match = re.search(r"([A-Z][\w&]+)", it['title'])
        brand = match.group(1) if match else "Marketing Head"
        linkedin = f"https://www.linkedin.com/search/results/people/?keywords={quote(brand + ' Marketing Head')}"

        report += f"🔥 {it['title']}\n"
        report += f"🎯 Score: {it['score']}/100\n"
        report += f"🔗 LinkedIn: {linkedin}\n"
        report += f"📂 Source: {it['link']}\n"
        report += "-"*25 + "\n\n"
        it["released"] = True
    
    persist_state()
    return report

@bot.message_handler(commands=["leads-ad", "leads-sports"])
def handle_commands(message):
    if message.from_user.id != ADMIN_ID: return
    mode = "ad" if "ad" in message.text else "sports"
    
    if not scan_lock.acquire(blocking=False):
        bot.send_message(message.chat.id, "⚠️ A scan is currently in progress.")
        return

    def run():
        try:
            bot.send_message(message.chat.id, f"🔍 Initiating {mode.upper()} Scan...")
            discover(mode)
            rep = build_report()
            if rep:
                for chunk in [rep[i:i+3500] for i in range(0, len(rep), 3500)]:
                    bot.send_message(message.chat.id, chunk)
            else:
                bot.send_message(message.chat.id, f"✅ Scan Finished. No new {mode} leads.")
        finally:
            scan_lock.release()

    threading.Thread(target=run, daemon=True).start()

if __name__ == "__main__":
    bot.infinity_polling(timeout=30)
