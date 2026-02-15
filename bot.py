import os
import telebot
import requests
import json

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent?key=" + GEMINI_API_KEY
)


def ask_gemini(text):
    headers = {"Content-Type": "application/json"}
    data = {
        "contents": [{"parts": [{"text": text}]}]
    }

    response = requests.post(GEMINI_URL, headers=headers, data=json.dumps(data))
    result = response.json()

    try:
        return result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return "⚠️ Gemini connection error."


@bot.message_handler(func=lambda message: True)
def handle_message(message):
    reply = ask_gemini(message.text)
    bot.reply_to(message, reply)


print("AI Assistant is running...")
bot.infinity_polling()
