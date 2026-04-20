"""
ETIHAD MONITOR — BOT TELEGRAM
Surveille les réservations Etihad toutes les 2h
et alerte quand une résa saute ou que le check-in est ouvert.
"""

import asyncio
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
from telegram.ext import Application, CommandHandler, ContextTypes

# ─────────────────────────────────────────────────────
#  CONFIGURATION (chargée depuis les variables Railway)
# ─────────────────────────────────────────────────────

BOT_TOKEN = os.environ.get("BOT_TOKEN", "VOTRE_TOKEN")
CHAT_ID   = int(os.environ.get("CHAT_ID", "0"))

CHECK_INTERVAL_MINUTES = 120  # toutes les 2h
STATE_FILE = "reservations.json"

logging.basicConfig(format="%(asctime)s — %(levelname)s — %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────
#  GESTION DES DONNÉES
# ─────────────────────────────────────────────────────

def load_data() -> dict:
    if Path(STATE_FILE).exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ─────────────────────────────────────────────────────
#  VÉRIFICATION ETIHAD
# ─────────────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

def check_reservation(code: str, name: str) -> dict:
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

        # Fallback scraping
        resp2 = session.get(
            f"https://www.etihad.com/fr-fr/manage?ref={code}&lastName={name}",
            headers=headers, timeout=15
        )
        soup = BeautifulSoup(resp2.text, "html.parser")
        txt = soup.get_text().lower()
        if any(k in txt for k in ["introuvable", "not found", "invalide"]):
            return {"status": "not_found", "detail": "Réservation introuvable", "checkin_open": False}
        if any(k in txt for k in ["vol", "flight", "départ", "passager"]):
            return {"status": "confirmed", "detail": "Réservation confirmée", "checkin_open": False}
        return {"status": "error", "detail": "Résultat indéterminé", "checkin_open": False}

    except requests.exceptions.Timeout:
        return {"status": "error", "detail": "Timeout — site Etihad lent", "checkin_open": False}
    except Exception as e:
        return {"status": "error", "detail": str(e)[:80], "checkin_open": False}

# ─────────────────────────────────────────────────────
#  VÉRIFICATION GLOBALE
# ─────────────────────────────────────────────────────

async def check_all(bot: Bot, silent: bool = False):
    data = load_data()
    if not data:
        if not silent:
            await bot.send_message(chat_id=CHAT_ID, text="📋 Aucune réservation.\nUtilise /add CODE NOM pour en ajouter une.")
        return

    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    if not silent:
        await bot.send_message(chat_id=CHAT_ID, text=f"🔍 Vérification de {len(data)} réservation(s)...\n⏱ {now}")

    for code, info in data.items():
        result = check_reservation(code, info["name"])
        prev = info.get("status", "unknown")
        new  = result["status"]

        data[code]["status"]     = new
        data[code]["last_check"] = now
        data[code]["detail"]     = result["detail"]

        if prev == "confirmed" and new == "not_found":
            await bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text=(
                f"🚨🚨🚨 <b>ALERTE — RÉSERVATION DISPARUE !</b>\n\n"
                f"✈️ Code : <b>{code}</b>\n"
                f"👤 Passager : <b>{info['name']}</b>\n\n"
                f"La réservation est <b>introuvable</b> sur etihad.com !\n\n"
                f"👉 https://www.etihad.com/fr-fr/manage"
            ))
        elif prev == "not_found" and new == "confirmed":
            await bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text=(
                f"✅ <b>Réservation retrouvée !</b>\n\n"
                f"✈️ Code : <b>{code}</b>\n👤 {info['name']}\n📋 {result['detail']}"
            ))

        if result["checkin_open"] and not info.get("checkin_notified", False):
            await bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text=(
                f"🛫 <b>CHECK-IN OUVERT !</b>\n\n"
                f"✈️ Code : <b>{code}</b>\n"
                f"👤 Passager : <b>{info['name']}</b>\n"
                f"📋 {result['detail']}\n\n"
                f"👉 https://www.etihad.com/fr-fr/manage/check-in"
            ))
            data[code]["checkin_notified"] = True

    save_data(data)

    if not silent:
        lines = []
        for code, info in data.items():
            emoji = {"confirmed":"✅","not_found":"🚨","error":"⚠️"}.get(info["status"],"❓")
            lines.append(f"{emoji} <b>{code}</b> — {info['name']}\n    └ {info['detail']}")
        await bot.send_message(chat_id=CHAT_ID, parse_mode="HTML",
            text="📊 <b>Rapport</b>\n\n" + "\n\n".join(lines))

# ─────────────────────────────────────────────────────
#  COMMANDES TELEGRAM
# ─────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ <b>Etihad Monitor</b> — Bonjour !\n\n"
        "Je surveille vos réservations toutes les 2h :\n"
        "🚨 Alerte si une résa disparaît\n"
        "🛫 Alerte quand le check-in est ouvert\n\n"
        "<b>Commandes :</b>\n"
        "• /add <code>CODE NOM</code>\n"
        "• /remove <code>CODE</code>\n"
        "• /check — Vérifier maintenant\n"
        "• /list — Voir toutes les réservations\n"
        "• /status — Statut du bot",
        parse_mode="HTML"
    )

async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 2:
        await update.message.reply_text("❌ Usage : /add CODE NOM\nEx: /add ABC123 MARTIN")
        return
    code = ctx.args[0].upper()
    name = " ".join(ctx.args[1:]).upper()
    data = load_data()
    if code in data:
        await update.message.reply_text(f"⚠️ {code} est déjà dans la liste.")
        return
    data[code] = {"name": name, "status": "unknown", "last_check": "jamais",
                  "detail": "Pas encore vérifié", "checkin_notified": False,
                  "added": datetime.now().strftime("%d/%m/%Y %H:%M")}
    save_data(data)
    await update.message.reply_text(
        f"✅ Ajouté !\n✈️ <b>{code}</b> — {name}\n\nUtilise /check pour vérifier maintenant.",
        parse_mode="HTML")

async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("❌ Usage : /remove CODE")
        return
    code = ctx.args[0].upper()
    data = load_data()
    if code not in data:
        await update.message.reply_text(f"❌ {code} introuvable.")
        return
    name = data[code]["name"]
    del data[code]
    save_data(data)
    await update.message.reply_text(f"🗑️ <b>{code}</b> ({name}) supprimé.", parse_mode="HTML")

async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Vérification en cours...")
    await check_all(ctx.bot)

async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not data:
        await update.message.reply_text("📋 Aucune réservation.\nUtilise /add CODE NOM.")
        return
    lines = []
    for code, info in data.items():
        emoji = {"confirmed":"✅","not_found":"🚨","error":"⚠️","unknown":"❓"}.get(info["status"],"❓")
        lines.append(f"{emoji} <b>{code}</b> — {info['name']}\n    └ {info['last_check']}")
    await update.message.reply_text(
        f"📋 <b>{len(data)} réservation(s)</b>\n\n" + "\n\n".join(lines), parse_mode="HTML")

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    await update.message.reply_text(
        f"🤖 <b>Statut</b>\n\n"
        f"• Réservations : <b>{len(data)}</b>\n"
        f"• Confirmées : <b>{sum(1 for v in data.values() if v['status']=='confirmed')}</b> ✅\n"
        f"• Disparues : <b>{sum(1 for v in data.values() if v['status']=='not_found')}</b> 🚨\n"
        f"• Vérification : toutes les <b>2h</b>\n"
        f"• Heure : {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        parse_mode="HTML")

# ─────────────────────────────────────────────────────
#  JOB AUTO + DÉMARRAGE
# ─────────────────────────────────────────────────────

async def auto_check_job(ctx: ContextTypes.DEFAULT_TYPE):
    log.info("⏰ Vérification automatique")
    await check_all(ctx.bot, silent=True)

def main():
    print("✈️  ETIHAD MONITOR — Démarrage...")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("add",    cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("check",  cmd_check))
    app.add_handler(CommandHandler("list",   cmd_list))
    app.add_handler(CommandHandler("status", cmd_status))
    app.job_queue.run_repeating(auto_check_job, interval=CHECK_INTERVAL_MINUTES * 60, first=60, name="auto_check")
    print("🟢 Bot actif !")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
