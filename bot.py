import os, sys, json, time, logging, threading, requests, re, hashlib
import feedparser
from urllib.parse import quote, urlparse
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

# File Paths
SEEN_FILE = os.path.join(DATA_DIR, "seen_links.json")
QUEUE_FILE = os.path.join(DATA_DIR, "commercial_queue.json")
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.json")

# ============================================================
# STABILITY UTILITIES
# ============================================================
def load_json(path, default):
    if not os.path.exists(path): return default
    try:
        with open(path, "r") as f: return json.load(f)
    except: return default

def save_json_atomic(path, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f: json.dump(data, f)
        os.replace(tmp, path)
    except Exception as e: logging.error(f"Write error: {e}")

def http_fetch(url, timeout=10):
    try:
        headers = {"User-Agent": "Mozilla/5.0 SAIKAT-OS/2.24 (Market Intelligence Master)"}
        r = requests.get(url, headers=headers, timeout=timeout)
        return r.text if r.status_code == 200 else ""
    except: return ""

def extract_text(html):
    if not html: return ""
    html = re.sub(r"<(script|style).*?>.*?</\1>", "", html, flags=re.DOTALL|re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()

# ============================================================
# REVENUE ENGINE: 2026 CONGLOMERATE EDITION
# ============================================================
RSS_FEEDS = [
    "https://www.exchange4media.com/rss.xml",
    "https://www.afaqs.com/rss.xml",
    "https://www.campaignindia.in/rss",
    "https://economictimes.indiatimes.com/marketing/rssfeeds/13352306.cms",
    "https://www.medianews4u.com/feed",
    "https://sportsmintmedia.com/feed/",
    "https://www.bestmediainfo.com/feed/",
    "https://www.adgully.com/rss.php"
]

def generate_linkedin_url(keyword, role):
    query = f"{keyword} {role}"
    return f"https://www.linkedin.com/search/results/people/?keywords={quote(query)}"

def discover_commercial():
    wl = load_json(WATCHLIST_FILE, {})
    seen = load_json(SEEN_FILE, [])
    seen_links = {x["link"] for x in seen}
    queue = load_json(QUEUE_FILE, {})

    # Hardcoded generic triggers for robustness
    money_words = wl.get('commercial_keywords', []) + ["partner", "mandate", "signs", "appoint"]
    geo_keywords = wl.get('cities', []) + ["india", "ipl", "bcci", "isl", "wpl"]

    for feed_url in RSS_FEEDS:
        feed = feedparser.parse(feed_url)
        deep_fetch_count = 0 

        for entry in feed.entries[:25]:
            if entry.link in seen_links: continue
            
            title = entry.title
            summary = getattr(entry, "summary", "")
            stage1_text = (title + " " + summary).lower()

            # TIERED GATE: Commercial Trigger + (Geo OR Athlete OR Conglomerate)
            has_money = any(mw.lower() in stage1_text for mw in money_words)
            has_geo = any(gk.lower() in stage1_text for gk in geo_keywords)
            matched_athlete = [a for a in wl.get('athletes', []) if a.lower() in stage1_text]
            matched_conglom = [c for c in wl.get('conglomerates', []) if c.lower() in stage1_text]

            if not has_money and "mandate" not in stage1_text: continue
            if not (has_geo or matched_athlete or matched_conglom):
                if "mandate" not in stage1_text: continue

            # Deep Fetch (Max 8 per feed)
            deep_text = ""
            if deep_fetch_count < 8:
                deep_text = extract_text(http_fetch(entry.link)).lower()
                deep_fetch_count += 1

            full_text = stage1_text + " " + deep_text
            
            detected_athletes = matched_athlete or [a for a in wl.get('athletes', []) if a.lower() in full_text]
            detected_agencies = [ag for ag in wl.get('agencies', []) if ag.lower() in full_text]
            detected_conglom = matched_conglom or [c for c in wl.get('conglomerates', []) if c.lower() in full_text]

            score = 60
            deal_type = "COMMERCIAL SIGNAL"

            if detected_conglom: score += 15; deal_type = "CONGLOMERATE DEAL"
            if detected_athletes: score += 15; deal_type = "ATHLETE ENDORSEMENT"
            if detected_agencies: score += 20; deal_type = "AGENCY MANDATE"
            
            if (detected_conglom or detected_athletes) and detected_agencies:
                deal_type = "🚨 HIGH PRIORITY: CAMPAIGN PITCH"
                score = 100

            sig = hashlib.md5(entry.link.encode()).hexdigest()
            queue[sig] = {
                "type": deal_type, "title": title, "link": entry.link,
                "conglom": detected_conglom, "athletes": detected_athletes,
                "agencies": detected_agencies, "score": min(score, 100),
                "released": False, "date": datetime.now(timezone.utc).isoformat()
            }
            seen.append({"link": entry.link, "date": datetime.now(timezone.utc).isoformat()})

    save_json_atomic(SEEN_FILE, seen)
    save_json_atomic(QUEUE_FILE, queue)

def build_report():
    queue = load_json(QUEUE_FILE, {})
    items = [v for v in queue.values() if not v.get("released")]
    if not items: return None

    items.sort(key=lambda x: x.get("score", 0), reverse=True)
    report = "📊 REVENUE RADAR 2.24: CONGLOMERATE WATCH\n" + "="*30 + "\n\n"

    for item in items:
        report += f"STATUS: {item['type']}\n"
        report += f"SCORE: {item['score']}/100\n"
        report += f"TITLE: {item['title']}\n"
        if item['conglom']: report += f"ENTITY: {', '.join(item['conglom'])}\n"
        if item['agencies']: report += f"AGENCY: {', '.join(item['agencies'])}\n"
        
        report += f"LINK: {item['link']}\n\n"
        report += "🎯 STRIKE ACTION (LinkedIn):\n"
        
        target = item['conglom'][0] if item['conglom'] else "Brand Marketing Head"
        report += f"- Brand Lead: {generate_linkedin_url(target, 'Marketing Head')}\n"
        if item['agencies']:
            report += f"- Creative Lead: {generate_linkedin_url(item['agencies'][0], 'Creative Director')}\n"
        
        report += "\n" + "-"*30 + "\n\n"
        item["released"] = True

    save_json_atomic(QUEUE_FILE, queue)
    return report

@bot.message_handler(commands=["leads-ad"])
def handle_ads(message):
    if message.from_user.id != ADMIN_ID: return
    def task():
        bot.send_message(message.chat.id, "Scanning Ad Trade & Conglomerate News...")
        discover_commercial()
        rep = build_report()
        if rep:
            for chunk in rep.split("-" * 30):
                if chunk.strip(): bot.send_message(message.chat.id, chunk + "-" * 30)
        else:
            bot.send_message(message.chat.id, "No high-value commercial deals found.")
    threading.Thread(target=task).start()

if __name__ == "__main__":
    logging.info("SAIKAT OS 2.24 ONLINE - MONETIZATION MODE")
    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=20)
        except Exception as e:
            logging.error(f"Restarting: {e}")
            time.sleep(15)
