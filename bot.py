import os
import sys
import json
import time
import logging
import threading
import requests
import re
import hashlib
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

if not BOT_TOKEN or not ADMIN_ID:
    logging.critical("Missing TELEGRAM_TOKEN or ADMIN_USER_ID")
    sys.exit(1)

ADMIN_ID = int(ADMIN_ID)

# Markdown removed for 100% Telegram stability
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
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except:
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
# NETWORK & PARSING (CONSERVATIVE DEEP FETCH)
# ============================================================

def http_fetch(url, timeout=8):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Phase2.20"}
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        logging.warning(f"Fetch failed for {url}: {e}")
    return ""

def extract_text(html):
    if not html:
        return ""
    html = re.sub(r"<script.*?>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style.*?>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()

def generate_sig(text):
    return hashlib.md5(text[:100].encode('utf-8')).hexdigest()

# ============================================================
# TRADE RSS SOURCES
# ============================================================

RSS_FEEDS = [
    "https://www.exchange4media.com/rss.xml",
    "https://www.afaqs.com/rss.xml",
    "https://www.campaignindia.in/rss",
    "https://economictimes.indiatimes.com/marketing/rssfeeds/13352306.cms",
    "https://www.mediainfoline.com/feed",
    "https://www.medianews4u.com/feed"
]

# ============================================================
# CORE ENGINE: TWO-STAGE DETECTION
# ============================================================

def extract_brand_guess(text, keywords):
    # Attempts to find capitalized words near commercial keywords
    for kw in keywords:
        match = re.search(r"([A-Z][A-Za-z0-9&]{2,20})\s+" + re.escape(kw), text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        match_after = re.search(re.escape(kw) + r"\s+([A-Z][A-Za-z0-9&]{2,20})", text, re.IGNORECASE)
        if match_after:
            return match_after.group(1).strip()
    return None

def discover_commercial():
    # Load dynamic watchlist
    wl = load_json(WATCHLIST_FILE, {})
    athletes = wl.get("athletes", [])
    teams = wl.get("teams", [])
    agencies = wl.get("agencies", [])
    cities = wl.get("cities", [])
    commercial_kws = wl.get("commercial_keywords", [])

    seen = load_json(SEEN_FILE, [])
    seen_links = {x["link"] for x in seen}
    queue = load_json(QUEUE_FILE, {})

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
        except:
            continue

        deep_fetch_count = 0 # Max 5 deep fetches per feed to protect server

        for entry in feed.entries[:30]:
            title = entry.title
            link = entry.link
            summary = getattr(entry, "summary", "")

            if link in seen_links:
                continue

            stage1_text = (title + " " + summary).lower()

            # STAGE 1: Quick Filter (Must contain a commercial keyword)
            if not any(kw.lower() in stage1_text for kw in commercial_kws):
                continue

            # STAGE 2: Conservative Deep Fetch (Only if Stage 1 passes)
            deep_text = ""
            if deep_fetch_count < 5:
                html = http_fetch(link)
                deep_text = extract_text(html).lower()
                deep_fetch_count += 1

            combined_text = stage1_text + " " + deep_text
            original_case_combined = title + " " + summary + " " + extract_text(html)

            deal_type = "COMMERCIAL DEAL"
            detected_athletes = [a for a in athletes if a.lower() in combined_text]
            detected_teams = [t for t in teams if t.lower() in combined_text]
            detected_agencies = [ag for ag in agencies if ag.lower() in combined_text]
            detected_cities = [c for c in cities if c.lower() in combined_text]

            brand_guess = extract_brand_guess(original_case_combined, commercial_kws)

            # SCORING & CLASSIFICATION
            score = 70
            
            if detected_teams:
                deal_type = "TEAM SPONSOR"
                score += 10
            
            if detected_agencies:
                deal_type = "AGENCY MANDATE"
                score += 15

            if detected_athletes:
                deal_type = "ATHLETE ENDORSEMENT"
                score += 15

            if detected_cities:
                score += 10 # City awareness boost

            # HIGH PRIORITY CAMPAIGN ALERT TRIGGER
            if detected_athletes and detected_agencies and any(kw.lower() in combined_text for kw in commercial_kws):
                deal_type = "🚨 HIGH PRIORITY CAMPAIGN ALERT"
                score = 100
            elif detected_teams and detected_agencies and brand_guess:
                deal_type = "🚨 HIGH PRIORITY CAMPAIGN ALERT"
                score = 100

            sig = generate_sig(title + link)
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
# DELIVERY & LINKEDIN DEEP LINK GENERATOR
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

    report = "📊 COMMERCIAL RADAR REPORT\n"
    report += "="*30 + "\n\n"

    for item in items:
        report += f"TYPE: {item.get('type')}\n"
        report += f"SCORE: {item.get('score')}/100\n"
        report += f"TITLE: {item.get('title')}\n"

        if item.get("brand_guess"):
            report += f"DETECTED BRAND: {item.get('brand_guess')}\n"
        if item.get("athletes"):
            report += f"ATHLETE(S): {', '.join(item.get('athletes'))}\n"
        if item.get("teams"):
            report += f"TEAM(S): {', '.join(item.get('teams'))}\n"
        if item.get("agencies"):
            report += f"AGENCY: {', '.join(item.get('agencies'))}\n"
        if item.get("cities"):
            report += f"CITY FLAG: {', '.join(item.get('cities'))}\n"

        report += f"\nSOURCE URL: {item.get('link')}\n\n"

        # ACTION BLOCK - LinkedIn Deep Links
        report += "🎯 ACTION STRIKE LINKS:\n"
        
        target_brand = item.get("brand_guess") or (item.get("teams")[0] if item.get("teams") else "Brand")
        report += f"- Brand Head: {generate_linkedin_url(target_brand, 'Marketing Head')}\n"
        
        if item.get("agencies"):
            for ag in item.get("agencies")[:2]: # Max 2 agencies to keep it clean
                report += f"- Agency Creative: {generate_linkedin_url(ag, 'Creative Director')}\n"
                report += f"- Agency Account: {generate_linkedin_url(ag, 'Account Director')}\n"
        elif item.get("athletes"):
            report += f"- Athlete Management: {generate_linkedin_url(item.get('athletes')[0], 'Manager')}\n"

        report += "\n" + "-"*30 + "\n\n"
        item["released"] = True

    save_json_atomic(QUEUE_FILE, queue)
    
    # Chunking to ensure Telegram delivery without errors
    return report

def send_chunked(chat_id, text):
    MAX = 3500
    parts = text.split("-" * 30)
    current_chunk = ""
    for p in parts:
        if not p.strip(): continue
        segment = p + "-" * 30 + "\n"
        if len(current_chunk) + len(segment) < MAX:
            current_chunk += segment
        else:
            bot.send_message(chat_id, current_chunk)
            current_chunk = segment
    if current_chunk:
        bot.send_message(chat_id, current_chunk)

# ============================================================
# COMMAND HANDLER
# ============================================================

@bot.message_handler(commands=["leads-ad"])
def handle_ads(message):
    if message.from_user.id != ADMIN_ID:
        return

    def task():
        bot.send_message(message.chat.id, "Scanning Commercial Ecosystem (RSS + Deep Fetch)...")
        discover_commercial()
        report = build_report()
        if report:
            send_chunked(message.chat.id, report)
        else:
            bot.send_message(message.chat.id, "No new commercial signals detected.")

    threading.Thread(target=task).start()

# ============================================================
# RUNNER
# ============================================================

if __name__ == "__main__":
    logging.info("SAIKAT OS 2.20 COMMERCIAL ENGINE ONLINE")
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=10)
        except Exception as e:
            logging.error(f"Polling error: {e}")
            time.sleep(10)
