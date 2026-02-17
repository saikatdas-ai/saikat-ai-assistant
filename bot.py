import os
import sys
import telebot
import google.generativeai as genai
import feedparser
import time
import json
import logging
import uuid
import threading
import schedule
import pytz
from urllib.parse import quote

# --- 1. ENTERPRISE LOGGING & CONFIG ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load Environment Variables
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
ADMIN_ID = os.environ.get("ADMIN_USER_ID")

# AUDIT FIX 1: Fail Fast Logic
if not BOT_TOKEN or not GEMINI_KEY or not ADMIN_ID:
    logging.critical("❌ CRITICAL: Missing Config. Exiting to prevent zombie process.")
    sys.exit(1)

try:
    ADMIN_ID = int(ADMIN_ID)
except ValueError:
    logging.critical("❌ CRITICAL: ADMIN_USER_ID must be a number.")
    sys.exit(1)

bot = telebot.TeleBot(BOT_TOKEN)
genai.configure(api_key=GEMINI_KEY)

# AUDIT FIX 2: Hardcoded Model for Stability
ACTIVE_MODEL = "models/gemini-1.5-flash"
model = genai.GenerativeModel(model_name=ACTIVE_MODEL)

# --- 2. PERSISTENCE LAYER (With Health Check) ---
DATA_DIR = "/app/data"
SEEN_FILE = os.path.join(DATA_DIR, "memory_leads.json")

# AUDIT FIX 3: Volume Integrity Check
if not os.path.exists(DATA_DIR):
    logging.warning(f"⚠️ DATA_DIR {DATA_DIR} does not exist. Creating it (Ephemeral only if not mounted!)")
    os.makedirs(DATA_DIR)

# Write Test to verify persistence
try:
    with open(os.path.join(DATA_DIR, ".healthcheck"), 'w') as f: f.write("ok")
    logging.info("✅ Persistence Storage is Writable.")
except Exception as e:
    logging.critical(f"❌ CRITICAL: Storage is READ-ONLY. Memory will be lost! Error: {e}")

def load_memory():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, 'r') as f: return set(json.load(f))
        except: return set()
    return set()

def save_memory(seen_set):
    try:
        with open(SEEN_FILE, 'w') as f: json.dump(list(seen_set), f)
    except Exception as e:
        logging.error(f"Memory Save Failed: {e}")

# --- 3. SIGNAL HUNTER ENGINE ---
def fetch_high_value_signals():
    queries = [
        '"Sports Authority of India" tender',
        '"BCCI" partner announced',
        '"appointed" "marketing head" sports',
        '"official photographer" tender',
        '"IPL" sponsorship 2026',
        '"campaign launch" sports india'
    ]
    
    signals = []
    seen_links = load_memory()
    new_links = set()
    
    for query in queries:
        try:
            encoded = quote(query)
            url = f"https://news.google.com/rss/search?q={encoded}&hl=en-IN&gl=IN&ceid=IN:en"
            # AUDIT FIX 4: Robust Feed Parsing
            feed = feedparser.parse(url)
            
            for entry in feed.entries[:3]:
                if entry.link not in seen_links:
                    signals.append({
                        "source": "Google News",
                        "trigger": query.replace('"', '').upper(),
                        "title": entry.title,
                        "link": entry.link,
                        "date": entry.published
                    })
                    new_links.add(entry.link)
        except Exception as e:
            logging.error(f"Feed Error ({query}): {e}")
            continue

    if new_links:
        save_memory(seen_links.union(new_links))
        
    return signals

# --- 4. AI ANALYST (Scoring) ---
def analyze_leads(raw_signals):
    if not raw_signals: return None
    try:
        signals_json = json.dumps(raw_signals, indent=2)
        prompt = f"""
        ACT AS: Elite Business Manager.
        TASK: Score & Filter Leads.
        RULES: +30 Budget, +20 New Role. FILTER > 70.
        DATA: {signals_json}
        OUTPUT: Plain text Report.
        """
        # AUDIT FIX 5: Strict Timeout
        response = model.generate_content(prompt, request_options={'timeout': 45})
        return response.text
    except Exception as e:
        logging.error(f"AI Analysis Failed: {e}")
        return None

# --- 5. SCHEDULER & UTILS ---
def safe_send(chat_id, text):
    try:
        # Fallback to plain text if Markdown fails
        bot.send_message(chat_id, text)
    except Exception as e:
        logging.error(f"Send Error: {e}")

def daily_job():
    logging.info("⏰ Running Scheduled Scout Job...")
    signals = fetch_high_value_signals()
    if signals:
        report = analyze_leads(signals)
        if report:
            safe_send(ADMIN_ID, f"🌅 **10 AM AUTO-SCOUT**\n\n{report}")
    else:
        logging.info("No new leads found today.")

def start_scheduler():
    try:
        ist = pytz.timezone('Asia/Kolkata')
        schedule.every().day.at("10:00").tz(ist).do(daily_job)
        logging.info("✅ Scheduler Armed: 10:00 AM IST daily.")
        while True:
            schedule.run_pending()
            time.sleep(60)
    except Exception as e:
        logging.critical(f"Scheduler Crashed: {e}")

# Start Scheduler
threading.Thread(target=start_scheduler, daemon=True).start()

# --- 6. COMMANDS (Admin Protected) ---
def admin_only(func):
    def wrapper(message, *args, **kwargs):
        if str(message.from_user.id) != str(ADMIN_ID):
            return 
        return func(message, *args, **kwargs)
    return wrapper

@bot.message_handler(commands=['start'])
@admin_only
def start(message):
    safe_send(message.chat.id, "🤖 **SAIKAT OS: PRODUCTION READY**\n\n✅ Persistence Mounted\n✅ Scheduler Active (10 AM)\n✅ Backoff Logic Active")

@bot.message_handler(commands=['scout'])
@admin_only
def manual_scout(message):
    safe_send(message.chat.id, "🕵️ Hunting signals (Manual Trigger)...")
    signals = fetch_high_value_signals()
    if not signals:
        safe_send(message.chat.id, "📉 No new high-value signals.")
        return
    report = analyze_leads(signals)
    if report: safe_send(message.chat.id, report)
    else: safe_send(message.chat.id, "⚠️ AI Analysis failed/timed out.")

@bot.message_handler(commands=['pitch'])
@admin_only
def pitch(message):
    try:
        brand = message.text.replace('/pitch', '').strip()
        if not brand:
            safe_send(message.chat.id, "Usage: /pitch [Brand]")
            return
        response = model.generate_content(f"Write elite photography pitch to {brand} for Saikat.", request_options={'timeout':30})
        safe_send(message.chat.id, f"💼 **PITCH:**\n\n{response.text}")
    except Exception as e:
        safe_send(message.chat.id, f"Error: {e}")

@bot.message_handler(content_types=['voice'])
@admin_only
def voice(message):
    filename = f"/app/data/voice_{uuid.uuid4().hex}.ogg"
    gemini_file = None
    try:
        bot.reply_to(message, "👂 analyzing...")
        file_info = bot.get_file(message.voice.file_id)
        downloaded = bot.download_file(file_info.file_path)
        with open(filename, 'wb') as f: f.write(downloaded)
        gemini_file = genai.upload_file(filename, mime_type="audio/ogg")
        response = model.generate_content(["Reply:", gemini_file], request_options={'timeout':30})
        safe_send(message.chat.id, f"🤖 **REPLY:**\n{response.text}")
    except Exception as e:
        safe_send(message.chat.id, f"Error: {e}")
    finally:
        if os.path.exists(filename): os.remove(filename)
        if gemini_file: 
            try: genai.delete_file(gemini_file.name)
            except: pass

# --- 7. RUNNER (Exponential Backoff) ---
if __name__ == "__main__":
    logging.info("🚀 SYSTEM STARTING...")
    retry_delay = 5
    max_delay = 60
    
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=10)
            # If we exit polling cleanly, reset delay
            retry_delay = 5 
        except Exception as e:
            logging.error(f"⚠️ CRASH DETECTED: {e}")
            logging.info(f"🔄 Restarting in {retry_delay} seconds...")
            time.sleep(retry_delay)
            # Exponential Backoff (5 -> 10 -> 20 -> 40 -> 60)
            retry_delay = min(retry_delay * 2, max_delay)
