import os
import telebot
import google.generativeai as genai
from telebot import types

# --- 1. CONFIGURATION ---
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

if not BOT_TOKEN or not GEMINI_KEY:
    raise Exception("❌ KEYS MISSING!")

bot = telebot.TeleBot(BOT_TOKEN)
genai.configure(api_key=GEMINI_KEY)

# --- 2. SMART MODEL DETECTION ---
def get_best_model():
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                if 'flash' in m.name.lower():
                    return m.name
        return "models/gemini-1.5-flash"
    except:
        return "models/gemini-1.5-flash"

ACTIVE_MODEL_NAME = get_best_model()
model = genai.GenerativeModel(model_name=ACTIVE_MODEL_NAME)

# --- 3. COMMANDS ---
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = (
        "🤖 *SAIKAT AI V2.1 (Voice Fix)*\n\n"
        "🎤 *Send me a Voice Note* now.\n"
        "I will transcribe and reply."
    )
    bot.reply_to(message, welcome_text, parse_mode='Markdown')

# --- 4. FIXED VOICE HANDLER ---
@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    try:
        wait_msg = bot.reply_to(message, "👂 Processing your voice...")
        
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        file_path = "voice_note.ogg"
        with open(file_path, 'wb') as f:
            f.write(downloaded_file)
            
        # FIX: Explicitly set the mime_type for Telegram voice notes
        myfile = genai.upload_file(file_path, mime_type="audio/ogg")
        
        response = model.generate_content([
            "You are Saikat's professional photography assistant. Listen to this and reply briefly.",
            myfile
        ])
        
        bot.reply_to(message, f"🤖 *AI Reply:*\n\n{response.text}", parse_mode='Markdown')
        os.remove(file_path)
        bot.delete_message(message.chat.id, wait_msg.message_id)
    except Exception as e:
        bot.reply_to(message, f"❌ Voice Error: {str(e)}")

@bot.message_handler(func=lambda message: True)
def echo_all(message):
    try:
        response = model.generate_content(message.text)
        bot.reply_to(message, response.text)
    except Exception as e:
        bot.reply_to(message, f"⚠️ Error: {str(e)}")

bot.infinity_polling()
