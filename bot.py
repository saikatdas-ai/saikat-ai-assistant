import os
import requests
import telebot

# Load environment variables from Railway
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN not found in environment variables")

if not GEMINI_API_KEY:
    raise ValueError("❌ GEMINI_API_KEY not found in environment variables")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent?key=" + GEMINI_API_KEY
)


def ask_gemini(prompt: str) -> str:
    try:
        response = requests.post(
            GEMINI_URL,
            headers={"Content-Type": "application/json"},
            json={
                "contents": [
                    {
                        "parts": [{"text": prompt}]
                    }
                ]
            },
            timeout=30,
        )

        data = response.json()

        return data["candidates"][0]["content"]["parts"][0]["text"]

    except Exception as e:
        return "⚠️ Gemini connection error."


@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_text = message.text
    reply = ask_gemini(user_text)
    bot.reply_to(message, reply)


print("🤖 Bot is running...")
bot.infinity_polling()
