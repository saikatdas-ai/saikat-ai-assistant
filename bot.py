import os
import telebot
import google.generativeai as genai
import feedparser
import time
from telebot import types

# --- 1. CONFIGURATION ---
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

if not BOT_TOKEN or not GEMINI_KEY:
    raise Exception("❌ KEYS MISSING!")

bot = telebot.TeleBot(BOT_TOKEN)
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# --- 2. INTELLIGENCE ENGINE (RSS) ---
def fetch_sports_intel():
    """
    Scans Google News for 3 specific High-Value Triggers:
    1. Tenders (Govt/Leagues)
    2. Sponsorships (Big Money)
    3. Appointments (Decision Makers)
    """
    # These are specific "CEO Level" search queries
    queries = [
        '"Sports Authority of India" tender',     # Trigger: Government Contracts
        '"BCCI" partner announced',               # Trigger: Big Money
        '"appointed" "marketing head" sports',    # Trigger: New Decision Maker
        '"official photographer" tender',         # Trigger: Direct Opportunity
        '"IPL" sponsorship 2026'                  # Trigger: League Money
    ]
    
    leads = []
    
    for query in queries:
        # Safe, Official Google News RSS Feed
        encoded_query = query.replace(' ', '%20').replace('"', '%22')
        url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"
        
        feed = feedparser.parse(url)
        
        # Get the top 1 freshest result for this trigger
        if feed.entries:
            entry = feed.entries[0]
            leads.append({
                "trigger": query.replace('"', '').upper(),
                "title": entry.title,
                "link": entry.link,
                "date": entry.published
            })
    
    return leads[:5] # Return top 5 distinct leads

# --- 3. COMMANDS ---

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, 
                 "🤖 *SAIKAT AI: INTELLIGENCE MODE*\n\n"
                 "📡 */leads_sports* → Scan for Tenders & Sponsors\n"
                 "💼 */pitch [Brand]* → Draft CEO-level outreach\n"
                 "🎤 *Voice Note* → Instant Strategy", 
                 parse_mode='Markdown')

@bot.message_handler(commands=['leads_sports', 'leads-sports'])
def scout_sports(message):
    wait_msg = bot.reply_to(message, "📡 *Scanning Indian Sports News Wires...* \n(Checking Tenders, BCCI, IPL, Appointments)")
    
    try:
        # 1. Get Real News
        intel_data = fetch_sports_intel()
        
        if not intel_data:
            bot.edit_message_text("❌ No significant news triggers found in the last 24h.", chat_id=message.chat.id, message_id=wait_msg.message_id)
            return

        # 2. Use Gemini to format it into "Battle Cards"
        prompt = f"""
        Analyze these raw news headlines for a professional sports photographer (Saikat).
        Convert them into a 'Business Opportunity Report'.
        
        RAW DATA: {intel_data}
        
        FORMAT GUIDELINES:
        - Headline: 🏆 [Event Name]
        - The Trigger: [One sentence summary]
        - Action: Suggest a specific pitch angle for Saikat (e.g., "Pitch for the tender", "Contact the new Marketing Head").
        - Don't make up names if not in the text, just say "Look for the Marketing Director".
        """
        
        response = model.generate_content(prompt)
        
        # 3. Send Report
        bot.delete_message(message.chat.id, wait_msg.message_id)
        bot.send_message(message.chat.id, f"📊 *DAILY SPORTS SCOUT REPORT*\n\n{response.text}", parse_mode='Markdown')
        
    except Exception as e:
        bot.reply_to(message, f"❌ Scout Error: {e}")

@bot.message_handler(commands=['pitch'])
def make_pitch(message):
    brand = message.text.replace('/pitch', '').strip()
    if not brand:
        bot.reply_to(message, "Usage: `/pitch [Brand Name]`")
        return
    response = model.generate_content(f"Write a high-stakes photography pitch for Saikat Das (15 yrs exp, BCCI/IPL) to {brand}.")
    bot.reply_to(message, response.text, parse_mode='Markdown')

@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    try:
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open("voice.ogg", 'wb') as f: f.write(downloaded_file)
        myfile = genai.upload_file("voice.ogg", mime_type="audio/ogg")
        response = model.generate_content(["Reply to this photographer's voice note:", myfile])
        bot.reply_to(message, response.text)
        os.remove("voice.ogg")
    except Exception as e: bot.reply_to(message, f"❌ Voice Error: {e}")

bot.infinity_polling()
