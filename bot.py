import os
import telebot
import google.generativeai as genai
from datetime import datetime

# === ENV VARIABLES ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# === CONFIGURE GEMINI ===
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# === TELEGRAM BOT ===
bot = telebot.TeleBot(TELEGRAM_TOKEN)


# === DASHBOARD GENERATOR (TEMP DEMO DATA) ===
def generate_daily_dashboard():
    today = datetime.now().strftime("%d %b %Y")

    dashboard = f"""
🎯 *DAILY CLIENT SCOUT — {today}*

1️⃣ *Rahul Mehta*  
Role: Brand Manager – Puma India  
Why: New athlete campaign announced  
Action: Premium outreach  

_Message ready:_  
Hi Rahul, I noticed Puma’s recent athlete campaign direction.  
I’ve been covering IPL, BCCI & major sports campaigns for 15+ years.  
Would love to collaborate if any visual support is needed.

────────────

2️⃣ *Sarah Khan*  
Role: Marketing Head – UAE T20 League  
Why: Upcoming season preparation  
Action: Professional intro  

_Message ready:_  
Hello Sarah, sharing a quick introduction.  
I’m a sports photographer working across IPL, international cricket & commercial campaigns.  
Happy to support your upcoming season if visuals are required.

────────────

⚡ 3 more leads arriving in Phase-2
"""
    return dashboard


# === MESSAGE HANDLER ===
@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    bot.reply_to(
        message,
        "🤖 AI Assistant Ready.\n\nCommands:\n"
        "/leads → Show today’s client dashboard\n"
        "/ask → Ask anything",
    )


@bot.message_handler(commands=["leads"])
def send_leads(message):
    dashboard = generate_daily_dashboard()
    bot.send_message(message.chat.id, dashboard, parse_mode="Markdown")


@bot.message_handler(func=lambda m: True)
def handle_message(message):
    user_text = message.text

    try:
        response = model.generate_content(user_text)
        reply = response.text
    except Exception:
        reply = "⚠️ Gemini connection error."

    bot.reply_to(message, reply)


print("✅ AI Assistant running...")
bot.infinity_polling()
