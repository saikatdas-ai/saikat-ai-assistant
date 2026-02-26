# bot.py -- SAIKAT OS Phase 2.27.1 (Hardened & Variable-Safe)
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
# 1. CORE SYSTEM INITIALIZATION (With Variable Fallback)
# ============================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# SAFETY: Railway Variables or Local Config
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_USER_ID")
DATA_DIR = os.getenv("DATA_DIR", "./data")

# If Variables are missing from Environment, try to load them from a local file
if not BOT_TOKEN or not ADMIN_ID:
    logging.warning("Environment variables missing. Searching for config.json...")
    if os.path.exists("config.json"):
        with open("config.json", "r") as f:
            cfg = json.load(f)
            BOT_TOKEN = cfg.get("TELEGRAM_TOKEN")
            ADMIN_ID = cfg.get("ADMIN_USER_ID")

if not BOT_TOKEN or not ADMIN_ID:
    logging.critical("FATAL: No Bot Token or Admin ID found. Bot cannot start.")
    sys.exit(1)

ADMIN_ID = int(ADMIN_ID)
bot = telebot.TeleBot(BOT_TOKEN)
os.makedirs(DATA_DIR, exist_ok=True)

# File Paths
SEEN_PATH = os.path.join(DATA_DIR, "seen_links.json")
QUEUE_PATH = os.path.join(DATA_DIR, "commercial_queue.json")
WATCH_PATH = os.path.join(DATA_DIR, "watchlist.json")

# Multi-Platform Tunables
MAX_SEEN = 5000
MAX_QUEUE = 1500
DEEP_FETCH_LIMIT = 8
PURGE_DAYS = 30

scan_lock = threading.Lock()
seen_lru = OrderedDict()
queue_lru = OrderedDict()

# ============================================================
# 2. STORAGE ENGINE (Self-Healing)
# ============================================================
def load_json_safe(path, default):
    if not os.path.exists(path): return default
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return data if data is not None else default
    except: return default

def save_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(data, f)
    os.replace(tmp, path)

def persist_state():
    cutoff = datetime.now(timezone.utc) - timedelta(days=PURGE_DAYS)
    # Persist Seen LRU
    clean_seen = [s for s in list(seen_lru.values()) if datetime.fromisoformat(s['date']) >= cutoff]
    save_json_atomic(SEEN_PATH, clean_seen)
    # Persist Queue LRU
    clean_queue = {k: v for k, v in dict(queue_lru).items() if not v.get("released") or datetime.fromisoformat(v['date']) >= cutoff}
    save_json_atomic(QUEUE_PATH, clean_queue)

def startup():
    global seen_lru, queue_lru
    s_list = load_json_safe(SEEN_PATH, [])
    if isinstance(s_list, list):
        for s in s_list[-MAX_SEEN:]: 
            if isinstance(s, dict) and "link" in s: seen_lru[s["link"]] = s
    q_dict = load_json_safe(QUEUE_PATH, {})
    if isinstance(q_dict, dict):
        sorted_q = sorted(q_dict.items(), key=lambda x: x[1].get("date", ""))
        for k, v in sorted_q[-MAX_QUEUE:]: queue_lru[k] = v

startup()

# ============================================================
# 3. DISCOVERY ENGINES
# ============================================================
RSS_FEEDS = [
    "https://www.exchange4media.com/rss.xml", "https://www.afaqs.com/rss.xml",
    "https://www.campaignindia.in/rss", "https://economictimes.indiatimes.com/marketing/rssfeeds/13352306.cms",
    "https://www.medianews4u.com/feed", "https://sportsmintmedia.com/feed/",
    "https://www.bestmediainfo.com/feed/", "https://www.adgully.com/rss.php"
]

def compile_re(items):
    clean = [re.escape(str(i)) for i in items if i]
    return re.compile(r"\b(?:" + "|".join(clean) + r")\b", re.IGNORECASE) if clean else None

def extract_content(html):
    if not html: return ""
    html = re.sub(r"<(script|style).*?>.*?</\1>", " ", html, flags=re.DOTALL|re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()

def discover(mode="ad"):
    wl = load_json_safe(WATCH_PATH, {})
    # Triangulated watchlist
    targets = (wl.get("athletes", []) + wl.get("teams", []) + wl.get("brands", []) + wl.get("conglomerates", []) + wl.get("execution_partners", []))
    
    re_ent = compile_re(targets)
    re_ag = compile_re(wl.get("agencies", []))
    re_money = compile_re(wl.get("commercial_keywords", []) + ["partner", "mandate", "onboards", "signs", "account move"])
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

            # --- GATE LOGIC ---
            has_money = re_money.search(text) if re_money else False
            has_ent = re_ent.search(text) if re_ent else False
            has_ag = re_ag.search(text) if re_ag else False

            if mode == "ad":
                # Ad leads MUST have money words or agency words
                if not (has_money or has_ag): continue
            elif mode == "sports":
                # Sports leads just need entity match
                if not has_ent: continue

            deep_text = ""
            if deep_count < DEEP_FETCH_LIMIT:
                r = requests.get(link, headers={"User-Agent": "SAIKAT-OS/2.27"}, timeout=8)
                deep_text = extract_content(r.text).lower() if r.status_code == 200 else ""
                deep_count += 1
            
            full_text = text + " " + deep_text
            sig = hashlib.md5(title.lower().encode()).hexdigest()
            if sig in queue_lru: continue

            # Scoring
            score = 60
            if has_ag or re_ag.search(full_text): score += 20
            if "mandate" in full_text: score += 10
            if re_home.search(full_text): score += 10 # Kolkata Bonus
            
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
# 4. DELIVERY ENGINE
# ============================================================
def build_report(mode="ad"):
    items = [v for v in queue_lru.values() if not v.get("released") and v.get("type") == mode.upper()]
    if not items: return None

    items.sort(key=lambda x: x.get("score", 0), reverse=True)
    report = f"🚀 SAIKAT OS 2.27 {mode.upper()} RADAR\n" + "="*25 + "\n\n"
    
    for it in items:
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
        bot.send_message(message.chat.id, "⚠️ Scan in progress...")
        return

    def run():
        try:
            bot.send_message(message.chat.id, f"🔍 Initiating {mode.upper()} Scan...")
            discover(mode)
            rep = build_report(mode)
            if rep:
                for chunk in [rep[i:i+3500] for i in range(0, len(rep), 3500)]:
                    bot.send_message(message.chat.id, chunk)
            else:
                bot.send_message(message.chat.id, f"✅ No new {mode} leads.")
        finally:
            scan_lock.release()

    threading.Thread(target=run, daemon=True).start()

if __name__ == "__main__":
    bot.infinity_polling(timeout=30)
