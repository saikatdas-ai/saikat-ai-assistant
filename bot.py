import os
import telebot
import google.generativeai as genai

# ===== ENV VARIABLES =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("Missing TELEGRAM_TOKEN")

if not GEMINI_API_KEY:
    raise ValueError("Missing GEMINI_API_KEY")

# ===== CONFIGURE GEMINI =====
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash-latest")

# ===== TELEGRAM BOT =====
bot = telebot.TeleBot(TELEGRAM_TOKEN)


def ask_gemini(prompt: str) -> str:
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return "⚠️ Gemini error: " + str(e)


@bot.message_handler(func=lambda message: True)
def handle_message(message):
    reply = ask_gemini(message.text)
    bot.reply_to(message, reply)


print("🤖 Saikat AI Assistant is LIVE...")
bot.infinity_polling()
