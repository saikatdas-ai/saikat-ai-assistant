import os
import telebot
import google.generativeai as genai
from datetime import datetime
import threading
import time

# ================= ENV =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
YOUR_CHAT_ID = os.getenv("YOUR_CHAT_ID")  # your personal Telegram chat ID

# ================= GEMINI =================
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# ================= TELEGRAM =================
bot = telebot.TeleBot(TELEGRAM_TOKEN)


# =========================================================
# CLIENT SCOUT PLACEHOLDERS (REAL APIS IN NEXT PHASE)
# =========================================================

def linkedin_leads():
    return [
        {"name": "Arjun Kapoor", "role": "Brand Manager – Adidas India", "why": "New athlete campaign", "action": "Premium outreach"},
        {"name": "Neha Sharma", "role": "Marketing Director – ILT20 UAE", "why": "League season prep", "action": "Professional intro"},
    ]


def instagram_leads():
    return [
        {"name": "Rohit Verma", "role": "Creative Producer – Sports Studio", "why": "Athlete BTS post", "action": "Warm intro"},
        {"name": "Sana Ali", "role": "Brand Executive – Puma India", "why": "Tagged in campaign", "action": "Premium short outreach"},
        {"name": "David Khan", "role": "League Media – T10 Global", "why": "Media prep started", "action": "Availability note"},
    ]


# =========================================================
# DASHBOARD GENERATION
# =========================================================

def generate_daily_dashboard():
    today = datetime.now().strftime("%d %b %Y")
    leads = linkedin_leads() + instagram_leads()

    dashboard = f"🎯 *DAILY CLIENT SCOUT — {today}*\n\n"

    for i, lead in enumerate(leads[:5], start=1):
        try:
            prompt = f"""
Write a short professional outreach message.

Name: {lead['name']}
Role: {lead['role']}
Reason: {lead['why']}

Photographer credentials:
• 15+ years IPL & BCCI
• International leagues
• SBI Life campaign with Pant & Jadeja

Tone: premium, human, confident.
Max 3 lines.
"""
            response = model.generate_content(prompt)
            msg = response.text.strip()
        except Exception:
            msg = "⚠️ Message generation error."

        dashboard += (
            f"{i}️⃣ *{lead['name']}*\n"
            f"Role: {lead['role']}\n"
            f"Why: {lead['why']}\n"
            f"Action: {lead['action']}\n\n"
            f"_Message ready:_\n{msg}\n\n────────────\n\n"
        )

    return dashboard


# =========================================================
# AUTO DAILY SCHEDULER (10:00 AM)
# =========================================================

def daily_scheduler():
    while True:
        now = datetime.now()

        if now.hour == 10 and now.minute == 0:
            try:
                dashboard = generate_daily_dashboard()
                bot.send_message(YOUR_CHAT_ID, dashboard, parse_mode="Markdown")
                time.sleep(60)  # avoid duplicate send
            except Exception:
                pass

        time.sleep(20)


# Run scheduler in background
threading.Thread(target=daily_scheduler, daemon=True).start()


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

@bot.message_handler(commands=["start", "help"])
def welcome(message):
    bot.reply_to(
        message,
        "🤖 *AI Business Assistant Active*\n\n"
        "/leads → Get latest client dashboard\n"
        "Auto-report → Every day at 10:00 AM",
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["leads"])
def manual_leads(message):
    dashboard = generate_daily_dashboard()
    bot.send_message(message.chat.id, dashboard, parse_mode="Markdown")


@bot.message_handler(func=lambda m: True)
def chat_ai(message):
    try:
        response = model.generate_content(message.text)
        reply = response.text
    except Exception:
        reply = "⚠️ Gemini connection error."

    bot.reply_to(message, reply)


print("✅ AI Assistant running with Hybrid Automation…")
bot.infinity_polling()
