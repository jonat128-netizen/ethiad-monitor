"""
ETIHAD MONITOR — BOT TELEGRAM v2
Compatible python-telegram-bot 13.x
"""

import json
import logging
import os
import random
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from telegram import Update, Bot
from telegram.ext import Updater, CommandHandler, CallbackContext

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = int(os.environ.get("CHAT_ID", "0"))
CHECK_INTERVAL_SECONDS = 120 * 60
STATE_FILE = "reservations.json"

logging.basicConfig(format="%(asctime)s — %(levelname)s — %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
]

def load_data():
    if Path(STATE_FILE).exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def check_reservation(code, name):
    try:
        time.sleep(random.uniform(3, 8))
        session = requests.Session()
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9",
            "Referer": "https://www.etihad.com/fr-fr/manage",
        }
        session.get("https://www.etihad.com/fr-fr/manage", headers=headers, timeout=15)
        time.sleep(random.uniform(1, 3))
        response = session.post(
            "https://www.etihad.com/api/manage/retrieve-booking",
            json={"bookingReference": code.upper(), "lastName": name.upper()},
            headers={**headers, "Content-Type": "application/json"},
            timeout=15
        )
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "success" or data.get("booking"):
                booking = data.get("booking", {})
                flight_date_str = booking.get("departureDate", "")
                checkin_open = False
                if flight_date_str:
                    try:
                        flight_date = datetime.strptime(flight_date_str, "%Y-%m-%d")
                        hours_until = (flight_date - datetime.now()).total_seconds() / 3600
                        checkin_open = 0 <= hours_until <= 24
                    except:
                        pass
                fn = booking.get("flightNumber", "")
                orig = booking.get("origin", "")
                dest = booking.get("destination", "")
                detail = f"{fn} {orig}→{dest}" if fn else "Réservation confirmée"
                return {"status": "confirmed", "detail": detail, "checkin_open": checkin_open}
            return {"status": "not_found", "detail": "Réservation introuvable", "checkin_open": False}
        elif response.status_code == 404:
            return {"status": "not_found", "detail": "Réservation introuvable", "checkin_open": False}
        resp2 = session.get(f"https://www.etihad.com/fr-fr/manage?ref={code}&lastName={name}", headers=headers, timeout=15)
        soup = BeautifulSoup(resp2.text, "html.parser")
        txt = soup.get_text().lower()
        if any(k in txt for k in ["introuvable", "not found", "invalide"]):
            return {"status": "not_found", "detail": "Réservation introuvable", "checkin_open": False}
        if any(k in txt for k in ["vol", "flight", "départ", "passager"]):
            return {"status": "confirmed", "detail": "Réservation confirmée", "checkin_open": False}
        return {"status": "error", "detail": "Résultat indéterminé", "checkin_open": False}
    except Exception as e:
        return {"status": "error", "detail": str(e)[:80], "checkin_open": False}

def check_all(bot, silent=False):
    data = load_data()
    if not data:
        if not silent:
            bot.send_message(chat_id=CHAT_ID, text="📋 Aucune réservation.\nUtilise /add CODE NOM pour en ajouter une.")
        return
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    if not silent:
        bot.send_message(chat_id=CHAT_ID, text=f"🔍 Vérification de {len(data)} réservation(s)...\n⏱ {now}")
    for code, info in data.items():
        result = check_reservation(code, info["name"])
        prev = info.get("status", "unknown")
        new = result["status"]
        data[code]["status"] = new
        data[code]["last_check"] = now
        data[code]["detail"] = result["detail"]
        if prev == "confirmed" and new == "not_found":
            bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text=(
                f"🚨🚨🚨 <b>ALERTE — RÉSERVATION DISPARUE !</b>\n\n"
                f"✈️ Code : <b>{code}</b>\n"
                f"👤 Passager : <b>{info['name']}</b>\n\n"
                f"La réservation est <b>introuvable</b> sur etihad.com !\n\n"
                f"👉 https://www.etihad.com/fr-fr/manage"
            ))
        elif prev == "not_found" and new == "confirmed":
            bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text=(
                f"✅ <b>Réservation retrouvée !</b>\n\n✈️ <b>{code}</b>\n👤 {info['name']}"
            ))
        if result["checkin_open"] and not info.get("checkin_notified", False):
            bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text=(
                f"🛫 <b>CHECK-IN OUVERT !</b>\n\n✈️ <b>{code}</b>\n👤 {info['name']}\n\n"
                f"👉 https://www.etihad.com/fr-fr/manage/check-in"
            ))
            data[code]["checkin_notified"] = True
    save_data(data)
    if not silent:
        lines = []
        for code, info in data.items():
            emoji = {"confirmed": "✅", "not_found": "🚨", "error": "⚠️"}.get(info["status"], "❓")
            lines.append(f"{emoji} <b>{code}</b> — {info['name']}\n    └ {info['detail']}")
        bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text="📊 <b>Rapport</b>\n\n" + "\n\n".join(lines))

def cmd_start(update, ctx):
    update.message.reply_text(
        "✈️ <b>Etihad Monitor</b> — Bonjour !\n\n"
        "🚨 Alerte si une résa disparaît\n"
        "🛫 Alerte quand le check-in est ouvert\n\n"
        "<b>Commandes :</b>\n"
        "• /add CODE NOM\n• /remove CODE\n• /check\n• /list\n• /status",
        parse_mode="HTML")

def cmd_add(update, ctx):
    args = ctx.args
    if len(args) < 2:
        update.message.reply_text("❌ Usage : /add CODE NOM\nEx: /add ABC123 MARTIN")
        return
    code = args[0].upper()
    name = " ".join(args[1:]).upper()
    data = load_data()
    if code in data:
        update.message.reply_text(f"⚠️ {code} déjà dans la liste.")
        return
    data[code] = {"name": name, "status": "unknown", "last_check": "jamais",
                  "detail": "Pas encore vérifié", "checkin_notified": False}
    save_data(data)
    update.message.reply_text(f"✅ Ajouté ! <b>{code}</b> — {name}", parse_mode="HTML")

def cmd_remove(update, ctx):
    if not ctx.args:
        update.message.reply_text("❌ Usage : /remove CODE")
        return
    code = ctx.args[0].upper()
    data = load_data()
    if code not in data:
        update.message.reply_text(f"❌ {code} introuvable.")
        return
    name = data[code]["name"]
    del data[code]
    save_data(data)
    update.message.reply_text(f"🗑️ <b>{code}</b> ({name}) supprimé.", parse_mode="HTML")

def cmd_check(update, ctx):
    update.message.reply_text("⏳ Vérification en cours...")
    check_all(ctx.bot)

def cmd_list(update, ctx):
    data = load_data()
    if not data:
        update.message.reply_text("📋 Aucune réservation.")
        return
    lines = []
    for code, info in data.items():
        emoji = {"confirmed": "✅", "not_found": "🚨", "error": "⚠️", "unknown": "❓"}.get(info["status"], "❓")
        lines.append(f"{emoji} <b>{code}</b> — {info['name']}\n    └ {info['last_check']}")
    update.message.reply_text(f"📋 <b>{len(data)} réservation(s)</b>\n\n" + "\n\n".join(lines), parse_mode="HTML")

def cmd_status(update, ctx):
    data = load_data()
    update.message.reply_text(
        f"🤖 <b>Statut</b>\n\n"
        f"• Réservations : <b>{len(data)}</b>\n"
        f"• Confirmées : <b>{sum(1 for v in data.values() if v['status']=='confirmed')}</b> ✅\n"
        f"• Disparues : <b>{sum(1 for v in data.values() if v['status']=='not_found')}</b> 🚨\n"
        f"• Vérification : toutes les <b>2h</b>",
        parse_mode="HTML")

def auto_check_job(ctx):
    log.info("⏰ Vérification automatique")
    check_all(ctx.bot, silent=True)

def main():
    print("✈️  ETIHAD MONITOR — Démarrage...")
    updater = Updater(token=BOT_TOKEN)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start",  cmd_start))
    dp.add_handler(CommandHandler("add",    cmd_add))
    dp.add_handler(CommandHandler("remove", cmd_remove))
    dp.add_handler(CommandHandler("check",  cmd_check))
    dp.add_handler(CommandHandler("list",   cmd_list))
    dp.add_handler(CommandHandler("status", cmd_status))
    updater.job_queue.run_repeating(auto_check_job, interval=CHECK_INTERVAL_SECONDS, first=60)
    print("🟢 Bot actif !")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
