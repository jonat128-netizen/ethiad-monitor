"""
ETIHAD MONITOR — BOT TELEGRAM v3
Avec menu boutons inline
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
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters, CallbackContext

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = int(os.environ.get("CHAT_ID", "0"))
CHECK_INTERVAL_SECONDS = 120 * 60
STATE_FILE = "reservations.json"
WAITING_ADD = {}
WAITING_REMOVE = {}

logging.basicConfig(format="%(asctime)s — %(levelname)s — %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
]

# ══════════════════════════════════════════
#  DONNÉES
# ══════════════════════════════════════════

def load_data():
    if Path(STATE_FILE).exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ══════════════════════════════════════════
#  MENU PRINCIPAL
# ══════════════════════════════════════════

def menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Ajouter une résa", callback_data="add")],
        [InlineKeyboardButton("🔍 Vérifier maintenant", callback_data="check")],
        [InlineKeyboardButton("📋 Voir la liste", callback_data="list")],
        [InlineKeyboardButton("🗑 Supprimer une résa", callback_data="remove")],
        [InlineKeyboardButton("📊 Statut du bot", callback_data="status")],
    ])

def show_menu(bot, chat_id, text="Que veux-tu faire ?"):
    bot.send_message(
        chat_id=chat_id,
        text=f"✈️ <b>Etihad Monitor</b>\n\n{text}",
        parse_mode="HTML",
        reply_markup=menu_keyboard()
    )

# ══════════════════════════════════════════
#  VÉRIFICATION ETIHAD
# ══════════════════════════════════════════

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
        session.get("https://www.etihad.com/fr-fr/manage", headers=headers, timeout=30)
        time.sleep(random.uniform(1, 3))
        response = session.post(
            "https://www.etihad.com/api/manage/retrieve-booking",
            json={"bookingReference": code.upper(), "lastName": name.upper()},
            headers={**headers, "Content-Type": "application/json"},
            timeout=30
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
        resp2 = session.get(f"https://www.etihad.com/fr-fr/manage?ref={code}&lastName={name}", headers=headers, timeout=30)
        soup = BeautifulSoup(resp2.text, "html.parser")
        txt = soup.get_text().lower()
        if any(k in txt for k in ["introuvable", "not found", "invalide"]):
            return {"status": "not_found", "detail": "Réservation introuvable", "checkin_open": False}
        if any(k in txt for k in ["vol", "flight", "départ", "passager"]):
            return {"status": "confirmed", "detail": "Réservation confirmée", "checkin_open": False}
        return {"status": "error", "detail": "Résultat indéterminé", "checkin_open": False}
    except Exception as e:
        return {"status": "error", "detail": str(e)[:80], "checkin_open": False}

# ══════════════════════════════════════════
#  VÉRIFICATION GLOBALE
# ══════════════════════════════════════════

def check_all(bot, chat_id=None, silent=False):
    if chat_id is None:
        chat_id = CHAT_ID
    data = load_data()
    if not data:
        if not silent:
            show_menu(bot, chat_id, "📋 Aucune réservation à surveiller.\nClique sur ➕ pour en ajouter une !")
        return
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    if not silent:
        bot.send_message(chat_id=chat_id, text=f"🔍 Vérification de {len(data)} réservation(s)...\n⏱ {now}")

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
        bot.send_message(chat_id=chat_id, parse_mode="HTML", text="📊 <b>Rapport</b>\n\n" + "\n\n".join(lines))
        show_menu(bot, chat_id)

# ══════════════════════════════════════════
#  COMMANDES
# ══════════════════════════════════════════

def cmd_start(update, ctx):
    show_menu(ctx.bot, update.effective_chat.id, "Bienvenue ! Je surveille tes réservations Etihad 24h/24 🛡")

def cmd_menu(update, ctx):
    show_menu(ctx.bot, update.effective_chat.id)

# ══════════════════════════════════════════
#  GESTION DES BOUTONS
# ══════════════════════════════════════════

def handle_button(update, ctx):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat_id
    data_cb = query.data

    if data_cb == "add":
        WAITING_ADD[chat_id] = "code"
        ctx.bot.send_message(chat_id=chat_id, text=(
            "➕ <b>Ajouter une réservation</b>\n\n"
            "Envoie le <b>code de réservation</b>\n"
            "Ex: <code>BTX4NJ</code>"
        ), parse_mode="HTML")

    elif data_cb == "check":
        ctx.bot.send_message(chat_id=chat_id, text="⏳ Vérification en cours...")
        check_all(ctx.bot, chat_id)

    elif data_cb == "list":
        data = load_data()
        if not data:
            show_menu(ctx.bot, chat_id, "📋 Aucune réservation.\nClique sur ➕ pour en ajouter une !")
            return
        lines = []
        for code, info in data.items():
            emoji = {"confirmed": "✅", "not_found": "🚨", "error": "⚠️", "unknown": "❓"}.get(info["status"], "❓")
            lines.append(f"{emoji} <b>{code}</b> — {info['name']}\n    └ Vérifié : {info['last_check']}")
        ctx.bot.send_message(chat_id=chat_id, parse_mode="HTML",
            text=f"📋 <b>{len(data)} réservation(s)</b>\n\n" + "\n\n".join(lines))
        show_menu(ctx.bot, chat_id)

    elif data_cb == "remove":
        data = load_data()
        if not data:
            show_menu(ctx.bot, chat_id, "📋 Aucune réservation à supprimer.")
            return
        # Créer boutons pour chaque résa
        buttons = []
        for code, info in data.items():
            buttons.append([InlineKeyboardButton(
                f"🗑 {code} — {info['name']}", callback_data=f"del_{code}"
            )])
        buttons.append([InlineKeyboardButton("↩️ Retour", callback_data="back")])
        ctx.bot.send_message(chat_id=chat_id, text="Quelle réservation supprimer ?",
            reply_markup=InlineKeyboardMarkup(buttons))

    elif data_cb.startswith("del_"):
        code = data_cb[4:]
        data = load_data()
        if code in data:
            name = data[code]["name"]
            del data[code]
            save_data(data)
            show_menu(ctx.bot, chat_id, f"🗑 <b>{code}</b> ({name}) supprimé.")
        else:
            show_menu(ctx.bot, chat_id, "❌ Réservation introuvable.")

    elif data_cb == "status":
        data = load_data()
        ctx.bot.send_message(chat_id=chat_id, parse_mode="HTML", text=(
            f"📊 <b>Statut du bot</b>\n\n"
            f"• Réservations : <b>{len(data)}</b>\n"
            f"• Confirmées : <b>{sum(1 for v in data.values() if v['status']=='confirmed')}</b> ✅\n"
            f"• Disparues : <b>{sum(1 for v in data.values() if v['status']=='not_found')}</b> 🚨\n"
            f"• Vérification auto : toutes les <b>2h</b>\n"
            f"• Heure : {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        ))
        show_menu(ctx.bot, chat_id)

    elif data_cb == "back":
        show_menu(ctx.bot, chat_id)

# ══════════════════════════════════════════
#  GESTION DES MESSAGES TEXTE (pour /add)
# ══════════════════════════════════════════

def handle_text(update, ctx):
    chat_id = update.effective_chat.id
    text = update.message.text.strip().upper()

    if chat_id in WAITING_ADD:
        step = WAITING_ADD[chat_id]

        if step == "code":
            ctx.user_data["add_code"] = text
            WAITING_ADD[chat_id] = "name"
            update.message.reply_text(
                f"✅ Code : <b>{text}</b>\n\nMaintenant envoie le <b>nom de famille</b> du passager\nEx: <code>MARTIN</code>",
                parse_mode="HTML"
            )

        elif step == "name":
            code = ctx.user_data.get("add_code", "")
            name = text
            data = load_data()
            if code in data:
                del WAITING_ADD[chat_id]
                show_menu(ctx.bot, chat_id, f"⚠️ <b>{code}</b> est déjà dans la liste.")
                return
            data[code] = {
                "name": name, "status": "unknown", "last_check": "jamais",
                "detail": "Pas encore vérifié", "checkin_notified": False,
                "added": datetime.now().strftime("%d/%m/%Y %H:%M")
            }
            save_data(data)
            del WAITING_ADD[chat_id]
            show_menu(ctx.bot, chat_id, f"✅ Réservation ajoutée !\n\n✈️ <b>{code}</b> — {name}\n\nClique sur 🔍 pour vérifier maintenant !")
    else:
        show_menu(ctx.bot, chat_id)

# ══════════════════════════════════════════
#  JOB AUTO
# ══════════════════════════════════════════

def auto_check_job(ctx):
    log.info("⏰ Vérification automatique")
    check_all(ctx.bot, silent=True)

# ══════════════════════════════════════════
#  DÉMARRAGE
# ══════════════════════════════════════════

def main():
    print("✈️  ETIHAD MONITOR v3 — Démarrage...")
    updater = Updater(token=BOT_TOKEN)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", cmd_start))
    dp.add_handler(CommandHandler("menu",  cmd_menu))
    dp.add_handler(CallbackQueryHandler(handle_button))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

    updater.job_queue.run_repeating(auto_check_job, interval=CHECK_INTERVAL_SECONDS, first=60)

    print("🟢 Bot actif avec menu boutons !")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
