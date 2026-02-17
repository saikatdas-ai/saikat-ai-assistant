import os
import telebot
import google.generativeai as genai
from datetime import datetime

# ================= ENV =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN:
    raise Exception("❌ TELEGRAM_TOKEN missing")

if not GEMINI_API_KEY:
    raise Exception("❌ GEMINI_API_KEY missing")

# ================= GEMINI =================
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# ================= TELEGRAM =================
bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="Markdown")

# ================= DAILY DASHBOARD =================
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


# ================= COMMANDS =================
@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    bot.reply_to(
        message,
        "🤖 *AI Assistant Ready*\n\n"
        "/leads → Show today’s client dashboard\n"
        "/ask → Ask anything",
    )


@bot.message_handler(commands=["leads"])
def send_leads(message):
    dashboard = generate_daily_dashboard()
    bot.send_message(message.chat.id, dashboard)


# ================= AI CHAT =================
@bot.message_handler(func=lambda m: True)
def handle_message(message):
    user_text = message.text

    try:
        response = model.generate_content(user_text)
        reply = response.text if response.text else "⚠️ Empty AI response."
    except Exception as e:
        print("Gemini error:", e)
        reply = "⚠️ AI temporarily unavailable. Try again."

    bot.reply_to(message, reply)


# ================= SAFE START =================
print("✅ AI Assistant running (Stable Phase-1)...")

try:
    bot.infinity_polling(skip_pending=True)
except Exception as e:
    print("❌ Bot crashed:", e)
