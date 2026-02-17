import os
import telebot
import google.generativeai as genai
from telebot import types

# --- 1. CONFIGURATION ---
# Get Keys from Railway (Secure Cloud Keys)
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

# Crash Prevention
if not BOT_TOKEN or not GEMINI_KEY:
    raise Exception("❌ KEYS MISSING! Check Railway Variables.")

# Connect to Services
bot = telebot.TeleBot(BOT_TOKEN)
genai.configure(api_key=GEMINI_KEY)

# AI Brain Settings (Gemini 1.5 Flash)
generation_config = {
    "temperature": 0.7,
    "top_p": 0.95,
    "top_k": 64,
    "max_output_tokens": 8192,
}

model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    generation_config=generation_config,
)

# --- 2. COMMANDS ---

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = (
        "🤖 *SAIKAT AI ASSISTANT V2 (Voice Enabled)*\n\n"
        "I am your photography business co-pilot.\n"
        "🎤 *Send me a Voice Note* → I will listen and reply.\n"
        "📋 */leads* → Get today's client list.\n"
        "💬 *Chat* → Ask me anything about photography."
    )
    bot.reply_to(message, welcome_text, parse_mode='Markdown')

@bot.message_handler(commands=['leads'])
def send_leads(message):
    leads_text = (
        "🎯 *DAILY CLIENT SCOUT — PHASE 2*\n\n"
        "1️⃣ *Rahul Mehta* | Brand Manager – Puma India\n"
        "   👉 *Action:* Premium outreach (Campaign mode)\n\n"
        "2️⃣ *Sarah Khan* | Marketing Head – UAE T20\n"
        "   👉 *Action:* Professional intro\n\n"
        "⚡ _Real database connecting in Phase-3..._"
    )
    bot.reply_to(message, leads_text, parse_mode='Markdown')

# --- 3. NEW: VOICE NOTE HANDLER 🎙️ ---
@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    try:
        # Acknowledge receipt
        wait_msg = bot.reply_to(message, "👂 Listening to your voice note...")
        
        # 1. Download the voice file from Telegram
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # 2. Save it temporarily
        file_path = "voice_note.ogg"
        with open(file_path, 'wb') as new_file:
            new_file.write(downloaded_file)
            
        # 3. Send audio to Gemini AI
        myfile = genai.upload_file(file_path)
        
        # 4. Generate Answer
        response = model.generate_content([
            "You are Saikat's personal photography assistant. Listen to this audio instructions and reply helpfully and briefly.",
            myfile
        ])
        
        # 5. Reply to user
        bot.reply_to(message, f"🤖 *AI Transcribed & Replied:*\n\n{response.text}", parse_mode='Markdown')
        
        # 6. Cleanup (Delete the temp file)
        os.remove(file_path)
        bot.delete_message(message.chat.id, wait_msg.message_id)

    except Exception as e:
        bot.reply_to(message, f"❌ Voice Error: {e}")

# --- 4. TEXT CHAT HANDLER ---
@bot.message_handler(func=lambda message: True)
def echo_all(message):
    try:
        # Simple text chat
        response = model.generate_content(message.text)
        bot.reply_to(message, response.text)
    except Exception as e:
        bot.reply_to(message, "⚠️ AI Brain Hiccup. Try again.")

# --- 5. RUNNER (Keep Alive) ---
print("✅ Bot is running...")
bot.infinity_polling()
