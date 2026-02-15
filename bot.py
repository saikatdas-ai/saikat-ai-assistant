import os
import requests
import telebot

# ====== ENV VARIABLES FROM RAILWAY ======
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# ====== TELEGRAM BOT SETUP ======
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ====== GEMINI ENDPOINT ======
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash-latest:generateContent?key=" + GEMINI_API_KEY
)

# ====== FUNCTION TO TALK TO GEMINI ======
def ask_gemini(prompt):
    headers = {"Content-Type": "application/json"}

    data = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }

    try:
        response = requests.post(GEMINI_URL, headers=headers, json=data)
        result = response.json()

        if "candidates" in result:
            return result["candidates"][0]["content"]["parts"][0]["text"]
        else:
            return "⚠️ Gemini connection error."

    except Exception as e:
        return f"⚠️ Gemini error: {str(e)}"


# ====== TELEGRAM MESSAGE HANDLER ======
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_text = message.text

    reply = ask_gemini(user_text)

    bot.reply_to(message, reply)


# ====== START BOT ======
print("🤖 Saikat AI Assistant is running...")
bot.infinity_polling()
