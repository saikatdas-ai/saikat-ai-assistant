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
    """Finds the best available Flash model for your API key."""
    try:
        for m in genai.list_models():
            # Looks for gemini-2.0-flash or gemini-1.5-flash
            if 'generateContent' in m.supported_generation_methods:
                if 'flash' in m.name.lower():
                    print(f"✅ Found and using model: {m.name}")
                    return m.name
        return "models/gemini-1.5-flash" # Fallback
    except Exception as e:
        print(f"Error listing models: {e}")
        return "models/gemini-1.5-flash"

ACTIVE_MODEL_NAME = get_best_model()
model = genai.GenerativeModel(model_name=ACTIVE_MODEL_NAME)

# --- 3. COMMANDS ---
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = (
        f"🤖 *SAIKAT AI V2 (Active Mode)*\n"
        f"Using: `{ACTIVE_MODEL_NAME.split('/')[-1]}`\n\n"
        "🎤 *Voice Note* → AI Action\n"
        "📋 */leads* → Client List"
    )
    bot.reply_to(message, welcome_text, parse_mode='Markdown')

@bot.message_handler(commands=['leads'])
def send_leads(message):
    leads_text = "🎯 *DAILY LEADS*\n1. Puma India Brand Mgr\n2. UAE T20 Marketing"
    bot.reply_to(message, leads_text, parse_mode='Markdown')

# --- 4. VOICE HANDLER ---
@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    try:
        wait_msg = bot.reply_to(message, "👂 Thinking...")
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        file_path = "voice_note.ogg"
        with open(file_path, 'wb') as f:
            f.write(downloaded_file)
            
        # Standard Upload process
        myfile = genai.upload_file(file_path)
        response = model.generate_content(["Process this request from Saikat:", myfile])
        
        bot.reply_to(message, f"🤖 *AI Reply:*\n\n{response.text}", parse_mode='Markdown')
        os.remove(file_path)
        bot.delete_message(message.chat.id, wait_msg.message_id)
    except Exception as e:
        bot.reply_to(message, f"❌ Voice Error: {str(e)}")

# --- 5. TEXT HANDLER ---
@bot.message_handler(func=lambda message: True)
def echo_all(message):
    try:
        response = model.generate_content(message.text)
        bot.reply_to(message, response.text)
    except Exception as e:
        bot.reply_to(message, f"⚠️ Error: {str(e)}")

print("🚀 Bot checking models and starting...")
bot.infinity_polling()
