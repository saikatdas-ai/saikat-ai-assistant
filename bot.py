import os
import requests
import telebot

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN missing")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY missing")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"


def ask_gemini(prompt: str) -> str:
    try:
        response = requests.post(
            GEMINI_URL,
            headers={"Content-Type": "application/json"},
            json={
                "contents": [
                    {"parts": [{"text": prompt}]}
                ]
            },
            timeout=30,
        )

        data = response.json()

        # SAFE parsing (new Gemini response structure protection)
        if "candidates" in data:
            return data["candidates"][0]["content"]["parts"][0]["text"]

        # If Google returns error JSON
        if "error" in data:
            return "⚠️ Gemini API error: " + data["error"]["message"]

        return "⚠️ Unknown Gemini response."

    except Exception as e:
        return "⚠️ Gemini connection error."


@bot.message_handler(func=lambda message: True)
def handle_message(message):
    reply = ask_gemini(message.text)
    bot.reply_to(message, reply)


print("Bot running...")
bot.infinity_polling()
