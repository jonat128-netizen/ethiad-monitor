"""
ETIHAD MONITOR — BOT TELEGRAM v4
- Menu boutons
- Résumé automatique chaque matin
- Alerte check-in ouvert (24h avant)
- Alerte 12h avant si check-in pas fait
"""

import json
import logging
import os
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters, CallbackContext

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = int(os.environ.get("CHAT_ID", "0"))
CHECK_INTERVAL_SECONDS = 120 * 60  # 2h
STATE_FILE = "reservations.json"
WAITING_ADD = {}  # chat_id -> étape

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

def parse_date(date_str):
    """Parse une date au format DD/MM/YYYY"""
    try:
        return datetime.strptime(date_str, "%d/%m/%Y")
    except:
        return None

# ══════════════════════════════════════════
#  MENU
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
                fn = booking.get("flightNumber", "")
                orig = booking.get("origin", "")
                dest = booking.get("destination", "")
                detail = f"{fn} {orig}→{dest}" if fn else "Réservation confirmée"
                return {"status": "confirmed", "detail": detail}
            return {"status": "not_found", "detail": "Réservation introuvable"}
        elif response.status_code == 404:
            return {"status": "not_found", "detail": "Réservation introuvable"}
        resp2 = session.get(
            f"https://www.etihad.com/fr-fr/manage?ref={code}&lastName={name}",
            headers=headers, timeout=30
        )
        soup = BeautifulSoup(resp2.text, "html.parser")
        txt = soup.get_text().lower()
        if any(k in txt for k in ["introuvable", "not found", "invalide"]):
            return {"status": "not_found", "detail": "Réservation introuvable"}
        if any(k in txt for k in ["vol", "flight", "départ", "passager"]):
            return {"status": "confirmed", "detail": "Réservation confirmée"}
        return {"status": "error", "detail": "Résultat indéterminé"}
    except Exception as e:
        return {"status": "error", "detail": str(e)[:80]}

# ══════════════════════════════════════════
#  VÉRIFICATION GLOBALE
# ══════════════════════════════════════════

def check_all(bot, chat_id=None, silent=False):
    if chat_id is None:
        chat_id = CHAT_ID
    data = load_data()
    if not data:
        if not silent:
            show_menu(bot, chat_id, "📋 Aucune réservation.\nClique sur ➕ pour en ajouter une !")
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

        # Alerte résa disparue
        if prev == "confirmed" and new == "not_found":
            bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text=(
                f"🚨🚨🚨 <b>ALERTE — RÉSERVATION DISPARUE !</b>\n\n"
                f"✈️ Code : <b>{code}</b>\n"
                f"👤 Passager : <b>{info['name']}</b>\n"
                f"📅 Vol : <b>{info.get('flight_date', 'non renseigné')}</b>\n\n"
                f"La réservation est <b>introuvable</b> sur etihad.com !\n"
                f"👉 https://www.etihad.com/fr-fr/manage"
            ))
        elif prev == "not_found" and new == "confirmed":
            bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text=(
                f"✅ <b>Réservation retrouvée !</b>\n\n✈️ <b>{code}</b>\n👤 {info['name']}"
            ))

    save_data(data)

    if not silent:
        lines = []
        for code, info in data.items():
            emoji = {"confirmed": "✅", "not_found": "🚨", "error": "⚠️"}.get(info["status"], "❓")
            lines.append(f"{emoji} <b>{code}</b> — {info['name']}\n    └ {info['detail']}")
        bot.send_message(chat_id=chat_id, parse_mode="HTML",
            text="📊 <b>Rapport</b>\n\n" + "\n\n".join(lines))
        show_menu(bot, chat_id)

# ══════════════════════════════════════════
#  RÉSUMÉ DU MATIN (8h00)
# ══════════════════════════════════════════

def morning_summary(ctx):
    bot = ctx.bot
    data = load_data()
    if not data:
        return

    now = datetime.now()
    aujourd_hui = []
    cette_semaine = []
    a_venir = []

    for code, info in data.items():
        flight_date = parse_date(info.get("flight_date", ""))
        if not flight_date:
            continue
        delta = (flight_date - now).days
        emoji = {"confirmed": "✅", "not_found": "🚨", "error": "⚠️"}.get(info.get("status", ""), "❓")
        line = f"{emoji} <b>{code}</b> — {info['name']} — {info.get('flight_date', '')}"
        if delta == 0:
            aujourd_hui.append(line)
        elif 1 <= delta <= 7:
            cette_semaine.append(f"{line} (dans {delta}j)")
        elif delta > 7:
            a_venir.append(f"{line} (dans {delta}j)")

    msg = f"🌅 <b>Bonjour ! Résumé du {now.strftime('%d/%m/%Y')}</b>\n\n"

    if aujourd_hui:
        msg += "🔴 <b>VOL AUJOURD'HUI</b>\n" + "\n".join(aujourd_hui) + "\n\n"
    if cette_semaine:
        msg += "🟠 <b>CETTE SEMAINE</b>\n" + "\n".join(cette_semaine) + "\n\n"
    if a_venir:
        msg += "🟢 <b>À VENIR</b>\n" + "\n".join(a_venir) + "\n\n"

    if not aujourd_hui and not cette_semaine and not a_venir:
        msg += "Aucun vol à venir cette semaine."

    msg += f"\n📋 Total surveillé : <b>{len(data)}</b> réservation(s)"
    bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="HTML")

# ══════════════════════════════════════════
#  ALERTES CHECK-IN ET 12H AVANT
# ══════════════════════════════════════════

def checkin_alerts(ctx):
    bot = ctx.bot
    data = load_data()
    now = datetime.now()

    for code, info in data.items():
        flight_date = parse_date(info.get("flight_date", ""))
        if not flight_date:
            continue

        hours_until = (flight_date - now).total_seconds() / 3600

        # Alerte check-in ouvert (entre 24h et 22h avant)
        if 22 <= hours_until <= 24 and not info.get("checkin_open_notified", False):
            bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text=(
                f"🛫 <b>CHECK-IN OUVERT !</b>\n\n"
                f"✈️ Code : <b>{code}</b>\n"
                f"👤 Passager : <b>{info['name']}</b>\n"
                f"📅 Vol : <b>{info.get('flight_date', '')}</b>\n\n"
                f"Le check-in en ligne est disponible !\n"
                f"👉 https://www.etihad.com/fr-fr/manage/check-in"
            ))
            data[code]["checkin_open_notified"] = True

        # Alerte 12h avant si check-in pas encore fait
        if 10 <= hours_until <= 12 and not info.get("checkin_done", False) and not info.get("checkin_12h_notified", False):
            bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text=(
                f"⚠️ <b>RAPPEL — CHECK-IN NON EFFECTUÉ !</b>\n\n"
                f"✈️ Code : <b>{code}</b>\n"
                f"👤 Passager : <b>{info['name']}</b>\n"
                f"📅 Vol dans environ <b>12 heures</b> !\n\n"
                f"Le check-in n'a pas encore été fait !\n"
                f"👉 https://www.etihad.com/fr-fr/manage/check-in"
            ))
            data[code]["checkin_12h_notified"] = True

    save_data(data)

# ══════════════════════════════════════════
#  COMMANDES
# ══════════════════════════════════════════

def cmd_start(update, ctx):
    show_menu(ctx.bot, update.effective_chat.id, "Bienvenue ! Je surveille tes réservations Etihad 24h/24 🛡")

def cmd_menu(update, ctx):
    show_menu(ctx.bot, update.effective_chat.id)

# ══════════════════════════════════════════
#  BOUTONS
# ══════════════════════════════════════════

def handle_button(update, ctx):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat_id
    data_cb = query.data

    if data_cb == "add":
        WAITING_ADD[chat_id] = {"step": "code"}
        ctx.bot.send_message(chat_id=chat_id, parse_mode="HTML", text=(
            "➕ <b>Ajouter une réservation</b>\n\n"
            "Étape 1/3 — Envoie le <b>code de réservation</b>\n"
            "Ex: <code>BTX4NJ</code>"
        ))

    elif data_cb == "check":
        ctx.bot.send_message(chat_id=chat_id, text="⏳ Vérification en cours...")
        check_all(ctx.bot, chat_id)

    elif data_cb == "list":
        data = load_data()
        if not data:
            show_menu(ctx.bot, chat_id, "📋 Aucune réservation.\nClique sur ➕ pour en ajouter une !")
            return
        lines = []
        now = datetime.now()
        for code, info in data.items():
            emoji = {"confirmed": "✅", "not_found": "🚨", "error": "⚠️", "unknown": "❓"}.get(info.get("status", ""), "❓")
            flight_date = parse_date(info.get("flight_date", ""))
            delta = f" — dans {(flight_date - now).days}j" if flight_date and (flight_date - now).days >= 0 else ""
            lines.append(f"{emoji} <b>{code}</b> — {info['name']}\n    └ 📅 {info.get('flight_date', 'date ?')}{delta}")
        ctx.bot.send_message(chat_id=chat_id, parse_mode="HTML",
            text=f"📋 <b>{len(data)} réservation(s)</b>\n\n" + "\n\n".join(lines))
        show_menu(ctx.bot, chat_id)

    elif data_cb == "remove":
        data = load_data()
        if not data:
            show_menu(ctx.bot, chat_id, "📋 Aucune réservation à supprimer.")
            return
        buttons = []
        for code, info in data.items():
            buttons.append([InlineKeyboardButton(
                f"🗑 {code} — {info['name']} ({info.get('flight_date', '?')})",
                callback_data=f"del_{code}"
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
        now = datetime.now()
        vols_aujourd_hui = sum(1 for v in data.values() if parse_date(v.get("flight_date","")) and (parse_date(v.get("flight_date","")) - now).days == 0)
        vols_semaine = sum(1 for v in data.values() if parse_date(v.get("flight_date","")) and 0 < (parse_date(v.get("flight_date","")) - now).days <= 7)
        ctx.bot.send_message(chat_id=chat_id, parse_mode="HTML", text=(
            f"📊 <b>Statut du bot</b>\n\n"
            f"• Total réservations : <b>{len(data)}</b>\n"
            f"• Confirmées : <b>{sum(1 for v in data.values() if v.get('status')=='confirmed')}</b> ✅\n"
            f"• Disparues : <b>{sum(1 for v in data.values() if v.get('status')=='not_found')}</b> 🚨\n"
            f"• Vols aujourd'hui : <b>{vols_aujourd_hui}</b> ✈️\n"
            f"• Vols cette semaine : <b>{vols_semaine}</b> 📅\n"
            f"• Vérification auto : toutes les <b>2h</b>\n"
            f"• Résumé matin : <b>8h00</b> 🌅\n"
            f"• Heure : {now.strftime('%d/%m/%Y %H:%M')}"
        ))
        show_menu(ctx.bot, chat_id)

    elif data_cb == "back":
        show_menu(ctx.bot, chat_id)

# ══════════════════════════════════════════
#  MESSAGES TEXTE (ajout étape par étape)
# ══════════════════════════════════════════

def handle_text(update, ctx):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    if chat_id in WAITING_ADD:
        step = WAITING_ADD[chat_id]["step"]

        if step == "code":
            WAITING_ADD[chat_id]["code"] = text.upper()
            WAITING_ADD[chat_id]["step"] = "name"
            update.message.reply_text(
                f"✅ Code : <b>{text.upper()}</b>\n\n"
                f"Étape 2/3 — Envoie le <b>nom de famille</b> du passager\n"
                f"Ex: <code>MARTIN</code>",
                parse_mode="HTML"
            )

        elif step == "name":
            WAITING_ADD[chat_id]["name"] = text.upper()
            WAITING_ADD[chat_id]["step"] = "date"
            update.message.reply_text(
                f"✅ Nom : <b>{text.upper()}</b>\n\n"
                f"Étape 3/3 — Envoie la <b>date du vol</b>\n"
                f"Format : <code>JJ/MM/AAAA</code>\n"
                f"Ex: <code>15/05/2026</code>",
                parse_mode="HTML"
            )

        elif step == "date":
            flight_date = parse_date(text)
            if not flight_date:
                update.message.reply_text(
                    "❌ Format incorrect. Envoie la date comme ça :\n"
                    "<code>15/05/2026</code>",
                    parse_mode="HTML"
                )
                return

            code = WAITING_ADD[chat_id]["code"]
            name = WAITING_ADD[chat_id]["name"]
            data = load_data()
            data[code] = {
                "name": name,
                "flight_date": text,
                "status": "unknown",
                "last_check": "jamais",
                "detail": "Pas encore vérifié",
                "checkin_open_notified": False,
                "checkin_12h_notified": False,
                "checkin_done": False,
                "added": datetime.now().strftime("%d/%m/%Y %H:%M")
            }
            save_data(data)
            del WAITING_ADD[chat_id]

            delta = (flight_date - datetime.now()).days
            show_menu(ctx.bot, chat_id,
                f"✅ <b>Réservation ajoutée !</b>\n\n"
                f"✈️ Code : <b>{code}</b>\n"
                f"👤 Passager : <b>{name}</b>\n"
                f"📅 Vol le : <b>{text}</b> (dans {delta} jours)\n\n"
                f"Clique sur 🔍 pour vérifier maintenant !"
            )
    else:
        show_menu(ctx.bot, chat_id)

# ══════════════════════════════════════════
#  JOBS AUTOMATIQUES
# ══════════════════════════════════════════

def auto_check_job(ctx):
    log.info("⏰ Vérification automatique")
    check_all(ctx.bot, silent=True)

# ══════════════════════════════════════════
#  DÉMARRAGE
# ══════════════════════════════════════════

def main():
    print("✈️  ETIHAD MONITOR v4 — Démarrage...")
    updater = Updater(token=BOT_TOKEN)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", cmd_start))
    dp.add_handler(CommandHandler("menu", cmd_menu))
    dp.add_handler(CallbackQueryHandler(handle_button))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

    jq = updater.job_queue

    # Vérification toutes les 2h
    jq.run_repeating(auto_check_job, interval=CHECK_INTERVAL_SECONDS, first=60)

    # Résumé chaque matin à 8h00
    jq.run_daily(morning_summary, time=datetime.strptime("08:00", "%H:%M").time())

    # Vérification check-in toutes les heures
    jq.run_repeating(checkin_alerts, interval=3600, first=120)

    print("🟢 Bot actif !")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
