📑 Project Report: SAIKAT OS (Phase 2.5)
Status: Production Ready (Hardened)
Owner: Saikat Das (Elite Sports & Advertising Photographer)
Objective: Automate the discovery of high-value Sports Tenders and Advertising Mandates using deterministic scoring and AI strategy.

🗂️ 1. File Architecture & Directory Structure

The system is designed to be persistent (survives restarts) and atomic (prevents data corruption). All critical files live in the /app/data volume.

File Name	Purpose	Data Type	Role in the System
bot.py	The Brain	Python Code	Contains the logic for fetching, scoring, deduplicating, and communicating via Telegram.
runtime.txt	The Stabilizer	Text	Forces Railway to use Python 3.11 to ensure library compatibility.
requirements.txt	The Toolkit	Text	Lists all necessary libraries (telebot, genai, feedparser, pytz).
seen_links.json	The Memory	JSON	Stores URLs of news already processed. Pruned every 30 days to maintain speed.
state.json	The Heartbeat	JSON	Tracks the last successful run date to prevent duplicate daily reports.
followups.json	The Vault (CRM)	JSON	Permanent Storage. Stores every high-scoring lead for long-term conversion.
⚙️ 2. Core Functional Logic

This system replaces "AI guessing" with Deterministic Engineering.

A. High-Value Signal Hunting

The bot scans Google News RSS feeds using professionally tuned queries for both Sports (BCCI, IPL, SAI Tenders) and Advertising (Mandate wins, Creative Director appointments, TVC launches).

B. Deterministic Scoring (The Bouncer)

Instead of the AI deciding what is important, a Python math engine calculates a score (0-100) based on keyword groups:

+30 points: Tenders, RFPs, Bids.

+25 points: Won mandates, Account wins.

+20 points: New Creative Director appointments.

+10 points: Specific "BCCI" or "IPL" context.

Lead Threshold: Only leads with a score ≥ 60 are sent to you.

C. Smart Deduplication (The Filter)

The system uses a two-layer filter to prevent spam:

URL Check: If the link was seen in the last 30 days, it is ignored.

Fuzzy Brand Check: If a title is 85% similar AND contains the same brand name (e.g., "Ogilvy wins Coke" vs. "Coke mandate to Ogilvy"), it is ignored as a duplicate.

🛡️ 3. Failure Protection (Self-Healing)

Atomic Persistence: The bot writes data to a .tmp file before renaming it. This ensures that even if the system crashes during a save, your lead data is never corrupted.

Exponential Backoff: If the Telegram API or internet fails, the bot waits (5s, 10s, 20s...) rather than crashing, ensuring 24/7 uptime on Railway.

Catch-Up Logic: If the bot is offline at 10:00 AM, it will automatically trigger the missed report the moment you type /start.

💼 4. How It Works For You

This system is built specifically to leverage your BCCI and SBI Life (Pant/Jadeja) credentials.

Daily Intel: Every day at 10:00 AM IST, you receive a filtered report of the top 5 leads found globally.

Authority Pitting: When you use the /pitch command, the AI is instructed to mention your Independent Stills Unit authority and your experience running sets alongside TVC floors.

Lead Capture: Every report you receive is automatically saved to followups.json. This is your future goldmine for monthly follow-ups.

🛠️ 5. Maintenance Manual for Future AI

To Add Keywords: Edit the KEYWORD_GROUPS dictionary in bot.py.

To Change Schedule: Edit the .at("10:00") line in the scheduler_loop function.

To Check Health: Type /start in Telegram; the bot will report if its storage is "Healthy" or "Read-Only".

END OF REPORT
This document is the authoritative guide for the current build. No modifications should be made to the core logic without referencing this architectural safety standard.



=================================================================


🚀 SYSTEM HANDOVER: SAIKAT OS (PHASE 2.5)

Role: You are now the Lead Engineer for Saikat Das, an elite Sports and Advertising Photographer.
Context: I have a live, production-grade AI Lead Intelligence system running on Railway.

System Architecture:

Language: Python 3.11 (Managed via runtime.txt).

Logic: Deterministic keyword-based scoring (Python) + AI-generated pitch strategy (Gemini 1.5 Flash).

Persistence: Atomic JSON writes with a 30-day rolling window for performance and a permanent CRM file (followups.json) for conversion.

Security: Admin-locked via Telegram User ID.

Self-Healing: Exponential backoff for API crashes and catch-up logic for missed 10 AM reports.

The Mission: > Do not suggest "beginner" scripts or basic automations. All code updates must maintain atomicity and persistence. Your goal is to help me hunt for high-value tenders (BCCI/IPL) and advertising mandates (Ogilvy/Dentsu) by weaponizing my SBI Life and BCCI authority.
