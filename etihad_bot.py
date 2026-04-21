"""
ETIHAD MONITOR — BOT TELEGRAM v6
- Alertes immediates si resa introuvable
- Sauvegarde apres chaque verification
- Check-in detection
- Suppression auto des vols passes
"""

import json
import logging
import os
import random
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, Filters, Updater

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = -5122598711
CHECK_INTERVAL_SECONDS = 90 * 60
STATE_FILE = "reservations.json"
WAITING_ADD = {}

REDIS_URL   = os.environ.get("UPSTASH_REDIS_REST_URL", "")
REDIS_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
REDIS_KEY   = "etihad_reservations"

def redis_save(data):
    if not REDIS_URL:
        return
    try:
        val = json.dumps(data, ensure_ascii=False)
        requests.post(
            REDIS_URL + "/set/" + REDIS_KEY,
            headers={"Authorization": "Bearer " + REDIS_TOKEN},
            json=val,
            timeout=5
        )
    except Exception as e:
        log.error("Redis save error: " + str(e))

def redis_load():
    if not REDIS_URL:
        return None
    try:
        r = requests.get(
            REDIS_URL + "/get/" + REDIS_KEY,
            headers={"Authorization": "Bearer " + REDIS_TOKEN},
            timeout=5
        )
        result = r.json().get("result")
        if result:
            return json.loads(result)
    except Exception as e:
        log.error("Redis load error: " + str(e))
    return None

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
    # Essayer Redis d abord
    redis_data = redis_load()
    if redis_data is not None:
        return redis_data
    # Fallback fichier local
    if Path(STATE_FILE).exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data):
    # Sauvegarder dans Redis ET fichier local
    redis_save(data)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def parse_date(date_str):
    try:
        return datetime.strptime(date_str, "%d/%m/%Y")
    except:
        return None

# ══════════════════════════════════════════
#  VÉRIFICATION PLAYWRIGHT
# ══════════════════════════════════════════

def check_reservation(code, name):
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = browser.new_context(
                locale="fr-FR",
                user_agent=random.choice(USER_AGENTS)
            )
            page = context.new_page()
            page.set_default_timeout(60000)

            log.info("Verification " + code + " / " + name)
            page.goto("https://www.etihad.com/fr-fr/manage", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            try:
                page.wait_for_selector("#bookingReference", state="attached", timeout=15000)
            except:
                browser.close()
                return {"status": "error", "detail": "Formulaire pas charge", "checkin_open": False, "checkin_done": False}

            page.wait_for_timeout(1000)

            ref_input = page.query_selector("#bookingReference")
            name_input = page.query_selector("#lastName")

            if not ref_input or not name_input:
                browser.close()
                return {"status": "error", "detail": "Champs introuvables", "checkin_open": False, "checkin_done": False}

            ref_input.type(code.upper(), delay=80)
            page.wait_for_timeout(500)
            name_input.type(name.upper(), delay=80)
            page.wait_for_timeout(500)

            ref_val = page.evaluate("document.querySelector('#bookingReference').value")
            name_val = page.evaluate("document.querySelector('#lastName').value")
            log.info("Champs: ref=" + str(ref_val) + " name=" + str(name_val))

            search_btn = page.query_selector('button[aria-label="Search"]')
            if search_btn:
                search_btn.click()
            else:
                name_input.press("Enter")

            url_changed = False
            try:
                page.wait_for_url("*digital.etihad.com*", timeout=20000)
                log.info("URL changee: " + page.url)
                url_changed = True
            except:
                log.info("URL pas changee: " + page.url)

            # Si URL pas changee = formulaire pas soumis ou resa invalide
            if not url_changed:
                browser.close()
                return {"status": "not_found", "detail": "Reservation introuvable sur Etihad", "checkin_open": False, "checkin_done": False}

            page.wait_for_timeout(6000)
            page_text = page.inner_text("body").lower()
            browser.close()

            log.info("Texte page (" + str(len(page_text)) + " chars): " + page_text[:300])

            error_kw = [
                "nous n'avons pas trouve de reservation",
                "nous n avons pas trouve",
                "veuillez verifier et reessayer",
                "unable to find a booking",
                "we're unable to find",
                "please check and try again",
                "not found", "invalide", "incorrect",
                "no booking found", "could not find",
            ]

            success_kw = [
                "reference de voyage", "voyage a destination",
                "informations de vol", "terminal",
                "adulte", "modification", "etihad"
            ]

            page_text_norm = page_text.replace("'", " ").replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a")

            for kw in error_kw:
                kw_norm = kw.replace("'", " ").replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a")
                if kw_norm in page_text_norm:
                    return {"status": "not_found", "detail": "Reservation introuvable sur Etihad", "checkin_open": False, "checkin_done": False}

            if any(k in page_text for k in success_kw):
                checkin_done = (
                    "vous etes deja enregistre" in page_text_norm or
                    "already checked in" in page_text or
                    "boarding pass" in page_text or
                    "enregistrement termine" in page_text_norm
                )
                checkin_open = (
                    "l enregistrement se termine" in page_text_norm or
                    "enregistrement en ligne est disponible" in page_text_norm or
                    "check-in is open" in page_text
                ) and not checkin_done

                detail = "Reservation confirmee"
                if "voyage a destination de" in page_text_norm:
                    idx = page_text_norm.find("voyage a destination de")
                    dest = page_text[idx+23:idx+50].strip().split("\n")[0].title()
                    detail = "Reservation confirmee - " + dest
                if checkin_done:
                    detail += " - Check-in effectue"
                elif checkin_open:
                    detail += " - Check-in disponible"

                return {"status": "confirmed", "detail": detail, "checkin_open": checkin_open, "checkin_done": checkin_done}

            return {"status": "error", "detail": "Resultat indetermine", "checkin_open": False, "checkin_done": False}

    except Exception as e:
        log.error("Erreur Playwright " + code + ": " + str(e))
        return {"status": "error", "detail": str(e)[:80], "checkin_open": False, "checkin_done": False}

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
        text="✈️ <b>Etihad Monitor</b>\n\n" + text,
        parse_mode="HTML",
        reply_markup=menu_keyboard()
    )

# ══════════════════════════════════════════
#  VÉRIFICATION GLOBALE
# ══════════════════════════════════════════

def check_all(bot, chat_id=None, silent=False):
    if chat_id is None:
        chat_id = CHAT_ID
    data = load_data()
    if not data:
        if not silent:
            show_menu(bot, chat_id, "Aucune reservation. Clique sur + pour en ajouter une !")
        return

    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    if not silent:
        bot.send_message(chat_id=chat_id, text="🔍 Verification de " + str(len(data)) + " reservation(s)...\n⏱ " + now)

    for i, (code, info) in enumerate(data.items()):
        if i > 0:
            delay = random.randint(120, 300)
            log.info("Attente " + str(delay) + "s...")
            time.sleep(delay)

        result = check_reservation(code, info["name"])
        new = result["status"]

        data[code]["status"] = new
        data[code]["last_check"] = now
        data[code]["detail"] = result["detail"]
        data[code]["checkin_done"] = result.get("checkin_done", False)
        data[code]["checkin_open"] = result.get("checkin_open", False)
        save_data(data)

        # ALERTE IMMEDIATE si introuvable
        if new == "not_found":
            alerte = (
                "🚨🚨🚨 <b>ALERTE — RESERVATION DISPARUE !</b>\n\n"
                "✈️ Code : <b>" + code + "</b>\n"
                "👤 Passager : <b>" + info["name"] + "</b>\n"
                "📅 Vol : <b>" + info.get("flight_date", "?") + "</b>\n\n"
                "❌ INTROUVABLE sur Etihad !\n"
                "👉 https://www.etihad.com/fr-fr/manage"
            )
            for _ in range(3):
                try:
                    bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text=alerte)
                    time.sleep(2)
                except:
                    pass
            log.info("ALERTE 3x envoyee pour " + code)

        elif result.get("checkin_open") and not info.get("checkin_open_notified", False):
            bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text=(
                "🛫 <b>CHECK-IN OUVERT !</b>\n\n"
                "✈️ Code : <b>" + code + "</b>\n"
                "👤 Passager : <b>" + info["name"] + "</b>\n"
                "📅 Vol : <b>" + info.get("flight_date", "?") + "</b>\n\n"
                "👉 https://www.etihad.com/fr-fr/manage/check-in"
            ))
            data[code]["checkin_open_notified"] = True
            save_data(data)

    if not silent:
        lines = []
        for code, info in data.items():
            emoji = {"confirmed": "✅", "not_found": "🚨", "error": "⚠️"}.get(info.get("status", ""), "❓")
            lines.append(emoji + " <b>" + code + "</b> — " + info["name"] + "\n    └ " + info.get("detail", "—"))
        bot.send_message(chat_id=chat_id, parse_mode="HTML", text="📊 <b>Rapport</b>\n\n" + "\n\n".join(lines))
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
        fd = parse_date(info.get("flight_date", ""))
        if not fd:
            continue
        delta = (fd - now).days
        emoji = {"confirmed": "✅", "not_found": "🚨", "error": "⚠️"}.get(info.get("status", ""), "❓")
        line = emoji + " <b>" + code + "</b> — " + info["name"] + " — " + info.get("flight_date", "")
        if delta == 0:
            aujourd_hui.append(line)
        elif 1 <= delta <= 7:
            cette_semaine.append(line + " (dans " + str(delta) + "j)")
        elif delta > 7:
            a_venir.append(line + " (dans " + str(delta) + "j)")

    msg = "🌅 <b>Bonjour ! Résumé du " + now.strftime("%d/%m/%Y") + "</b>\n\n"
    if aujourd_hui:
        msg += "🔴 <b>VOL AUJOURD'HUI</b>\n" + "\n".join(aujourd_hui) + "\n\n"
    if cette_semaine:
        msg += "🟠 <b>CETTE SEMAINE</b>\n" + "\n".join(cette_semaine) + "\n\n"
    if a_venir:
        msg += "🟢 <b>À VENIR</b>\n" + "\n".join(a_venir) + "\n\n"
    if not aujourd_hui and not cette_semaine and not a_venir:
        msg += "Aucun vol à venir."
    msg += "\n📋 Total : <b>" + str(len(data)) + "</b> reservation(s)"
    bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="HTML")

# ══════════════════════════════════════════
#  ALERTES 12H AVANT
# ══════════════════════════════════════════

def checkin_alerts(ctx):
    bot = ctx.bot
    data = load_data()
    now = datetime.now()
    for code, info in data.items():
        fd = parse_date(info.get("flight_date", ""))
        if not fd:
            continue
        hours_until = (fd - now).total_seconds() / 3600
        if 10 <= hours_until <= 12 and not info.get("checkin_done", False) and not info.get("checkin_12h_notified", False):
            bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text=(
                "⚠️ <b>RAPPEL — CHECK-IN NON EFFECTUE !</b>\n\n"
                "✈️ Code : <b>" + code + "</b>\n"
                "👤 Passager : <b>" + info["name"] + "</b>\n"
                "📅 Vol dans environ <b>12 heures</b> !\n\n"
                "👉 https://www.etihad.com/fr-fr/manage/check-in"
            ))
            data[code]["checkin_12h_notified"] = True
    save_data(data)

# ══════════════════════════════════════════
#  COMMANDES
# ══════════════════════════════════════════

def cmd_start(update, ctx):
    show_menu(ctx.bot, update.effective_chat.id, "Bienvenue ! Je surveille vos reservations Etihad 24h/24")

def cmd_menu(update, ctx):
    show_menu(ctx.bot, update.effective_chat.id)

# ══════════════════════════════════════════
#  BOUTONS
# ══════════════════════════════════════════

def handle_button(update, ctx):
    query = update.callback_query
    try:
        query.answer()
    except:
        pass
    chat_id = query.message.chat_id
    data_cb = query.data

    if data_cb == "add":
        WAITING_ADD[chat_id] = {"step": "code"}
        ctx.bot.send_message(chat_id=chat_id, parse_mode="HTML", text=(
            "➕ <b>Ajouter une reservation</b>\n\n"
            "Etape 1/3 — Envoie le <b>code de reservation</b>\n"
            "Ex: <code>BTX4NJ</code>"
        ))

    elif data_cb == "check":
        ctx.bot.send_message(chat_id=chat_id, text="⏳ Verification en cours...")
        def run():
            check_all(ctx.bot, chat_id)
        threading.Thread(target=run, daemon=True).start()

    elif data_cb == "list":
        data = load_data()
        if not data:
            show_menu(ctx.bot, chat_id, "Aucune reservation. Clique sur + pour en ajouter une !")
            return
        lines = []
        now = datetime.now()
        for code, info in data.items():
            emoji = {"confirmed": "✅", "not_found": "🚨", "error": "⚠️", "unknown": "❓"}.get(info.get("status", ""), "❓")
            fd = parse_date(info.get("flight_date", ""))
            delta = " — dans " + str((fd - now).days) + "j" if fd and (fd - now).days >= 0 else ""

            last_check = info.get("last_check", "jamais")
            time_ago = "jamais"
            if last_check != "jamais":
                try:
                    lc = datetime.strptime(last_check, "%d/%m/%Y %H:%M")
                    diff = int((now - lc).total_seconds() / 60)
                    if diff < 1:
                        time_ago = "a l'instant"
                    elif diff < 60:
                        time_ago = "il y a " + str(diff) + " min"
                    elif diff < 1440:
                        time_ago = "il y a " + str(diff // 60) + "h" + str(diff % 60).zfill(2)
                    else:
                        time_ago = "il y a " + str(diff // 1440) + "j"
                except:
                    time_ago = last_check

            if info.get("status") == "not_found":
                badge = "🚨 INTROUVABLE"
            elif info.get("checkin_done", False):
                badge = "✈️ CHECK-IN EFFECTUE"
            elif info.get("checkin_open", False):
                badge = "🛫 CHECK-IN OUVERT"
            elif info.get("status") == "confirmed":
                badge = "✅ CONFIRMEE"
            else:
                badge = "❓ NON VERIFIE"

            lines.append(
                emoji + " <b>" + code + "</b> — " + info["name"] + "\n"
                "    └ 📅 " + info.get("flight_date", "?") + delta + "\n"
                "    └ 🏷 " + badge + "\n"
                "    └ 🕐 Verifie " + time_ago
            )
        ctx.bot.send_message(chat_id=chat_id, parse_mode="HTML",
            text="📋 <b>" + str(len(data)) + " reservation(s)</b>\n\n" + "\n\n".join(lines))
        show_menu(ctx.bot, chat_id)

    elif data_cb == "remove":
        data = load_data()
        if not data:
            show_menu(ctx.bot, chat_id, "Aucune reservation a supprimer.")
            return
        buttons = []
        for code, info in data.items():
            buttons.append([InlineKeyboardButton(
                "🗑 " + code + " — " + info["name"] + " (" + info.get("flight_date", "?") + ")",
                callback_data="del_" + code
            )])
        buttons.append([InlineKeyboardButton("↩️ Retour", callback_data="back")])
        ctx.bot.send_message(chat_id=chat_id, text="Quelle reservation supprimer ?",
            reply_markup=InlineKeyboardMarkup(buttons))

    elif data_cb.startswith("del_"):
        code = data_cb[4:]
        data = load_data()
        if code in data:
            name = data[code]["name"]
            del data[code]
            save_data(data)
            show_menu(ctx.bot, chat_id, "🗑 <b>" + code + "</b> (" + name + ") supprime.")
        else:
            show_menu(ctx.bot, chat_id, "❌ Reservation introuvable.")

    elif data_cb == "status":
        data = load_data()
        now = datetime.now()
        ctx.bot.send_message(chat_id=chat_id, parse_mode="HTML", text=(
            "📊 <b>Statut du bot</b>\n\n"
            "• Total : <b>" + str(len(data)) + "</b> reservations\n"
            "• Confirmees : <b>" + str(sum(1 for v in data.values() if v.get("status") == "confirmed")) + "</b> ✅\n"
            "• Disparues : <b>" + str(sum(1 for v in data.values() if v.get("status") == "not_found")) + "</b> 🚨\n"
            "• Verification auto : toutes les <b>90 min</b>\n"
            "• Resume matin : <b>8h00</b> 🌅\n"
            "• Heure : " + now.strftime("%d/%m/%Y %H:%M")
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
                "✅ Code : <b>" + text.upper() + "</b>\n\n"
                "Etape 2/3 — Envoie le <b>nom de famille</b>\n"
                "Ex: <code>MARTIN</code>",
                parse_mode="HTML")

        elif step == "name":
            WAITING_ADD[chat_id]["name"] = text.upper()
            WAITING_ADD[chat_id]["step"] = "date"
            update.message.reply_text(
                "✅ Nom : <b>" + text.upper() + "</b>\n\n"
                "Etape 3/3 — Envoie la <b>date du vol</b>\n"
                "Format : <code>JJ/MM/AAAA</code>\n"
                "Ex: <code>15/05/2026</code>",
                parse_mode="HTML")

        elif step == "date":
            flight_date = parse_date(text)
            if not flight_date:
                update.message.reply_text("❌ Format incorrect. Ex: <code>15/05/2026</code>", parse_mode="HTML")
                return

            code = WAITING_ADD[chat_id]["code"]
            name = WAITING_ADD[chat_id]["name"]
            data = load_data()
            data[code] = {
                "name": name, "flight_date": text, "status": "unknown",
                "last_check": "jamais", "detail": "Pas encore verifie",
                "checkin_open_notified": False, "checkin_12h_notified": False,
                "checkin_done": False, "checkin_open": False,
                "added": datetime.now().strftime("%d/%m/%Y %H:%M")
            }
            save_data(data)
            del WAITING_ADD[chat_id]

            delta = (flight_date - datetime.now()).days
            ctx.bot.send_message(chat_id=chat_id, parse_mode="HTML", text=(
                "✅ <b>Reservation ajoutee !</b>\n\n"
                "✈️ Code : <b>" + code + "</b>\n"
                "👤 Passager : <b>" + name + "</b>\n"
                "📅 Vol le : <b>" + text + "</b> (dans " + str(delta) + " jours)\n\n"
                "⏳ Verification en cours..."
            ))

            def verify_bg(bot, code, name, chat_id):
                result = check_reservation(code, name)
                d = load_data()
                if code in d:
                    d[code]["status"] = result["status"]
                    d[code]["last_check"] = datetime.now().strftime("%d/%m/%Y %H:%M")
                    d[code]["detail"] = result["detail"]
                    d[code]["checkin_done"] = result.get("checkin_done", False)
                    d[code]["checkin_open"] = result.get("checkin_open", False)
                    save_data(d)

                if result["status"] == "confirmed":
                    status_text = "✅ <b>" + code + " confirmee !</b> — " + result.get("detail", "")
                elif result["status"] == "not_found":
                    d2 = load_data()
                    if code in d2:
                        del d2[code]
                        save_data(d2)
                    status_text = "🚨 <b>" + code + " INTROUVABLE !</b> Cette reservation n'existe pas sur Etihad. Elle a ete supprimee."
                else:
                    status_text = "⚠️ Impossible de verifier <b>" + code + "</b>, reessaie dans quelques minutes."
                # Toujours envoyer au CHAT_ID du groupe
                show_menu(bot, CHAT_ID, status_text)

            threading.Thread(target=verify_bg, args=(ctx.bot, code, name, chat_id), daemon=True).start()
    else:
        show_menu(ctx.bot, chat_id)

# ══════════════════════════════════════════
#  JOB AUTO
# ══════════════════════════════════════════

def auto_check_job(ctx):
    log.info("⏰ Verification automatique")
    data = load_data()
    now = datetime.now()
    to_delete = []
    for code, info in data.items():
        fd = parse_date(info.get("flight_date", ""))
        if fd and (now - fd).total_seconds() > 86400:
            to_delete.append((code, info["name"]))
    for code, name in to_delete:
        del data[code]
        log.info("Resa expiree supprimee : " + code)
        try:
            ctx.bot.send_message(
                chat_id=CHAT_ID,
                text="🗑 Vol termine : " + code + " / " + name + " supprime de la liste."
            )
        except:
            pass
    if to_delete:
        save_data(data)
    check_all(ctx.bot, silent=True)

# ══════════════════════════════════════════
#  DÉMARRAGE
# ══════════════════════════════════════════

def main():
    print("✈️  ETIHAD MONITOR v6 — Demarrage...")
    subprocess.run(["python", "-m", "playwright", "install", "--with-deps", "chromium"], check=True)
    print("✅ Chromium installe !")

    updater = Updater(token=BOT_TOKEN)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", cmd_start))
    dp.add_handler(CommandHandler("menu", cmd_menu))
    dp.add_handler(CallbackQueryHandler(handle_button))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

    jq = updater.job_queue
    jq.run_repeating(auto_check_job, interval=CHECK_INTERVAL_SECONDS, first=60)
    jq.run_daily(morning_summary, time=datetime.strptime("08:00", "%H:%M").time())
    jq.run_repeating(checkin_alerts, interval=3600, first=120)

    print("🟢 Bot actif !")
    print("CHAT_ID = " + str(CHAT_ID))
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
