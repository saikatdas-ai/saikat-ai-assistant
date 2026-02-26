import os, sys, json, time, logging, threading, requests, re, hashlib
import feedparser
from urllib.parse import quote
from datetime import datetime, timezone
import telebot

# ============================================================
# CORE CONFIG
# ============================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_USER_ID")
DEBUG_MODE = os.environ.get("DEBUG_MODE", "false").lower() == "true"

if not BOT_TOKEN or not ADMIN_ID:
    logging.critical("Missing TELEGRAM_TOKEN or ADMIN_USER_ID")
    sys.exit(1)

ADMIN_ID = int(ADMIN_ID)
bot = telebot.TeleBot(BOT_TOKEN)

DATA_DIR = os.getenv("DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)

SEEN_FILE = os.path.join(DATA_DIR, "seen_links.json")
QUEUE_FILE = os.path.join(DATA_DIR, "commercial_queue.json")
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.json")

# ============================================================
# STORAGE UTILITIES
# ============================================================

def load_json(path, default):
    if not os.path.exists(path):
        if DEBUG_MODE:
            logging.warning(f"{path} not found. Using fallback.")
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"JSON load error: {e}")
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
# NETWORK
# ============================================================

def http_fetch(url, timeout=10):
    try:
        headers = {"User-Agent": "Mozilla/5.0 SAIKAT-OS/2.21"}
        r = requests.get(url, headers=headers, timeout=timeout)
        return r.text if r.status_code == 200 else ""
    except Exception as e:
        if DEBUG_MODE:
            logging.warning(f"Fetch failed: {url}")
        return ""

def extract_text(html):
    if not html:
        return ""
    html = re.sub(r"<(script|style).*?>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()

# ============================================================
# RSS SOURCES
# ============================================================

RSS_FEEDS = [
    "https://www.exchange4media.com/rss.xml",
    "https://www.afaqs.com/rss.xml",
    "https://www.campaignindia.in/rss",
    "https://economictimes.indiatimes.com/marketing/rssfeeds/13352306.cms",
    "https://www.medianews4u.com/feed",
    "https://sportsmintmedia.com/feed/"
]

# ============================================================
# BRAND DETECTION (IMPROVED MULTI-WORD)
# ============================================================

def extract_brand_guess(text):
    pattern = r"\b([A-Z][a-zA-Z0-9&]+(?:\s+[A-Z][a-zA-Z0-9&]+){0,2})\b"
    matches = re.findall(pattern, text)
    return matches[0] if matches else None

# ============================================================
# DISCOVERY ENGINE 2.21
# ============================================================

def discover_commercial():
    wl = load_json(WATCHLIST_FILE, {
        "athletes": [],
        "teams": [],
        "agencies": [],
        "cities": [],
        "commercial_keywords": []
    })

    seen = load_json(SEEN_FILE, [])
    seen_links = {x["link"] for x in seen}
    queue = load_json(QUEUE_FILE, {})

    commercial_kws = wl.get("commercial_keywords", [])

    if DEBUG_MODE:
        logging.info(f"Loaded commercial keywords: {len(commercial_kws)}")

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            if DEBUG_MODE:
                logging.info(f"Feed {feed_url} entries: {len(feed.entries)}")
        except:
            continue

        deep_fetch_count = 0

        for entry in feed.entries[:30]:
            link = entry.link
            if link in seen_links:
                continue

            title = entry.title
            summary = getattr(entry, "summary", "")
            raw_content = (title + " " + summary).lower()

            # === SMART FALLBACK LOGIC ===
            weak_signal = any(kw.lower() in raw_content for kw in commercial_kws)

            if not weak_signal:
                # Soft fallback: detect generic deal words
                generic_triggers = ["partner", "deal", "appoint", "mandate", "collaborate", "signs"]
                if not any(g in raw_content for g in generic_triggers):
                    continue

            # === CONSERVATIVE DEEP FETCH ===
            html = ""
            deep_text = ""
            if deep_fetch_count < 5:
                html = http_fetch(link)
                deep_text = extract_text(html).lower()
                deep_fetch_count += 1

            full_content = raw_content + " " + deep_text
            original_case = title + " " + summary + " " + extract_text(html)

            detected_athletes = [a for a in wl["athletes"] if a.lower() in full_content]
            detected_teams = [t for t in wl["teams"] if t.lower() in full_content]
            detected_agencies = [ag for ag in wl["agencies"] if ag.lower() in full_content]
            detected_cities = [c for c in wl["cities"] if c.lower() in full_content]

            brand_guess = extract_brand_guess(original_case)

            score = 60
            deal_type = "COMMERCIAL SIGNAL"

            if detected_teams:
                score += 15
                deal_type = "TEAM SPONSOR"

            if detected_athletes:
                score += 15
                deal_type = "ATHLETE ENDORSEMENT"

            if detected_agencies:
                score += 20
                deal_type = "AGENCY MANDATE"

            if detected_cities:
                score += 10

            if (detected_teams or detected_athletes) and detected_agencies:
                deal_type = "🚨 HIGH PRIORITY CAMPAIGN ALERT"
                score = 100

            sig = hashlib.md5(link.encode()).hexdigest()
            if sig in queue:
                continue

            queue[sig] = {
                "type": deal_type,
                "title": title,
                "link": link,
                "athletes": detected_athletes,
                "teams": detected_teams,
                "agencies": detected_agencies,
                "cities": detected_cities,
                "brand_guess": brand_guess,
                "score": min(score, 100),
                "released": False,
                "date": datetime.now(timezone.utc).isoformat()
            }

            seen.append({"link": link, "date": datetime.now(timezone.utc).isoformat()})

    save_json_atomic(SEEN_FILE, seen)
    save_json_atomic(QUEUE_FILE, queue)

# ============================================================
# REPORT ENGINE
# ============================================================

def generate_linkedin_url(keyword, role):
    query = f"{keyword} {role}"
    return f"https://www.linkedin.com/search/results/people/?keywords={quote(query)}"

def build_report():
    queue = load_json(QUEUE_FILE, {})
    items = [v for v in queue.values() if not v.get("released")]

    if not items:
        return None

    items.sort(key=lambda x: x.get("score", 0), reverse=True)

    report = "REVENUE RADAR REPORT\n"
    report += "="*30 + "\n\n"

    for item in items:
        report += f"TYPE: {item['type']}\n"
        report += f"SCORE: {item['score']}/100\n"
        report += f"TITLE: {item['title']}\n"

        if item.get("brand_guess"):
            report += f"BRAND: {item['brand_guess']}\n"
        if item.get("teams"):
            report += f"TEAM: {', '.join(item['teams'])}\n"
        if item.get("athletes"):
            report += f"ATHLETE: {', '.join(item['athletes'])}\n"
        if item.get("agencies"):
            report += f"AGENCY: {', '.join(item['agencies'])}\n"

        report += f"SOURCE: {item['link']}\n"

        # Outreach Links
        seed = item.get("brand_guess") or (item.get("teams")[0] if item.get("teams") else "Marketing")
        report += f"LinkedIn Brand: {generate_linkedin_url(seed, 'Marketing Head')}\n"

        if item.get("agencies"):
            report += f"LinkedIn Creative: {generate_linkedin_url(item['agencies'][0], 'Creative Director')}\n"

        report += "\n" + "-"*30 + "\n\n"
        item["released"] = True

    save_json_atomic(QUEUE_FILE, queue)
    return report

# ============================================================
# TELEGRAM HANDLER
# ============================================================

@bot.message_handler(commands=["leads-ad"])
def handle_ads(message):
    if message.from_user.id != ADMIN_ID:
        return

    def task():
        bot.send_message(message.chat.id, "Scanning Commercial Ecosystem...")
        discover_commercial()
        rep = build_report()
        if rep:
            for chunk in rep.split("-" * 30):
                if chunk.strip():
                    bot.send_message(message.chat.id, chunk + "-"*30)
        else:
            bot.send_message(message.chat.id, "No commercial signals detected this cycle.")

    threading.Thread(target=task).start()

# ============================================================
# RUNNER
# ============================================================

if __name__ == "__main__":
    logging.info("SAIKAT OS 2.21 REVENUE RADAR ONLINE")
    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=20)
        except Exception as e:
            logging.error(f"Restarting polling: {e}")
            time.sleep(15)
