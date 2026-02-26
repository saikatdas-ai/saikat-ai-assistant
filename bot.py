import os
import sys
import json
import time
import logging
import threading
import difflib
import requests

import telebot
import feedparser

from urllib.parse import quote
from datetime import datetime, timedelta, date

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

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

# ============================================================
# STORAGE
# ============================================================

DATA_DIR = "/app/data"
SEEN_FILE = os.path.join(DATA_DIR, "seen_links.json")
QUEUE_FILE = os.path.join(DATA_DIR, "league_queue.json")

os.makedirs(DATA_DIR, exist_ok=True)


def load_json(path, default):
    if not os.path.exists(path):
        return default

    try:
        with open(path, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                logging.error(f"Corrupted JSON detected in {path}. Resetting file.")
                return default
    except Exception as e:
        logging.error(f"Read error for {path}: {e}")
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
# SAFETY UTILITIES
# ============================================================

def safe_send(chat_id, text):
    MAX = 3500
    chunks = [text[i:i+MAX] for i in range(0, len(text), MAX)]
    for chunk in chunks:
        try:
            bot.send_message(chat_id, chunk)
        except Exception as e:
            logging.error(f"Send failed: {e}")


def escape_md(text):
    return text.replace("[", "").replace("]", "").replace("(", "").replace(")", "")


# ============================================================
# LEAGUE DETECTION ENGINE
# ============================================================

FRANCHISE_KEYWORDS = [
    "league",
    "t20",
    "auction",
    "draft",
    "franchise",
    "season",
    "broadcast deal",
    "sponsorship deal",
]

CRICKET_PRIORITY = [
    "ipl",
    "t20",
    "cricket",
    "big bash",
    "psl",
    "cpl",
]

EXCLUDE = [
    "nfl",
    "nba",
    "university",
    "college",
    "youth",
    "school",
]


def is_valid_league(title):
    t = title.lower()
    if any(x in t for x in EXCLUDE):
        return False
    if any(k in t for k in FRANCHISE_KEYWORDS):
        return True
    return False


def calculate_score(title):
    score = 50
    t = title.lower()
    if any(k in t for k in CRICKET_PRIORITY):
        score += 30
    if "auction" in t or "draft" in t:
        score += 20
    return min(score, 100)


def signature(title):
    words = sorted([w for w in title.lower().split() if len(w) > 4])
    return "-".join(words[:5])


# ============================================================
# DISCOVERY ENGINE
# ============================================================

def fetch_rss(query, days):
    results = []
    past_date = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    url = f"https://news.google.com/rss/search?q={quote(query)}+after:{past_date}&hl=en-IN&gl=IN&ceid=IN:en"

    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:25]:
            results.append((entry.title, entry.link))
    except:
        pass

    return results


def discover(days):
    queries = [
        "franchise league india",
        "t20 league season",
        "league auction cricket",
        "state t20 league",
        "broadcast deal league",
        "motorsport championship india",
    ]

    seen = load_json(SEEN_FILE, [])
    seen_links = {x["link"] for x in seen}

    queue = load_json(QUEUE_FILE, {})

    scanned = 0

    for q in queries:
        entries = fetch_rss(q, days)
        for title, link in entries:
            scanned += 1
            if link in seen_links:
                continue
            if not is_valid_league(title):
                continue

            sig = signature(title)
            if sig in queue:
                continue

            score = calculate_score(title)

            queue[sig] = {
                "title": title,
                "link": link,
                "score": score,
                "released": False,
                "date": datetime.utcnow().isoformat()
            }

            seen.append({
                "link": link,
                "date": datetime.utcnow().isoformat()
            })

    save_json_atomic(SEEN_FILE, seen)
    save_json_atomic(QUEUE_FILE, queue)

    return scanned
    }

# ============================================================
# PHASE 2.14 - EXTENDED DISCOVERY LAYER
# ============================================================

def http_fetch(url, timeout=15):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (SaikatOS Radar 2.14)"
        }
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.text
    except:
        pass
    return ""


def google_index_scan(query):
    results = []
    search_url = f"https://www.google.com/search?q={quote(query)}&num=10"
    html = http_fetch(search_url)

    if not html:
        return results

    matches = re.findall(r'/url\?q=(https?://[^&]+)&', html)

    for link in matches:
        if "google" in link:
            continue
        results.append(link)

    return list(set(results))


def cricbuzz_fixture_scan():
    results = []
    url = "https://www.cricbuzz.com/cricket-schedule"
    html = http_fetch(url)

    if not html:
        return results

    matches = re.findall(r'href="(/cricket-match/[^"]+)"', html)

    for m in matches[:15]:
        full = "https://www.cricbuzz.com" + m
        results.append(("Cricbuzz Fixture", full))

    return results


def detect_revenue_angle(text):
    t = text.lower()

    if "sponsor" in t or "brand partner" in t:
        return "sponsorship"
    if "broadcast" in t or "streaming" in t:
        return "media_rights"
    if "ticket" in t:
        return "ticketing"
    if "auction" in t or "draft" in t:
        return "player_market"

    return "general"


def detect_decision_signal(text):
    roles = [
        "ceo",
        "director",
        "head",
        "commercial",
        "marketing",
        "sponsorship"
    ]

    found = []
    lower = text.lower()

    for r in roles:
        if r in lower:
            found.append(r)

    return found


def extended_discover(days):
    queue = load_json(QUEUE_FILE, {})
    seen = load_json(SEEN_FILE, [])
    seen_links = {x["link"] for x in seen}

    queries = [
        "new franchise league india",
        "league expansion cricket",
        "t20 league announcement",
        "sports broadcast deal india",
    ]

    for q in queries:
        links = google_index_scan(q)

        for link in links:
            if link in seen_links:
                continue

            html = http_fetch(link)
            if not html:
                continue

            if not is_valid_league(html):
                continue

            sig = signature(link)
            if sig in queue:
                continue

            score = 60
            if any(k in html.lower() for k in CRICKET_PRIORITY):
                score += 20

            revenue = detect_revenue_angle(html)
            roles = detect_decision_signal(html)

            queue[sig] = {
                "title": q,
                "link": link,
                "score": min(score, 100),
                "released": False,
                "date": datetime.utcnow().isoformat(),
                "revenue_angle": revenue,
                "decision_roles": roles
            }

            seen.append({
                "link": link,
                "date": datetime.utcnow().isoformat()
            })

    # Cricbuzz fixtures
    fixtures = cricbuzz_fixture_scan()
    for title, link in fixtures:
        if link in seen_links:
            continue

        sig = signature(link)
        if sig in queue:
            continue

        queue[sig] = {
            "title": title,
            "link": link,
            "score": 70,
            "released": False,
            "date": datetime.utcnow().isoformat(),
            "revenue_angle": "media_rights",
            "decision_roles": []
        }

        seen.append({
            "link": link,
            "date": datetime.utcnow().isoformat()
        })

    save_json_atomic(SEEN_FILE, seen)
    save_json_atomic(QUEUE_FILE, queue)

<?php
/* =========================================================
   SAIKAT OS - Phase 2.14 Intelligence Expansion Layer
   Extends Phase 2.13 without altering core architecture
   Railway-safe | No Unicode | No external dependencies
========================================================= */

if (!function_exists('http_safe_fetch')) {
    function http_safe_fetch($url) {
        $ch = curl_init();
        curl_setopt_array($ch, [
            CURLOPT_URL => $url,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_FOLLOWLOCATION => true,
            CURLOPT_CONNECTTIMEOUT => 10,
            CURLOPT_TIMEOUT => 20,
            CURLOPT_USERAGENT => 'Mozilla/5.0 (compatible; SaikatOS/2.14)',
            CURLOPT_SSL_VERIFYPEER => false,
            CURLOPT_SSL_VERIFYHOST => false
        ]);
        $response = curl_exec($ch);
        curl_close($ch);
        return $response ? $response : '';
    }
}

/* =========================================================
   GOOGLE INDEX SCAN (HTML scrape, no API dependency)
========================================================= */
function google_index_scan($query) {
    $url = "https://www.google.com/search?q=" . urlencode($query) . "&num=20";
    $html = http_safe_fetch($url);
    if (!$html) return [];

    preg_match_all('/<a href="\/url\?q=(.*?)&/i', $html, $matches);
    $results = [];

    foreach ($matches[1] as $link) {
        $clean = urldecode($link);
        if (strpos($clean, 'http') === 0) {
            $results[] = $clean;
        }
    }

    return array_unique($results);
}

/* =========================================================
   CRICBUZZ FIXTURE DETECTION
========================================================= */
function cricbuzz_schedule_scan() {
    $url = "https://www.cricbuzz.com/cricket-schedule";
    $html = http_safe_fetch($url);
    if (!$html) return [];

    preg_match_all('/<a[^>]+href="([^"]*cricket-match[^"]*)"[^>]*>(.*?)<\/a>/i', $html, $matches);

    $fixtures = [];
    foreach ($matches[1] as $k => $link) {
        $fixtures[] = [
            'url' => "https://www.cricbuzz.com" . $link,
            'title' => strip_tags($matches[2][$k])
        ];
    }

    return $fixtures;
}

/* =========================================================
   LINKEDIN INDEXED DETECTION (Google indexed only)
========================================================= */
function linkedin_index_scan($query) {
    $search = "site:linkedin.com/posts " . $query;
    return google_index_scan($search);
}

/* =========================================================
   DECISION MAKER EXTRACTION LAYER
========================================================= */
function extract_decision_makers($html) {
    $roles = [
        'CEO','Director','Head','Founder','Owner',
        'Marketing','Sponsorship','Commercial','Operations'
    ];

    $found = [];

    foreach ($roles as $role) {
        if (stripos($html, $role) !== false) {
            $found[] = $role;
        }
    }

    return array_unique($found);
}

/* =========================================================
   REVENUE ANGLE CLASSIFIER
========================================================= */
function revenue_angle_classify($text) {

    $map = [
        'sponsorship' => ['sponsor','brand partner','presented by'],
        'ticketing'   => ['tickets','box office','book now'],
        'media_rights'=> ['broadcast','live on','streaming'],
        'franchise'   => ['franchise','expansion team'],
        'grassroots'  => ['academy','development','grassroot']
    ];

    foreach ($map as $type => $keywords) {
        foreach ($keywords as $kw) {
            if (stripos($text, $kw) !== false) {
                return $type;
            }
        }
    }

    return 'general';
}

/* =========================================================
   MASTER EXTENDED DISCOVERY ENGINE
   Plugs into existing queue + signature system
========================================================= */
function extendedDiscoveryEngine($queries = []) {

    $output = [];

    foreach ($queries as $q) {

        // 1. Google Index
        $googleLinks = google_index_scan($q);

        foreach ($googleLinks as $link) {
            $html = http_safe_fetch($link);
            if (!$html) continue;

            $decisionRoles = extract_decision_makers($html);
            $revenueTag = revenue_angle_classify($html);

            $output[] = [
                'source' => 'google',
                'query'  => $q,
                'url'    => $link,
                'roles'  => $decisionRoles,
                'revenue_angle' => $revenueTag,
                'signature' => md5($link)
            ];
        }

        // 2. LinkedIn Indexed
        $linkedinLinks = linkedin_index_scan($q);
        foreach ($linkedinLinks as $l) {
            $output[] = [
                'source' => 'linkedin_index',
                'query'  => $q,
                'url'    => $l,
                'roles'  => [],
                'revenue_angle' => 'network',
                'signature' => md5($l)
            ];
        }
    }

    // 3. Cricbuzz Schedules
    $fixtures = cricbuzz_schedule_scan();
    foreach ($fixtures as $f) {
        $output[] = [
            'source' => 'cricbuzz',
            'query'  => 'fixture',
            'url'    => $f['url'],
            'title'  => $f['title'],
            'roles'  => [],
            'revenue_angle' => 'media_rights',
            'signature' => md5($f['url'])
        ];
    }

    return $output;
}

# ============================================================
# DELIVERY ENGINE
# ============================================================

def build_report(limit=20):
    queue = load_json(QUEUE_FILE, {})
    items = [v for v in queue.values() if not v["released"]]

    if not items:
        return None

    items.sort(key=lambda x: x["score"], reverse=True)

    selected = items[:limit]

    report = "Sports Radar Report\n\n"

    for item in selected:
        title = escape_md(item["title"])
        report += f"({item['score']}) [{title}]({item['link']})\n\n"

        item["released"] = True

    save_json_atomic(QUEUE_FILE, queue)

    return report


# ============================================================
# COMMAND HANDLERS
# ============================================================

def run_manual_sports(chat_id):
    safe_send(chat_id, "Sports Radar running. Checking 48h + 7d buffer. Estimated 15-30 seconds.")

    scanned = discover(7)

    report = build_report(limit=20)

    if not report:
        safe_send(chat_id, f"Scan complete. Scanned: {scanned}. No new franchise league announcements detected.")
        return

    safe_send(chat_id, report)


def run_bootstrap(chat_id):
    safe_send(chat_id, "Running 90-day archive scan. This may take 30-60 seconds.")

    scanned = discover(90)

    report = build_report(limit=9999)

    if not report:
        safe_send(chat_id, f"Archive scan complete. Scanned: {scanned}. No new historical leagues found.")
        return

    safe_send(chat_id, report)


@bot.message_handler(commands=["leads-sports"])
def manual_sports(message):
    if message.from_user.id != ADMIN_ID:
        return
    threading.Thread(target=run_manual_sports, args=(message.chat.id,)).start()


@bot.message_handler(commands=["bootstrap-archive"])
def bootstrap_archive(message):
    if message.from_user.id != ADMIN_ID:
        return
    threading.Thread(target=run_bootstrap, args=(message.chat.id,)).start()


# ============================================================
# RUNNER
# ============================================================

if __name__ == "__main__":
    delay = 5
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=10)
            delay = 5
        except Exception as e:
            logging.error(f"Polling crash: {e}")
            time.sleep(delay)
            delay = min(delay * 2, 300)
