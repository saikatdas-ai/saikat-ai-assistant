import os
import telebot
import google.generativeai as genai
from datetime import datetime

# ================= ENV =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# ================= GEMINI =================
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# ================= TELEGRAM =================
bot = telebot.TeleBot(TELEGRAM_TOKEN)


# =========================================================
# PHASE-2 CLIENT SCOUT ENGINE (STRUCTURE READY FOR REAL DATA)
# =========================================================

def linkedin_leads():
    """
    Placeholder for future real LinkedIn API search.
    Phase-3 will connect real discovery.
    """
    return [
        {
            "name": "Arjun Kapoor",
            "role": "Brand Manager – Adidas India",
            "why": "Recent athlete campaign announcement",
            "action": "Premium outreach",
        },
        {
            "name": "Neha Sharma",
            "role": "Marketing Director – ILT20 UAE",
            "why": "Upcoming league season planning",
            "action": "Professional intro",
        },
    ]


def instagram_leads():
    """
    Placeholder for Instagram campaign discovery.
    Will be replaced by real tracking in Phase-3.
    """
    return [
        {
            "name": "Rohit Verma",
            "role": "Creative Producer – Sports Campaign Studio",
            "why": "Posted athlete shoot BTS yesterday",
            "action": "Warm relationship intro",
        },
        {
            "name": "Sana Ali",
            "role": "Brand Executive – Puma India",
            "why": "Tagged in new sports visual campaign",
            "action": "Premium short outreach",
        },
        {
            "name": "David Khan",
            "role": "League Media Manager – T10 Global",
            "why": "Season media prep started",
            "action": "Professional availability note",
        },
    ]


# =========================================================
# DASHBOARD BUILDER
# =========================================================

def generate_daily_dashboard():
    today = datetime.now().strftime("%d %b %Y")

    leads = linkedin_leads() + instagram_leads()

    dashboard = f"🎯 *DAILY CLIENT SCOUT — {today}*\n\n"

    for i, lead in enumerate(leads[:5], start=1):
        # Use Gemini to generate personalized outreach text
        try:
            prompt = f"""
Write a SHORT professional outreach message for:

Name: {lead['name']}
Role: {lead['role']}
Reason: {lead['why']}

Photographer credentials:
• 15+ years IPL & BCCI
• International cricket & leagues
• SBI Life campaign with Pant & Jadeja
Tone: premium, human, confident, not salesy.
Max 3 lines.
"""
            response = model.generate_content(prompt)
            message_text = response.text.strip()
        except Exception:
            message_text = "⚠️ Message generation error."

        dashboard += (
            f"{i}️⃣ *{lead['name']}*\n"
            f"Role: {lead['role']}\n"
            f"Why: {lead['why']}\n"
            f"Action: {lead['action']}\n\n"
            f"_Message ready:_\n{message_text}\n\n"
            f"────────────\n\n"
        )

    return dashboard


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

@bot.message_handler(commands=["start", "help"])
def welcome(message):
    bot.reply_to(
        message,
        "🤖 *AI Business Assistant Ready*\n\n"
        "Commands:\n"
        "/leads → Today’s client dashboard\n"
        "/ask → Ask anything\n",
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["leads"])
def send_leads(message):
    dashboard = generate_daily_dashboard()
    bot.send_message(message.chat.id, dashboard, parse_mode="Markdown")


@bot.message_handler(func=lambda m: True)
def chat_with_ai(message):
    try:
        response = model.generate_content(message.text)
        reply = response.text
    except Exception:
        reply = "⚠️ Gemini connection error."

    bot.reply_to(message, reply)


print("✅ AI Assistant running (Phase-2)…")
bot.infinity_polling()
