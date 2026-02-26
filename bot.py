# bot.py -- SAIKAT OS Phase 2.27.2 (The Final Sovereign Build)
import os, sys, json, time, logging, threading, requests, re, hashlib, calendar
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import feedparser
from urllib.parse import quote
import telebot

# 1. INITIALIZATION
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Hardcoded Search logic for Token/ID
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_USER_ID")

if not BOT_TOKEN or not ADMIN_ID:
    if os.path.exists("config.json"):
        with open("config.json", "r") as f:
            cfg = json.load(f)
            BOT_TOKEN = cfg.get("TELEGRAM_TOKEN")
            ADMIN_ID = cfg.get("ADMIN_USER_ID")

if not BOT_TOKEN or not ADMIN_ID:
    print("--- CRITICAL ERROR: NO CREDENTIALS FOUND ---")
    print("Please create config.json or set Railway variables.")
    sys.exit(1)

ADMIN_ID = int(ADMIN_ID)
bot = telebot.TeleBot(BOT_TOKEN)
DATA_DIR = os.getenv("DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)

# File Paths
SEEN_PATH = os.path.join(DATA_DIR, "seen_links.json")
QUEUE_PATH = os.path.join(DATA_DIR, "commercial_queue.json")
WATCH_PATH = os.path.join(DATA_DIR, "watchlist.json")

# State Management
scan_lock = threading.Lock()
seen_lru = OrderedDict()
queue_lru = OrderedDict()

# 2. STORAGE UTILS
def load_json_safe(path, default):
    if not os.path.exists(path): return default
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return data if data else default
    except: return default

def save_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(data, f)
    os.replace(tmp, path)

def persist_state():
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    save_json_atomic(SEEN_PATH, [s for s in list(seen_lru.values()) if datetime.fromisoformat(s['date']) >= cutoff])
    save_json_atomic(QUEUE_PATH, {k: v for k, v in dict(queue_lru).items() if not v.get("released") or datetime.fromisoformat(v['date']) >= cutoff})

def startup():
    global seen_lru, queue_lru
    s_list = load_json_safe(SEEN_PATH, [])
    if isinstance(s_list, list):
        for s in s_list[-5000:]: 
            if isinstance(s, dict) and "link" in s: seen_lru[s["link"]] = s
    q_dict = load_json_safe(QUEUE_PATH, {})
    if isinstance(q_dict, dict):
        sorted_q = sorted(q_dict.items(), key=lambda x: x[1].get("date", ""))
        for k, v in sorted_q[-1500:]: queue_lru[k] = v

startup()

# 3. SCAN ENGINE
RSS_FEEDS = [
    "https://www.exchange4media.com/rss.xml", "https://www.afaqs.com/rss.xml",
    "https://www.campaignindia.in/rss", "https://economictimes.indiatimes.com/marketing/rssfeeds/13352306.cms",
    "https://www.medianews4u.com/feed", "https://sportsmintmedia.com/feed/",
    "https://www.bestmediainfo.com/feed/", "https://www.adgully.com/rss.php"
]

def compile_re(items):
    clean = [re.escape(str(i)) for i in items if i]
    return re.compile(r"\b(?:" + "|".join(clean) + r")\b", re.IGNORECASE) if clean else None

def discover(mode="ad"):
    wl = load_json_safe(WATCH_PATH, {})
    targets = (wl.get("athletes", []) + wl.get("teams", []) + wl.get("brands", []) + wl.get("conglomerates", []) + wl.get("execution_partners", []))
    
    re_ent = compile_re(targets)
    re_ag = compile_re(wl.get("agencies", []))
    re_money = compile_re(wl.get("commercial_keywords", []) + ["partner", "mandate", "onboards", "signs", "account win"])
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

            has_money = re_money.search(text) if re_money else False
            has_ent = re_ent.search(text) if re_ent else False
            has_ag = re_ag.search(text) if re_ag else False

            if mode == "ad":
                if not (has_money or has_ag): continue
            elif mode == "sports":
                if not has_ent: continue

            full_text = text
            if deep_count < 8:
                try:
                    r = requests.get(link, headers={"User-Agent": "SAIKAT-OS/2.27"}, timeout=8)
                    if r.status_code == 200:
                        clean = re.sub(r"<(script|style).*?>.*?</\1>", " ", r.text, flags=re.DOTALL|re.IGNORECASE)
                        full_text += " " + re.sub(r"<[^>]+>", " ", clean).lower()
                        deep_count += 1
                except: pass
            
            sig = hashlib.md5(title.lower().encode()).hexdigest()
            if sig in queue_lru: continue

            score = 65
            if has_ag or (re_ag and re_ag.search(full_text)): score += 20
            if "mandate" in full_text: score += 10
            if re_home.search(full_text): score += 5
            
            if (has_ent or (re_ent and re_ent.search(full_text))) and (has_ag or (re_ag and re_ag.search(full_text))):
                score = 100

            queue_lru[sig] = {
                "title": title, "link": link, "score": min(score, 100),
                "released": False, "date": datetime.now(timezone.utc).isoformat(),
                "type": mode.upper()
            }
            seen_lru[link] = {"link": link, "date": datetime.now(timezone.utc).isoformat()}
            if len(seen_lru) > 5000: seen_lru.popitem(last=False)
    persist_state()

# 4. HANDLERS
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
            items = [v for v in queue_lru.values() if not v.get("released") and v.get("type") == mode.upper()]
            if not items:
                bot.send_message(message.chat.id, f"✅ No new {mode} leads.")
                return
            items.sort(key=lambda x: x.get("score", 0), reverse=True)
            report = f"🚀 SAIKAT OS 2.27 {mode.upper()} RADAR\n" + "="*25 + "\n\n"
            for it in items:
                match = re.search(r"([A-Z][\w&]+)", it['title'])
                brand = match.group(1) if match else "Marketing"
                report += f"🔥 {it['title']}\n🎯 Score: {it['score']}/100\n🔗 LinkedIn: https://www.linkedin.com/search/results/people/?keywords={quote(brand + ' Marketing Head')}\n📂 Source: {it['link']}\n" + "-"*25 + "\n\n"
                it["released"] = True
            for chunk in [report[i:i+3500] for i in range(0, len(report), 3500)]: bot.send_message(message.chat.id, chunk)
            persist_state()
        finally: scan_lock.release()
    threading.Thread(target=run, daemon=True).start()

if __name__ == "__main__":
    bot.infinity_polling(timeout=30)
