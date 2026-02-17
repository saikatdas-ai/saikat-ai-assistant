import os
import telebot
import google.generativeai as genai
from telebot import types

# --- CONFIG ---
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

bot = telebot.TeleBot(BOT_TOKEN)
genai.configure(api_key=GEMINI_KEY)

# --- SMART BRAIN ---
model = genai.GenerativeModel('gemini-1.5-flash')

# --- BUSINESS LOGIC ---
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "🤖 *SAIKAT AI: PRO MODE*\n\n"
                          "🎤 *Voice Note* -> Instant Caption/Advice\n"
                          "💼 */pitch [Brand]* -> Pro Outreach Message\n"
                          "📋 */leads* -> Targeted Client List", parse_mode='Markdown')

@bot.message_handler(commands=['pitch'])
def make_pitch(message):
    brand = message.text.replace('/pitch', '').strip()
    if not brand:
        bot.reply_to(message, "Please provide a brand name. Example: `/pitch Adidas`", parse_mode='Markdown')
        return
    
    prompt = f"Write a professional, short WhatsApp pitch for Saikat Das, a sports photographer with 15 years experience (IPL, BCCI, ISL). He wants to work with {brand}. Make it elite and confident."
    response = model.generate_content(prompt)
    bot.reply_to(message, f"🔥 *PITCH FOR {brand.upper()}:*\n\n{response.text}", parse_mode='Markdown')

@bot.message_handler(commands=['leads'])
def send_leads(message):
    leads_text = ("🎯 *TODAY'S TARGETS*\n\n"
                  "1. *Star Sports India* (Marketing Team)\n"
                  "2. *Rajasthan Royals* (Content Lead)\n"
                  "3. *Thums Up* (Advertising Agency)")
    bot.reply_to(message, leads_text, parse_mode='Markdown')

@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    try:
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open("voice.ogg", 'wb') as f: f.write(downloaded_file)
        
        myfile = genai.upload_file("voice.ogg", mime_type="audio/ogg")
        response = model.generate_content(["You are Saikat's AI partner. Reply to this voice note:", myfile])
        bot.reply_to(message, f"🤖 *AI PARTNER:* \n\n{response.text}", parse_mode='Markdown')
        os.remove("voice.ogg")
    except Exception as e: bot.reply_to(message, f"❌ Voice Error: {e}")

@bot.message_handler(func=lambda message: True)
def chat(message):
    response = model.generate_content(f"User: {message.text}\nSaikat is a top sports/ad photographer. Reply as his assistant.")
    bot.reply_to(message, response.text)

bot.infinity_polling()
