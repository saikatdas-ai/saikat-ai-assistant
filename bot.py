import os
import telebot
import google.generativeai as genai

# ===== ENV =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("Missing TELEGRAM_TOKEN")

if not GEMINI_API_KEY:
    raise ValueError("Missing GEMINI_API_KEY")

# ===== GEMINI CONFIG =====
genai.configure(api_key=GEMINI_API_KEY)

# --- Find a valid text model dynamically ---
available_models = [
    m.name for m in genai.list_models()
    if "generateContent" in m.supported_generation_methods
]

if not available_models:
    raise RuntimeError("No compatible Gemini models found for this API key.")

MODEL_NAME = available_models[0]  # pick first valid model
model = genai.GenerativeModel(MODEL_NAME)

print(f"Using Gemini model: {MODEL_NAME}")

# ===== TELEGRAM =====
bot = telebot.TeleBot(TELEGRAM_TOKEN)


def ask_gemini(prompt: str) -> str:
    try:
        response = model.generate_content(prompt)
        return response.text or "⚠️ Empty Gemini reply."
    except Exception as e:
        return "⚠️ Gemini error: " + str(e)


@bot.message_handler(func=lambda message: True)
def handle_message(message):
    reply = ask_gemini(message.text)
    bot.reply_to(message, reply)


print("🤖 Saikat AI Assistant is LIVE...")
bot.infinity_polling()
