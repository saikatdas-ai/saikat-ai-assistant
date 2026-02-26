import os, sys, json, time, logging, threading, requests, re, hashlib
import feedparser
from urllib.parse import quote, urlparse
from datetime import datetime, timedelta, timezone
import telebot

# ============================================================
# CORE CONFIG & STABILITY
# ============================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_USER_ID")

if not BOT_TOKEN or not ADMIN_ID:
    logging.critical("CRITICAL: Missing environment variables.")
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
# PHASE 2.23 HARDENED UTILITIES
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
        headers = {"User-Agent": "Mozilla/5.0 SAIKAT-OS/2.23 (Revenue Engine Stabilized)"}
        r = requests.get(url, headers=headers, timeout=timeout)
        return r.text if r.status_code == 200 else ""
    except: return ""

def extract_clean_text(html):
    if not html: return ""
    html = re.sub(r"<(script|style).*?>.*?</\1>", "", html, flags=re.DOTALL|re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()

# ============================================================
# TOKEN-PROXIMITY BRAND EXTRACTION (UPGRADED)
# ============================================================
def extract_brand_223(text, trigger_keywords):
    # Word-based tokenization for stability
    words = text.split()
    blacklist = ["IPL", "ISL", "BCCI", "WPL", "India", "The", "Monday", "Tuesday", "Wednesday", 
                 "Thursday", "Friday", "Saturday", "Sunday", "Media", "Group", "Limited", 
                 "Ltd", "Pvt", "Company", "Brand", "Marketing", "Creative"]
    
    triggers_lower = [tk.lower() for tk in trigger_keywords]
    
    for i, word in enumerate(words):
        if any(tk in word.lower() for tk in triggers_lower):
            # Scan window: 6 words before and 6 words after
            start = max(0, i - 6)
            end = min(len(words), i + 7)
            window = words[start:end]
            
            # Find Capitalized words (Potential Brands)
            potential = [w.strip(".,()\"") for w in window if w[0].isupper() and len(w) > 2]
            # Filter against blacklist
            filtered = [p for p in potential if p.upper() not in blacklist and not any(tk in p.lower() for tk in triggers_lower)]
            
            if filtered: return filtered[0]
    return None

# ============================================================
# PHASE 2.23 TRIANGULATED FILTER ENGINE
# ============================================================
RSS_FEEDS = [
    "https://www.exchange4media.com/rss.xml",
    "https://www.afaqs.com/rss.xml",
    "https://www.campaignindia.in/rss",
    "https://economictimes.indiatimes.com/marketing/rssfeeds/13352306.cms",
    "https://www.medianews4u.com/feed",
    "https://sportsmintmedia.com/feed/",
    "https://www.mediainfoline.com/feed"
]

def generate_linkedin_links(brand, agency=None):
    brand_url = f"🔗 Brand: https://www.linkedin.com/search/results/people/?keywords={quote(brand + ' Marketing Head')}\n"
    if agency:
        brand_url += f"🔗 Agency: https://www.linkedin.com/search/results/people/?keywords={quote(agency + ' Creative Director')}"
    return brand_url

def discover_commercial():
    wl = load_json(WATCHLIST_FILE, {})
    seen = load_json(SEEN_FILE, [])
    seen_links = {x["link"] for x in seen}
    queue = load_json(QUEUE_FILE, {})
    
    # Hardcoded Fallback Triggers for Robustness
    generic_money = ["partner", "partnership", "appoint", "mandate", "deal", "ties up", "signs", "onboard", "collaborate"]
    money_words = wl.get('commercial_keywords', []) + generic_money

    # False Positive Safety (Extended Geo)
    geo_keywords = wl.get('cities', []) + ["india", "ipl", "isl", "bcci", "wpl", "ranji", "indian premier league", "indian super league"]

    for feed_url in RSS_FEEDS:
        feed = feedparser.parse(feed_url)
        deep_fetch_count = 0 # PER FEED LIMIT

        for entry in feed.entries[:25]:
            if entry.link in seen_links: continue
            
            title = entry.title
            summary = getattr(entry, "summary", "")
            stage1_text = (title + " " + summary).lower()

            # --- TIERED GATE LOGIC ---
            has_money_word = any(mw.lower() in stage1_text for mw in money_words)
            has_geo = any(gk.lower() in stage1_text for gk in geo_keywords)
            matched_athlete = [a for a in wl.get('athletes', []) if a.lower() in stage1_text]
            matched_team = [t for t in wl.get('teams', []) if t.lower() in stage1_text]

            # REJECTION GATE
            if not has_money_word and "mandate" not in stage1_text: continue
            if not (has_geo or matched_athlete or matched_team):
                # Only bypass if specifically an agency mandate (High ROI signal)
                if "mandate" not in stage1_text: continue

            # Deep Fetch (Only if passes Stage 1)
            deep_text = ""
            if deep_fetch_count < 8:
                deep_text = extract_clean_text(http_fetch(entry.link)).lower()
                deep_fetch_count += 1

            full_text_lower = stage1_text + " " + deep_text
            
            # Re-check money word and mandate in full text (Blind Spot Fix)
            if not any(mw.lower() in full_text_lower for mw in money_words) and "mandate" not in full_text_lower:
                continue

            # Entity Resolution
            final_athletes = matched_athlete or [a for a in wl.get('athletes', []) if a.lower() in full_text_lower]
            final_teams = matched_team or [t for t in wl.get('teams', []) if t.lower() in full_text_lower]
            final_agencies = [ag for ag in wl.get('agencies', []) if ag.lower() in full_text_lower]
            
            brand = extract_brand_223(title + " " + deep_text, money_words)

            # --- SCORING ENGINE ---
            score = 40
            if has_geo: score += 10
            if final_athletes: score += 20
            if final_teams: score += 15
            if final_agencies: score += 20
            if "mandate" in full_text_lower: score += 25
            
            item_type = "COMMERCIAL DEAL"
            if (final_athletes or final_teams) and final_agencies:
                item_type = "🚨 HIGH PRIORITY: CAMPAIGN PITCH"
                score = 100
            elif "mandate" in full_text_lower:
                item_type = "MANDATE WIN (AGENCY)"

            sig = hashlib.md5(entry.link.encode()).hexdigest()
            if sig in queue: continue

            queue[sig] = {
                "type": item_type, "title": title, "link": entry.link,
                "brand": brand or "Detected Lead",
                "athletes": final_athletes, "teams": final_teams,
                "agencies": final_agencies, "score": min(score, 100),
                "released": False, "date": datetime.now(timezone.utc).isoformat()
            }
            seen.append({"link": entry.link, "date": datetime.now(timezone.utc).isoformat()})

    save_json_atomic(SEEN_FILE, seen)
    save_json_atomic(QUEUE_FILE, queue)

# ============================================================
# DELIVERY ENGINE
# ============================================================
def build_report():
    queue = load_json(QUEUE_FILE, {})
    items = [v for v in queue.values() if not v.get("released")]
    if not items: return None

    items.sort(key=lambda x: x.get("score", 0), reverse=True)
    report = "🚀 SAIKAT OS: REVENUE RADAR 2.23\n" + "="*30 + "\n\n"

    for item in items:
        report += f"[{item['type']}]\n"
        report += f"STRENGTH: {item['score']}/100\n"
        report += f"TITLE: {item['title']}\n"
        report += f"BRAND: {item['brand']}\n"
        if item['agencies']: report += f"AGENCY: {', '.join(item['agencies'])}\n"
        report += f"LINK: {item['link']}\n\n"
        
        report += "🎯 STRIKE LINKS (LinkedIn):\n"
        report += generate_linkedin_links(item['brand'], item['agencies'][0] if item['agencies'] else None)
        report += "\n" + "-"*30 + "\n\n"
        item["released"] = True

    save_json_atomic(QUEUE_FILE, queue)
    return report

# ============================================================
# COMMANDS & POLLING
# ============================================================
@bot.message_handler(commands=["leads-ad"])
def handle_ads(message):
    if message.from_user.id != ADMIN_ID: return
    def task():
        bot.send_message(message.chat.id, "Scanning Commercial Ecosystem (Hardened Filter Active)...")
        discover_commercial()
        rep = build_report()
        if rep:
            for chunk in rep.split("-" * 30):
                if chunk.strip(): bot.send_message(message.chat.id, chunk + "-" * 30)
        else:
            bot.send_message(message.chat.id, "No high-value commercial signals in this cycle.")
    threading.Thread(target=task).start()

if __name__ == "__main__":
    logging.info("SAIKAT OS 2.23 ONLINE - STABILIZED REVENUE ENGINE")
    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=20)
        except Exception as e:
            logging.error(f"Polling error: {e}")
            time.sleep(15)
