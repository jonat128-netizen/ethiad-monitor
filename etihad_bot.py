"""
ETIHAD MONITOR — BOT TELEGRAM v5
Avec Playwright pour vraiment lire la page Etihad
"""

import json
import logging
import os
import random
import threading
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (CallbackQueryHandler, CommandHandler,
                           MessageHandler, Filters, Updater)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = int(os.environ.get("CHAT_ID", "0"))
CHECK_INTERVAL_SECONDS = 120 * 60
STATE_FILE = "reservations.json"
WAITING_ADD = {}

logging.basicConfig(format="%(asctime)s — %(levelname)s — %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

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
    try:
        return datetime.strptime(date_str, "%d/%m/%Y")
    except:
        return None

# ══════════════════════════════════════════
#  VÉRIFICATION AVEC PLAYWRIGHT
# ══════════════════════════════════════════

def check_reservation(code, name):
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = browser.new_context(
                locale="fr-FR",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            log.info(f"Vérification {code} / {name}...")

            # Attendre que la page charge complètement avec le JS
            page.goto("https://www.etihad.com/fr-fr/manage", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)  # Attendre le JS

            # Prendre screenshot pour debug
            page_text_before = page.inner_text("body").lower()
            log.info(f"Page chargée, longueur texte: {len(page_text_before)}")

            # Chercher tous les inputs visibles
            inputs = page.query_selector_all("input")
            log.info(f"Nombre d inputs trouvés: {len(inputs)}")

            # Attendre que le formulaire soit visible
            try:
                page.wait_for_selector('#bookingReference', timeout=15000)
            except:
                browser.close()
                return {"status": "error", "detail": "Formulaire pas chargé — site Etihad lent"}

            ref_input = page.query_selector('#bookingReference')
            name_input = page.query_selector('#lastName')

            if not ref_input or not name_input:
                browser.close()
                return {"status": "error", "detail": "Formulaire introuvable"}

            # Scroller vers le formulaire
            ref_input.scroll_into_view_if_needed()
            page.wait_for_timeout(500)

            # Remplir les champs
            ref_input.click()
            page.wait_for_timeout(300)
            ref_input.fill(code.upper())
            page.wait_for_timeout(300)

            name_input.click()
            page.wait_for_timeout(300)
            name_input.fill(name.upper())
            page.wait_for_timeout(500)

            # Cliquer le bouton Search
            try:
                search_btn = page.wait_for_selector('button[aria-label="Search"]', timeout=10000)
                search_btn.scroll_into_view_if_needed()
                page.wait_for_timeout(300)
                search_btn.click()
                log.info(f"Bouton Search cliqué pour {code} / {name}")
            except:
                # Fallback : Entrée
                name_input.press("Enter")
                log.info(f"Fallback Entrée pour {code} / {name}")

            # Attendre que l URL change vers digital.etihad.com
            try:
                page.wait_for_url("*digital.etihad.com*", timeout=20000)
                log.info(f"✅ URL changée vers: {page.url}")
            except:
                log.info(f"⚠️ URL pas changée, actuelle: {page.url}")

            # Attendre que le contenu JS charge
            page.wait_for_timeout(6000)
            page_text_after = page.inner_text("body").lower()
            log.info(f"Texte après soumission ({len(page_text_after)} chars): {page_text_after[:500]}")

            # Attendre la réponse
            page_text = page_text_after
            browser.close()

            # Mots clés erreur SPÉCIFIQUES à la page de résultat
            error_kw = [
                "introuvable", "not found", "invalide", "incorrect",
                "aucune réservation", "no booking found", "we couldn",
                "unable to find", "could not find", "booking not found",
                "référence de réservation incorrecte", "incorrect booking"
            ]

            # Mots clés succès SPÉCIFIQUES (présents seulement sur la page de résa)
            success_kw = [
                "itinéraire", "itinerary", "vol prévu", "scheduled flight",
                "carte d'embarquement", "boarding pass", "numéro de vol",
                "flight number", "passager", "passenger name",
                "modifier mon vol", "change flight", "annuler", "cancel booking"
            ]

            if any(k in page_text for k in error_kw):
                return {"status": "not_found", "detail": "Réservation introuvable sur Etihad"}

            if any(k in page_text for k in success_kw):
                checkin_open = "check-in" in page_text and ("disponible" in page_text or "available" in page_text)
                checkin_done = "enregistré" in page_text or "checked in" in page_text or "boarding pass" in page_text
                detail = "Réservation confirmée"
                if checkin_done:
                    detail = "Réservation confirmée ✅ Check-in effectué"
                elif checkin_open:
                    detail = "Réservation confirmée 🛫 Check-in disponible !"
                return {"status": "confirmed", "detail": detail, "checkin_open": checkin_open, "checkin_done": checkin_done}

            # Si on ne trouve ni erreur ni succès clair → indéterminé
            return {"status": "error", "detail": "Résultat indéterminé — réessaie plus tard"}

    except Exception as e:
        log.error(f"Erreur Playwright {code}: {e}")
        return {"status": "error", "detail": str(e)[:80]}

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
    bot.send_message(chat_id=chat_id, text=f"✈️ <b>Etihad Monitor</b>\n\n{text}",
        parse_mode="HTML", reply_markup=menu_keyboard())

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

    for i, (code, info) in enumerate(data.items()):
        # Délai aléatoire entre chaque résa pour ne pas spammer Etihad
        if i > 0:
            delay = random.randint(120, 300)  # 2 à 5 minutes entre chaque
            log.info(f"Attente {delay}s avant prochaine vérification...")
            time.sleep(delay)

        result = check_reservation(code, info["name"])
        prev = info.get("status", "unknown")
        new  = result["status"]

        data[code]["status"]     = new
        data[code]["last_check"] = now
        data[code]["detail"]     = result["detail"]

        if prev == "confirmed" and new == "not_found":
            bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text=(
                f"🚨🚨🚨 <b>ALERTE — RÉSERVATION DISPARUE !</b>\n\n"
                f"✈️ Code : <b>{code}</b>\n"
                f"👤 Passager : <b>{info['name']}</b>\n"
                f"📅 Vol : <b>{info.get('flight_date','?')}</b>\n\n"
                f"Introuvable sur etihad.com !\n"
                f"👉 https://www.etihad.com/fr-fr/manage"
            ))
        elif prev == "not_found" and new == "confirmed":
            bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text=(
                f"✅ <b>Réservation retrouvée !</b>\n\n✈️ <b>{code}</b>\n👤 {info['name']}"
            ))

        # Alerte check-in ouvert
        if result.get("checkin_open") and not info.get("checkin_open_notified", False):
            bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text=(
                f"🛫 <b>CHECK-IN OUVERT !</b>\n\n"
                f"✈️ Code : <b>{code}</b>\n"
                f"👤 Passager : <b>{info['name']}</b>\n"
                f"📅 Vol : <b>{info.get('flight_date','?')}</b>\n\n"
                f"👉 https://www.etihad.com/fr-fr/manage/check-in"
            ))
            data[code]["checkin_open_notified"] = True

    save_data(data)

    if not silent:
        lines = []
        for code, info in data.items():
            emoji = {"confirmed":"✅","not_found":"🚨","error":"⚠️"}.get(info.get("status",""),"❓")
            lines.append(f"{emoji} <b>{code}</b> — {info['name']}\n    └ {info['detail']}")
        bot.send_message(chat_id=chat_id, parse_mode="HTML",
            text="📊 <b>Rapport</b>\n\n" + "\n\n".join(lines))
        show_menu(bot, chat_id)

# ══════════════════════════════════════════
#  RÉSUMÉ DU MATIN
# ══════════════════════════════════════════

def morning_summary(ctx):
    bot = ctx.bot
    data = load_data()
    if not data:
        return
    now = datetime.now()
    aujourd_hui, cette_semaine, a_venir = [], [], []

    for code, info in data.items():
        fd = parse_date(info.get("flight_date",""))
        if not fd:
            continue
        delta = (fd - now).days
        emoji = {"confirmed":"✅","not_found":"🚨","error":"⚠️"}.get(info.get("status",""),"❓")
        line = f"{emoji} <b>{code}</b> — {info['name']} — {info.get('flight_date','')}"
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
        msg += "Aucun vol à venir."
    msg += f"\n📋 Total : <b>{len(data)}</b> réservation(s)"
    bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="HTML")

# ══════════════════════════════════════════
#  ALERTES 12H AVANT
# ══════════════════════════════════════════

def checkin_alerts(ctx):
    bot = ctx.bot
    data = load_data()
    now = datetime.now()
    for code, info in data.items():
        fd = parse_date(info.get("flight_date",""))
        if not fd:
            continue
        hours_until = (fd - now).total_seconds() / 3600
        if 10 <= hours_until <= 12 and not info.get("checkin_done", False) and not info.get("checkin_12h_notified", False):
            bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text=(
                f"⚠️ <b>RAPPEL — CHECK-IN NON EFFECTUÉ !</b>\n\n"
                f"✈️ Code : <b>{code}</b>\n"
                f"👤 Passager : <b>{info['name']}</b>\n"
                f"📅 Vol dans environ <b>12 heures</b> !\n\n"
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
        def run_check():
            check_all(ctx.bot, chat_id)
        threading.Thread(target=run_check, daemon=True).start()

    elif data_cb == "list":
        data = load_data()
        if not data:
            show_menu(ctx.bot, chat_id, "📋 Aucune réservation.\nClique sur ➕ pour en ajouter une !")
            return
        lines = []
        now = datetime.now()
        for code, info in data.items():
            emoji = {"confirmed":"✅","not_found":"🚨","error":"⚠️","unknown":"❓"}.get(info.get("status",""),"❓")
            fd = parse_date(info.get("flight_date",""))
            delta = f" — dans {(fd-now).days}j" if fd and (fd-now).days >= 0 else ""
            lines.append(f"{emoji} <b>{code}</b> — {info['name']}\n    └ 📅 {info.get('flight_date','?')}{delta}")
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
                f"🗑 {code} — {info['name']} ({info.get('flight_date','?')})",
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
        vols_today = sum(1 for v in data.values() if parse_date(v.get("flight_date","")) and (parse_date(v.get("flight_date",""))-now).days == 0)
        vols_week  = sum(1 for v in data.values() if parse_date(v.get("flight_date","")) and 0 < (parse_date(v.get("flight_date",""))-now).days <= 7)
        ctx.bot.send_message(chat_id=chat_id, parse_mode="HTML", text=(
            f"📊 <b>Statut du bot</b>\n\n"
            f"• Total : <b>{len(data)}</b> réservations\n"
            f"• Confirmées : <b>{sum(1 for v in data.values() if v.get('status')=='confirmed')}</b> ✅\n"
            f"• Disparues : <b>{sum(1 for v in data.values() if v.get('status')=='not_found')}</b> 🚨\n"
            f"• Vols aujourd'hui : <b>{vols_today}</b> ✈️\n"
            f"• Vols cette semaine : <b>{vols_week}</b> 📅\n"
            f"• Vérification auto : toutes les <b>2h</b>\n"
            f"• Résumé matin : <b>8h00</b> 🌅\n"
            f"• Heure : {now.strftime('%d/%m/%Y %H:%M')}"
        ))
        show_menu(ctx.bot, chat_id)

    elif data_cb == "back":
        show_menu(ctx.bot, chat_id)

# ══════════════════════════════════════════
#  MESSAGES TEXTE
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
                f"Étape 2/3 — Envoie le <b>nom de famille</b>\n"
                f"Ex: <code>MARTIN</code>",
                parse_mode="HTML")

        elif step == "name":
            WAITING_ADD[chat_id]["name"] = text.upper()
            WAITING_ADD[chat_id]["step"] = "date"
            update.message.reply_text(
                f"✅ Nom : <b>{text.upper()}</b>\n\n"
                f"Étape 3/3 — Envoie la <b>date du vol</b>\n"
                f"Format : <code>JJ/MM/AAAA</code>\n"
                f"Ex: <code>15/05/2026</code>",
                parse_mode="HTML")

        elif step == "date":
            flight_date = parse_date(text)
            if not flight_date:
                update.message.reply_text(
                    "❌ Format incorrect.\nEx: <code>15/05/2026</code>",
                    parse_mode="HTML")
                return

            code = WAITING_ADD[chat_id]["code"]
            name = WAITING_ADD[chat_id]["name"]
            data = load_data()
            data[code] = {
                "name": name, "flight_date": text, "status": "unknown",
                "last_check": "jamais", "detail": "Vérification en cours...",
                "checkin_open_notified": False, "checkin_12h_notified": False,
                "checkin_done": False, "added": datetime.now().strftime("%d/%m/%Y %H:%M")
            }
            save_data(data)
            del WAITING_ADD[chat_id]

            delta = (flight_date - datetime.now()).days
            ctx.bot.send_message(chat_id=chat_id, parse_mode="HTML", text=(
                f"✅ <b>Réservation ajoutée !</b>\n\n"
                f"✈️ Code : <b>{code}</b>\n"
                f"👤 Passager : <b>{name}</b>\n"
                f"📅 Vol le : <b>{text}</b> (dans {delta} jours)\n\n"
                f"⏳ Vérification en cours..."
            ))

            def verify_bg(bot, code, name, chat_id):
                result = check_reservation(code, name)
                d = load_data()
                if code in d:
                    d[code]["status"] = result["status"]
                    d[code]["last_check"] = datetime.now().strftime("%d/%m/%Y %H:%M")
                    d[code]["detail"] = result["detail"]
                    save_data(d)
                status_text = {
                    "confirmed": f"✅ <b>{code} confirmée !</b> Réservation valide sur Etihad.",
                    "not_found": f"🚨 <b>{code} introuvable !</b> Vérifie le code et le nom.",
                    "error": f"⚠️ Impossible de vérifier <b>{code}</b>, réessaie dans quelques minutes."
                }.get(result["status"], "Statut inconnu")
                show_menu(bot, chat_id, status_text)

            threading.Thread(target=verify_bg, args=(ctx.bot, code, name, chat_id), daemon=True).start()
    else:
        show_menu(ctx.bot, chat_id)

# ══════════════════════════════════════════
#  JOBS AUTO
# ══════════════════════════════════════════

def auto_check_job(ctx):
    log.info("⏰ Vérification automatique")
    check_all(ctx.bot, silent=True)

# ══════════════════════════════════════════
#  DÉMARRAGE
# ══════════════════════════════════════════

def main():
    print("✈️  ETIHAD MONITOR v5 (Playwright) — Démarrage...")
    # Installer Chromium automatiquement au démarrage
    import subprocess
    print("📥 Installation de Chromium...")
    subprocess.run(["python", "-m", "playwright", "install", "--with-deps", "chromium"], check=True)
    print("✅ Chromium installé !")
    updater = Updater(token=BOT_TOKEN)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", cmd_start))
    dp.add_handler(CommandHandler("menu",  cmd_menu))
    dp.add_handler(CallbackQueryHandler(handle_button))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

    jq = updater.job_queue
    jq.run_repeating(auto_check_job, interval=CHECK_INTERVAL_SECONDS, first=60)
    jq.run_daily(morning_summary, time=datetime.strptime("08:00", "%H:%M").time())
    jq.run_repeating(checkin_alerts, interval=3600, first=120)

    print("🟢 Bot actif avec Playwright !")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
