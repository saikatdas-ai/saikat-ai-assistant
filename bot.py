# bot.py -- SAIKAT OS Phase 2.25.2 (Hardened)
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

import feedparser
from urllib.parse import quote
from datetime import datetime, timedelta, timezone

import telebot

# ============================================================
# CONFIG
# ============================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_USER_ID")

if not BOT_TOKEN or not ADMIN_ID:
    logging.critical("Missing TELEGRAM_TOKEN or ADMIN_USER_ID")
    sys.exit(1)

ADMIN_ID = int(ADMIN_ID)
# Use default parse mode (none) to avoid entity parsing issues
bot = telebot.TeleBot(BOT_TOKEN)

DATA_DIR = os.getenv("DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)

SEEN_FILE = os.path.join(DATA_DIR, "seen_links.json")
QUEUE_FILE = os.path.join(DATA_DIR, "commercial_queue.json")
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.json")
META_FILE = os.path.join(DATA_DIR, "meta.json")

# ============================================================
# GLOBALS
# ============================================================
scan_lock = threading.Lock()   # prevents concurrent scans
DEEP_FETCH_PER_FEED = 8
SEEN_PURGE_DAYS = 30
QUEUE_RELEASED_PURGE_DAYS = 30

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

MANDATE_WORDS = [
    "mandate",
    "account move",
    "creative duties",
    "agency of record",
    "retains",
    "account win"
]

GENERIC_MONEY = [
    "partner",
    "partnership",
    "signs",
    "appoint",
    "appointed",
    "onboards",
    "deal",
    "strategic alliance"
]

# ============================================================
# STORAGE UTILITIES (with purge)
# ============================================================
def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"Failed to load JSON {path}: {e}")
        return default

def purge_seen(seen):
    cutoff = datetime.now(timezone.utc) - timedelta(days=SEEN_PURGE_DAYS)
    new = [s for s in seen if parse_iso(s.get("date")) >= cutoff]
    return new

def purge_queue(queue):
    cutoff = datetime.now(timezone.utc) - timedelta(days=QUEUE_RELEASED_PURGE_DAYS)
    new = {}
    for k, v in queue.items():
        if not v.get("released"):
            new[k] = v
            continue
        d = parse_iso(v.get("date"))
        if d and d >= cutoff:
            new[k] = v
    return new

def save_json_atomic_with_purge(seen_path, seen_data, queue_path, queue_data):
    # Purge seen older than SEEN_PURGE_DAYS
    try:
        seen_data = purge_seen(seen_data)
    except Exception:
        pass
    try:
        queue_data = purge_queue(queue_data)
    except Exception:
        pass

    # Atomic write both files separately
    try:
        tmp = seen_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(seen_data, f)
        os.replace(tmp, seen_path)
    except Exception as e:
        logging.error(f"Write error (seen): {e}")

    try:
        tmpq = queue_path + ".tmp"
        with open(tmpq, "w") as f:
            json.dump(queue_data, f)
        os.replace(tmpq, queue_path)
    except Exception as e:
        logging.error(f"Write error (queue): {e}")

def save_json_atomic(path, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception as e:
        logging.error(f"Write error: {e}")

def parse_iso(s):
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except Exception:
        return None

# ============================================================
# NETWORK / PARSING UTILITIES
# ============================================================
def http_fetch(url, timeout=10):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SAIKAT-OS/2.25.2"}
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        logging.debug(f"Fetch fail {url}: {e}")
    return ""

def extract_text(html):
    if not html:
        return ""
    html = re.sub(r"<(script|style).*?>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()

def normalize_title(title):
    return re.sub(r"\W+", "", title.lower())

def generate_linkedin_url(entity, role):
    if not entity:
        return None
    # Encode only the search query
    return f"https://www.linkedin.com/search/results/people/?keywords={quote(entity + ' ' + role)}"

def chunked_send(chat_id, text):
    if not text:
        return
    MAX = 3500
    # split on separators if present to keep whole records intact
    parts = text.split("\n" + "-" * 35 + "\n")
    current = ""
    for p in parts:
        block = p + "\n" + "-" * 35 + "\n"
        if len(current) + len(block) < MAX:
            current += block
        else:
            try:
                bot.send_message(chat_id, current)
            except Exception as e:
                logging.error(f"Send failed chunk: {e}")
            current = block
    if current:
        try:
            bot.send_message(chat_id, current)
        except Exception as e:
            logging.error(f"Final send failed: {e}")

# ============================================================
# COMPILED REGEX HELPERS (exact word matching)
# ============================================================
def compile_word_regex(list_of_terms):
    if not list_of_terms:
        return None
    # escape each term, then join as alternation; use word boundaries
    escaped = [re.escape(t) for t in list_of_terms if t and isinstance(t, str)]
    if not escaped:
        return None
    pattern = r"\b(?:" + "|".join(escaped) + r")\b"
    try:
        return re.compile(pattern, flags=re.IGNORECASE)
    except Exception:
        return None

# ============================================================
# CORE DISCOVERY (Hardened)
# ============================================================
def discover_commercial():
    # Acquire the lock inside caller; this function assumes safe single-run
    wl = load_json(WATCHLIST_FILE, {})
    seen = load_json(SEEN_FILE, [])
    queue = load_json(QUEUE_FILE, {})

    seen_links = {s.get("link") for s in seen if s.get("link")}

    # Build regexes
    athletes_re = compile_word_regex(wl.get("athletes", []))
    teams_re = compile_word_regex(wl.get("teams", []))
    agencies_re = compile_word_regex(wl.get("agencies", []))
    brands_re = compile_word_regex((wl.get("brands", []) or []) + (wl.get("conglomerates", []) or []))
    cities_re = compile_word_regex(wl.get("cities", []))
    commercial_re = compile_word_regex(wl.get("commercial_keywords", []) + GENERIC_MONEY)
    mandate_re = compile_word_regex(MANDATE_WORDS)

    # fallback plain lists for checks if regex is None
    def re_search(r, txt):
        if not r: return False
        return bool(r.search(txt))

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            logging.warning(f"Feed parse failed {feed_url}: {e}")
            continue

        deep_fetch_count = 0

        for entry in feed.entries[:25]:
            try:
                title = (entry.title or "").strip()
                link = (entry.link or "").strip()
                summary = getattr(entry, "summary", "") or ""
                if not link:
                    continue

                if link in seen_links:
                    continue

                stage1_text = (title + " " + summary).lower()

                # Stage1 lightweight checks (exact word boundaries)
                has_money = re_search(commercial_re, stage1_text)
                has_mandate = re_search(mandate_re, stage1_text)
                has_geo = re_search(cities_re, stage1_text) or any(gk in stage1_text for gk in ["india", "ipl", "isl", "bcci", "wpl"])

                matched_athlete = re_search(athletes_re, stage1_text)
                matched_team = re_search(teams_re, stage1_text)
                matched_brand = re_search(brands_re, stage1_text)
                matched_agency_stage1 = re_search(agencies_re, stage1_text)

                # Primary filter: require money-like or mandate or agency mention to proceed
                if not (has_money or has_mandate or matched_agency_stage1):
                    continue

                # Secondary gate: require geo or entity or mandate/agency
                if not (has_geo or matched_athlete or matched_team or matched_brand or matched_agency_stage1 or has_mandate):
                    continue

                # Recency gate using calendar.timegm for UTC correctness
                is_old = False
                pub_struct = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
                if pub_struct:
                    pub_ts = calendar.timegm(pub_struct)
                    pub_date = datetime.fromtimestamp(pub_ts, tz=timezone.utc)
                    if datetime.now(timezone.utc) - pub_date > timedelta(days=5):
                        is_old = True
                if is_old and not has_mandate:
                    continue

                # Deep fetch (limited)
                deep_text = ""
                if deep_fetch_count < DEEP_FETCH_PER_FEED:
                    deep_html = http_fetch(link)
                    deep_text = extract_text(deep_html).lower()
                    deep_fetch_count += 1

                full_text = stage1_text + " " + deep_text

                # Final exact matching
                detected_athletes = athletes_re.findall(full_text) if athletes_re else []
                detected_teams = teams_re.findall(full_text) if teams_re else []
                detected_brands = brands_re.findall(full_text) if brands_re else []
                detected_agencies = agencies_re.findall(full_text) if agencies_re else []
                detected_cities = cities_re.findall(full_text) if cities_re else []
                detected_exec = []  # could be extended from watchlist if provided

                has_mandate_full = re_search(mandate_re, full_text)

                # Title-normalized dedupe (hash on normalized title)
                sig = hashlib.md5(normalize_title(title).encode()).hexdigest()
                if sig in queue:
                    # already queued
                    continue

                # Scoring
                score = 50
                deal_type = "COMMERCIAL SIGNAL"
                if detected_brands:
                    score += 10
                if detected_teams:
                    score += 15
                    deal_type = "TEAM DEAL"
                if detected_athletes:
                    score += 15
                    deal_type = "ATHLETE ENDORSEMENT"
                if detected_agencies:
                    score += 20
                    deal_type = "AGENCY SIGNAL"
                if detected_exec:
                    score += 10
                if has_mandate_full:
                    score += 40
                    deal_type = "🚨 MANDATE FAST-TRACK"
                if (detected_brands or detected_athletes or detected_teams) and detected_agencies:
                    score = 100
                    deal_type = "🚨 HIGH PRIORITY CAMPAIGN"

                queue[sig] = {
                    "type": deal_type,
                    "title": title,
                    "link": link,
                    "brands": list(dict.fromkeys([b.strip() for b in detected_brands])) if detected_brands else [],
                    "teams": list(dict.fromkeys([t.strip() for t in detected_teams])) if detected_teams else [],
                    "athletes": list(dict.fromkeys([a.strip() for a in detected_athletes])) if detected_athletes else [],
                    "agencies": list(dict.fromkeys([ag.strip() for ag in detected_agencies])) if detected_agencies else [],
                    "execution": detected_exec,
                    "score": min(score, 100),
                    "released": False,
                    "date": datetime.now(timezone.utc).isoformat()
                }

                seen.append({"link": link, "date": datetime.now(timezone.utc).isoformat()})
                seen_links.add(link)

            except Exception as e:
                logging.debug(f"entry processing error: {e}")
                continue

    # Save with purge
    try:
        save_json_atomic_with_purge(SEEN_FILE, seen, QUEUE_FILE, queue)
    except Exception as e:
        logging.error(f"Save with purge failed: {e}")

# ============================================================
# REPORTING
# ============================================================
def build_report():
    queue = load_json(QUEUE_FILE, {})
    items = [v for v in queue.values() if not v.get("released")]
    if not items:
        return None

    items.sort(key=lambda x: x.get("score", 0), reverse=True)
    report = "📊 SAIKAT OS 2.25.2 - HARDENED\n" + "=" * 35 + "\n\n"

    for item in items:
        report += f"[{item['type']}]\n"
        report += f"SCORE: {item['score']}/100\n"
        report += f"TITLE: {item['title']}\n"
        if item.get("brands"):
            report += f"BRAND: {', '.join(item.get('brands'))}\n"
        if item.get("teams"):
            report += f"TEAM: {', '.join(item.get('teams'))}\n"
        if item.get("athletes"):
            report += f"ATHLETE: {', '.join(item.get('athletes'))}\n"
        if item.get("agencies"):
            report += f"AGENCY: {', '.join(item.get('agencies'))}\n"
        if item.get("execution"):
            report += f"EXECUTION: {', '.join(item.get('execution'))}\n"

        report += f"LINK: {item.get('link')}\n\n"

        primary_target = None
        if item.get("brands"):
            primary_target = item["brands"][0]
        elif item.get("teams"):
            primary_target = item["teams"][0]
        elif item.get("agencies"):
            primary_target = item["agencies"][0]

        if primary_target:
            report += "🎯 STRIKE LINKS:\n"
            brand_link = generate_linkedin_url(primary_target, "Marketing Head")
            if brand_link:
                report += f"- Brand Lead: {brand_link}\n"
            if item.get("agencies"):
                agency_link = generate_linkedin_url(item["agencies"][0], "Creative Director")
                if agency_link:
                    report += f"- Agency Lead: {agency_link}\n"

        report += "\n" + "-" * 35 + "\n\n"
        # mark released
        # we will update the queue file below after building
        item["released"] = True

    # Write queue back after marking released and purge old released items
    try:
        queue = {k: v for k, v in load_json(QUEUE_FILE, {}).items()}
        # update items marked
        for k, v in queue.items():
            if v.get("released"):
                # leave existing value (expensive but safe) - in-memory updated items already set "released"
                pass
        # We'll simply reload, update released flags from items list by matching title+link hashes
        for i in items:
            # compute sig same way
            s = hashlib.md5(re.sub(r"\W+", "", i.get("title", "").lower()).encode()).hexdigest()
            if s in queue:
                queue[s]["released"] = True
        # purge released older than threshold then save
        save_json_atomic_with_purge(SEEN_FILE, load_json(SEEN_FILE, []), QUEUE_FILE, queue)
    except Exception as e:
        logging.error(f"Failed to update queue after report: {e}")

    return report

# ============================================================
# TELEGRAM HANDLER (with lock)
# ============================================================
@bot.message_handler(commands=["leads-ad"])
def handle_ads(message):
    if message.from_user.id != ADMIN_ID:
        return

    # try to acquire lock: if already scanning, inform user
    acquired = scan_lock.acquire(blocking=False)
    if not acquired:
        try:
            bot.send_message(message.chat.id, "Scan already in progress. Please wait for it to finish.")
        except:
            pass
        return

    def task():
        try:
            try:
                bot.send_message(message.chat.id, "Starting Hardened Commercial Scan (2.25.2). This may take 10-60s.")
            except:
                pass
            discover_commercial()
            rep = build_report()
            if rep:
                chunked_send(message.chat.id, rep)
            else:
                try:
                    bot.send_message(message.chat.id, "No high-confidence commercial signals found in this run.")
                except:
                    pass
        finally:
            try:
                scan_lock.release()
            except RuntimeError:
                pass

    t = threading.Thread(target=task, daemon=True)
    t.start()

# ============================================================
# RUN LOOP
# ============================================================
if __name__ == "__main__":
    logging.info("SAIKAT OS 2.25.2 ONLINE - Hardened (fixes applied)")
    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=20)
        except Exception as e:
            logging.error(f"Polling error: {e}")
            time.sleep(10)
