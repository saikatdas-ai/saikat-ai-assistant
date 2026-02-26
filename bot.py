# bot.py -- SAIKAT OS Phase 2.26
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
# CONFIG / INITIALIZATION
# ============================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_USER_ID")
DATA_DIR = os.getenv("DATA_DIR", "./data")
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"

if not BOT_TOKEN or not ADMIN_ID:
    logging.critical("Missing TELEGRAM_TOKEN or ADMIN_USER_ID")
    sys.exit(1)

ADMIN_ID = int(ADMIN_ID)
bot = telebot.TeleBot(BOT_TOKEN)

os.makedirs(DATA_DIR, exist_ok=True)

SEEN_PATH = os.path.join(DATA_DIR, "seen_links.json")
QUEUE_PATH = os.path.join(DATA_DIR, "commercial_queue.json")
WATCH_PATH = os.path.join(DATA_DIR, "watchlist.json")
META_PATH = os.path.join(DATA_DIR, "meta.json")

# ============================================================
# TUNABLES (safe defaults)
# ============================================================
MAX_SEEN = int(os.getenv("MAX_SEEN", "5000"))            # LRU cap for seen links
MAX_QUEUE = int(os.getenv("MAX_QUEUE", "1500"))          # LRU cap for queue items
DEEP_FETCH_PER_FEED = int(os.getenv("DEEP_FETCH_PER_FEED", "8"))
SEEN_PURGE_DAYS = int(os.getenv("SEEN_PURGE_DAYS", "30"))
QUEUE_RELEASED_PURGE_DAYS = int(os.getenv("QUEUE_RELEASED_PURGE_DAYS", "30"))
FEED_ITEM_LIMIT = int(os.getenv("FEED_ITEM_LIMIT", "25"))

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
    "mandate", "account move", "creative duties", "agency of record",
    "retains", "account win", "bags mandate"
]
GENERIC_MONEY = [
    "partner", "partnership", "signs", "appoint", "appointed",
    "onboards", "deal", "strategic alliance", "creative mandate"
]

# ============================================================
# THREAD LOCK (prevent concurrent scans)
# ============================================================
scan_lock = threading.Lock()

# ============================================================
# LRU in-memory structures
# - seen_lru: OrderedDict of link -> {"link":..., "date": ISO}
# - queue_lru: OrderedDict of sig -> item dict
# They are persisted on disk in atomic fashion.
# ============================================================
seen_lru = OrderedDict()
queue_lru = OrderedDict()

# ============================================================
# UTIL: JSON load/save atomic + purge helpers
# ============================================================
def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"load_json failed {path}: {e}")
        return default

def save_json_atomic(path, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception as e:
        logging.error(f"save_json_atomic failed {path}: {e}")

def purge_seen_list(list_of_seen):
    cutoff = datetime.now(timezone.utc) - timedelta(days=SEEN_PURGE_DAYS)
    retained = [s for s in list_of_seen if parse_iso(s.get("date")) and parse_iso(s.get("date")) >= cutoff]
    return retained

def purge_queue_dict(qdict):
    cutoff = datetime.now(timezone.utc) - timedelta(days=QUEUE_RELEASED_PURGE_DAYS)
    new_q = {}
    for k, v in qdict.items():
        if not v.get("released"):
            new_q[k] = v
            continue
        d = parse_iso(v.get("date"))
        if d and d >= cutoff:
            new_q[k] = v
    return new_q

# ============================================================
# TIME / PARSE HELPERS
# ============================================================
def parse_iso(s):
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except Exception:
        return None

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

# ============================================================
# STARTUP: load persisted files into LRU structures
# ============================================================
def load_state():
    global seen_lru, queue_lru
    # seen -> list of {link, date}
    seen_list = load_json(SEEN_PATH, [])
    # keep last MAX_SEEN items (they already sorted by append time)
    if isinstance(seen_list, list):
        tail = seen_list[-MAX_SEEN:]
        seen_lru = OrderedDict((s["link"], s) for s in tail if s.get("link"))
    else:
        seen_lru = OrderedDict()
    # queue -> dict sig->item
    queue_dict = load_json(QUEUE_PATH, {})
    if isinstance(queue_dict, dict):
        # keep most recent MAX_QUEUE by date (sort by date)
        items = list(queue_dict.items())
        # items are (sig, obj)
        items_sorted = sorted(items, key=lambda kv: kv[1].get("date", ""), reverse=False)
        tail = items_sorted[-MAX_QUEUE:]
        queue_lru = OrderedDict(tail)
    else:
        queue_lru = OrderedDict()

    # meta defaults
    meta = load_json(META_PATH, {})
    return meta

meta = load_state()

# ============================================================
# NETWORK + PARSING helpers (conservative)
# ============================================================
def http_fetch(url, timeout=8):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SAIKAT-OS/2.26"}
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        logging.debug(f"http_fetch fail {url}: {e}")
    return ""

def extract_text(html):
    if not html:
        return ""
    html = re.sub(r"<(script|style).*?>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()

def normalize_title(title):
    return re.sub(r"\W+", "", (title or "").lower())

def sig_from_title(title):
    return hashlib.md5(normalize_title(title).encode()).hexdigest()

# ============================================================
# Regex builder with word-boundary safe matching
# ============================================================
def compile_word_regex(items):
    items = [i for i in (items or []) if isinstance(i, str) and i.strip()]
    if not items:
        return None
    escaped = [re.escape(i) for i in items]
    pattern = r"\b(?:" + "|".join(escaped) + r")\b"
    try:
        return re.compile(pattern, flags=re.IGNORECASE)
    except Exception as e:
        logging.debug(f"compile_word_regex failed: {e}")
        return None

# ============================================================
# TELEGRAM safe send (chunked)
# ============================================================
def safe_send(chat_id, text):
    if not text:
        return
    MAX = 3500
    parts = text.split("\n" + "-"*35 + "\n")
    current = ""
    for p in parts:
        block = p + "\n" + "-"*35 + "\n"
        if len(current) + len(block) < MAX:
            current += block
        else:
            try:
                bot.send_message(chat_id, current)
            except Exception as e:
                logging.error(f"safe_send failed chunk: {e}")
            current = block
    if current:
        try:
            bot.send_message(chat_id, current)
        except Exception as e:
            logging.error(f"safe_send final failed: {e}")

# ============================================================
# Persist in-memory LRU -> disk (atomic with purge)
# - Convert seen_lru OrderedDict to list in chronological order
# - Convert queue_lru to dict
# ============================================================
def persist_state():
    try:
        seen_list = list(seen_lru.values())
        # purge old seen entries to keep disk small
        seen_list = purge_seen_list(seen_list)
        # queue dict
        queue_dict = dict(queue_lru)
        queue_dict = purge_queue_dict(queue_dict)
        # atomic writes
        save_json_atomic(SEEN_PATH, seen_list)
        save_json_atomic(QUEUE_PATH, queue_dict)
    except Exception as e:
        logging.error(f"persist_state failed: {e}")

# ============================================================
# Feed debug telemetry collection
# ============================================================
def update_meta_feed_stats(feed_url, reason):
    if not DEBUG_MODE:
        return
    global meta
    fs = meta.get("feed_stats", {})
    f = fs.get(feed_url, {"total": 0, "accepted": 0, "rejected": {}, "final": 0})
    f["total"] = f.get("total", 0) + 1
    if reason == "accepted":
        f["accepted"] = f.get("accepted", 0) + 1
    else:
        rej = f["rejected"]
        rej[reason] = rej.get(reason, 0) + 1
        f["rejected"] = rej
    fs[feed_url] = f
    meta["feed_stats"] = fs
    meta["last_run"] = utc_now_iso()
    save_json_atomic(META_PATH, meta)

# ============================================================
# Core Discovery: discover_commercial (Phase 2.26)
# - Uses in-memory watchlist (watchlist.json)
# - Exact word regex checks
# - Stage1 lightweight checks and recency gate with calendar.timegm
# - Deep fetch limit per feed
# - Title-normalized dedupe -> sig
# - Adds to queue_lru and seen_lru (LRU semantics)
# ============================================================
def discover_commercial():
    global seen_lru, queue_lru, meta

    # Load watchlist (dynamic)
    wl = load_json(WATCH_PATH, {})
    # build regexes (word-boundary)
    athletes_re = compile_word_regex(wl.get("athletes", []))
    teams_re = compile_word_regex(wl.get("teams", []))
    agencies_re = compile_word_regex(wl.get("agencies", []))
    brands_re = compile_word_regex((wl.get("brands", []) or []) + (wl.get("conglomerates", []) or []))
    cities_re = compile_word_regex(wl.get("cities", []))
    commercial_re = compile_word_regex((wl.get("commercial_keywords", []) or []) + GENERIC_MONEY)
    mandate_re = compile_word_regex(MANDATE_WORDS)

    def re_search(r, txt):
        return bool(r.search(txt)) if r else False

    # iterate feeds
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            logging.warning(f"feedparser failed {feed_url}: {e}")
            continue

        deep_fetch_count = 0
        # For debug: reset feed counter
        if DEBUG_MODE:
            meta.setdefault("feed_stats", {}).setdefault(feed_url, {"total": 0, "accepted": 0, "rejected": {}, "final": 0})

        entries = getattr(feed, "entries", [])[:FEED_ITEM_LIMIT]
        for entry in entries:
            # very defensive extraction
            title = (getattr(entry, "title", "") or "").strip()
            link = (getattr(entry, "link", "") or "").strip()
            summary = (getattr(entry, "summary", "") or "").strip()

            if not link:
                update_meta_feed_stats(feed_url, "no_link")
                continue

            # recency gate (use published_parsed or updated_parsed)
            pub_struct = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
            if pub_struct:
                try:
                    pub_ts = calendar.timegm(pub_struct)  # UTC-safe
                    pub_dt = datetime.fromtimestamp(pub_ts, tz=timezone.utc)
                    if datetime.now(timezone.utc) - pub_dt > timedelta(days=5):
                        update_meta_feed_stats(feed_url, "old")
                        continue
                except Exception:
                    pass

            # Stage1 lightweight combined text
            stage1_text = (title + " " + summary).lower()

            # Quick checks
            has_money = re_search(commercial_re, stage1_text)
            has_mandate = re_search(mandate_re, stage1_text)
            has_geo = re_search(cities_re, stage1_text) or any(gk in stage1_text for gk in ["india", "ipl", "isl", "bcci", "wpl"])
            matched_athlete = re_search(athletes_re, stage1_text)
            matched_team = re_search(teams_re, stage1_text)
            matched_brand = re_search(brands_re, stage1_text)
            matched_agency_stage1 = re_search(agencies_re, stage1_text)

            # allow lightweight agency mention to pass to deep fetch (fix requested logic)
            if not (has_money or has_mandate or matched_agency_stage1):
                update_meta_feed_stats(feed_url, "money_miss")
                continue

            if not (has_geo or matched_athlete or matched_team or matched_brand or matched_agency_stage1 or has_mandate):
                update_meta_feed_stats(feed_url, "entity_miss")
                continue

            # de-dup by link earlier: if seen link skip (fast)
            if link in seen_lru:
                update_meta_feed_stats(feed_url, "seen_link")
                continue

            # deep fetch if needed (limit)
            deep_text = ""
            if deep_fetch_count < DEEP_FETCH_PER_FEED:
                deep_html = http_fetch(link)
                deep_text = extract_text(deep_html).lower()
                deep_fetch_count += 1

            full_text = stage1_text + " " + deep_text

            # final detection using regexes on full_text
            detected_athletes = athletes_re.findall(full_text) if athletes_re else []
            detected_teams = teams_re.findall(full_text) if teams_re else []
            detected_brands = brands_re.findall(full_text) if brands_re else []
            detected_agencies = agencies_re.findall(full_text) if agencies_re else []
            detected_cities = cities_re.findall(full_text) if cities_re else []

            has_mandate_full = re_search(mandate_re, full_text)
            has_money_full = re_search(commercial_re, full_text)

            # If after deep fetch no money signal and no mandate, drop it
            if not (has_money_full or has_mandate_full):
                update_meta_feed_stats(feed_url, "deep_money_miss")
                continue

            # Title-normalized dedupe sig
            sig = sig_from_title(title)
            if sig in queue_lru:
                update_meta_feed_stats(feed_url, "sig_exists")
                # still mark link as seen to avoid repeats across feeds
                seen_lru[link] = {"link": link, "date": utc_now_iso()}
                # move to end (LRU behavior)
                seen_lru.move_to_end(link)
                if len(seen_lru) > MAX_SEEN:
                    seen_lru.popitem(last=False)
                continue

            # scoring
            score = 50
            item_type = "COMMERCIAL SIGNAL"
            if detected_brands:
                score += 10
            if detected_teams:
                score += 15; item_type = "TEAM DEAL"
            if detected_athletes:
                score += 15; item_type = "ATHLETE ENDORSEMENT"
            if detected_agencies:
                score += 20; item_type = "AGENCY SIGNAL"
            if has_mandate_full:
                score += 40; item_type = "MANDATE FAST-TRACK"
            if (detected_brands or detected_athletes or detected_teams) and detected_agencies:
                score = 100; item_type = "HIGH PRIORITY CAMPAIGN"

            item = {
                "type": item_type,
                "title": title,
                "link": link,
                "brands": list(dict.fromkeys(detected_brands)) if detected_brands else [],
                "teams": list(dict.fromkeys(detected_teams)) if detected_teams else [],
                "athletes": list(dict.fromkeys(detected_athletes)) if detected_athletes else [],
                "agencies": list(dict.fromkeys(detected_agencies)) if detected_agencies else [],
                "cities": list(dict.fromkeys(detected_cities)) if detected_cities else [],
                "score": min(score, 100),
                "released": False,
                "date": utc_now_iso()
            }

            # insert into queue LRU
            queue_lru[sig] = item
            queue_lru.move_to_end(sig)
            if len(queue_lru) > MAX_QUEUE:
                queue_lru.popitem(last=False)

            # mark seen link LRU
            seen_lru[link] = {"link": link, "date": utc_now_iso()}
            seen_lru.move_to_end(link)
            if len(seen_lru) > MAX_SEEN:
                seen_lru.popitem(last=False)

            update_meta_feed_stats(feed_url, "accepted")
            # track final count
            fs = meta.get("feed_stats", {})
            fs.setdefault(feed_url, {}).setdefault("final", 0)
            fs[feed_url]["final"] = fs[feed_url].get("final", 0) + 1
            meta["feed_stats"] = fs

    # persist state after run
    persist_state()
    if DEBUG_MODE:
        meta["last_run"] = utc_now_iso()
        save_json_atomic(META_PATH, meta)

# ============================================================
# Reporting build and delivery
# ============================================================
def build_report():
    # work on snapshot of queue to avoid concurrency issues
    q_snapshot = list(queue_lru.items())
    items = [v for k, v in q_snapshot if not v.get("released")]
    if not items:
        return None

    items.sort(key=lambda x: x.get("score", 0), reverse=True)
    report_lines = []
    header = "SAIKAT OS REVENUE RADAR 2.26\n" + "="*35 + "\n"
    report_lines.append(header)
    for it in items:
        lines = []
        lines.append(f"TYPE: {it.get('type')}")
        lines.append(f"SCORE: {it.get('score')}/100")
        lines.append(f"TITLE: {it.get('title')}")
        if it.get("brands"):
            lines.append("BRAND: " + ", ".join(it.get("brands")))
        if it.get("teams"):
            lines.append("TEAM: " + ", ".join(it.get("teams")))
        if it.get("athletes"):
            lines.append("ATHLETE: " + ", ".join(it.get("athletes")))
        if it.get("agencies"):
            lines.append("AGENCY: " + ", ".join(it.get("agencies")))
        lines.append("LINK: " + it.get("link"))
        # Strike links
        primary = None
        if it.get("brands"):
            primary = it["brands"][0]
        elif it.get("teams"):
            primary = it["teams"][0]
        elif it.get("agencies"):
            primary = it["agencies"][0]
        if primary:
            lines.append("STRIKE LINKS:")
            lines.append("- Brand Lead: " + generate_linkedin_url_safe(primary, "Marketing Head"))
            if it.get("agencies"):
                lines.append("- Agency Lead: " + generate_linkedin_url_safe(it["agencies"][0], "Creative Director"))
        block = "\n".join(lines)
        report_lines.append(block)
        report_lines.append("-"*35)
        # mark released in the live queue_lru after collecting report
        # we'll mark after building to avoid interfering with iteration
    # mark released now
    for sig, v in list(queue_lru.items()):
        if not v.get("released"):
            v["released"] = True
            queue_lru[sig] = v
    persist_state()
    return "\n".join(report_lines)

def generate_linkedin_url_safe(entity, role):
    # avoid doubling role words, ensure entity is not empty
    if not entity:
        return ""
    entity = re.sub(r"\s+", " ", entity).strip()
    q = quote(entity + " " + role)
    return f"https://www.linkedin.com/search/results/people/?keywords={q}"

# ============================================================
# Telegram handler with lock + optional debug summary
# ============================================================
@bot.message_handler(commands=["leads-ad"])
def handle_leads_ad(message):
    if message.from_user.id != ADMIN_ID:
        return
    # try acquire lock
    if not scan_lock.acquire(blocking=False):
        try:
            bot.send_message(message.chat.id, "A scan is already running. Wait until it finishes.")
        except:
            pass
        return

    def job():
        try:
            try:
                bot.send_message(message.chat.id, "Starting Revenue Scan (Phase 2.26). This may take 10-60s.")
            except:
                pass
            # reset feed stats if debug mode
            if DEBUG_MODE:
                meta["feed_stats"] = {}
                save_json_atomic(META_PATH, meta)
            discover_commercial()
            report = build_report()
            if report:
                safe_send(message.chat.id, report)
            else:
                try:
                    bot.send_message(message.chat.id, "No high-confidence commercial signals detected in this run.")
                except:
                    pass
            # If debug mode send telemetry summary
            if DEBUG_MODE:
                debug_summary = build_debug_summary()
                try:
                    bot.send_message(message.chat.id, "DEBUG FEED SUMMARY:\n" + debug_summary)
                except:
                    pass
        finally:
            try:
                scan_lock.release()
            except RuntimeError:
                pass

    t = threading.Thread(target=job, daemon=True)
    t.start()

def build_debug_summary():
    if not DEBUG_MODE:
        return ""
    m = load_json(META_PATH, {})
    fs = m.get("feed_stats", {})
    lines = []
    for feed, data in fs.items():
        lines.append(f"Feed: {feed}")
        lines.append(f"  Total items inspected: {data.get('total', 0)}")
        lines.append(f"  Accepted (to queue): {data.get('final', 0)}")
        rejected = data.get("rejected", {})
        if rejected:
            for reason, count in rejected.items():
                lines.append(f"  Rejected - {reason}: {count}")
        lines.append("")
    return "\n".join(lines)

# ============================================================
# Bootstrap: save initial (persist current in-memory state)
# ============================================================
def bootstrap_persist():
    persist_state()
    save_json_atomic(META_PATH, meta)

# ============================================================
# RUN LOOP
# ============================================================
if __name__ == "__main__":
    logging.info("SAIKAT OS 2.26 ONLINE (Performance + Debug). DEBUG_MODE=%s", DEBUG_MODE)
    bootstrap_persist()
    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=20)
        except Exception as e:
            logging.error(f"Polling error: {e}")
            time.sleep(10)
