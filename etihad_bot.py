send_message(chat_id=CHAT_ID, text=f"Verification de {len(data)} reservation(s)... {now}")
    for code, info in data.items():
        result = check_reservation(code, info["name"])
        prev = info.get("status", "unknown")
        new  = result["status"]
        data[code]["status"]     = new
        data[code]["last_check"] = now
        data[code]["detail"]     = result["detail"]
        if prev == "confirmed" and new == "not_found":
            bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text=(
                f"ALERTE RESERVATION DISPARUE !\n\nCode : <b>{code}</b>\nPassager : <b>{info['name']}</b>\n\nhttps://www.etihad.com/fr-fr/manage"
            ))
        elif prev == "not_found" and new == "confirmed":
            bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text=(
                f"Reservation retrouvee !\n<b>{code}</b> - {info['name']}"
            ))
        if result["checkin_open"] and not info.get("checkin_notified", False):
            bot.send_message(chat_id=CHAT_ID, parse_mode="HTML", text=(
                f"CHECK-IN OUVERT !\nCode : <b>{code}</b>\n{info['name']}\nhttps://www.etihad.com/fr-fr/manage/check-in"
            ))
            data[code]["checkin_notified"] = True
    save_data(data)
    if not silent:
        lines = []
        for code, info in data.items():
            emoji = {"confirmed":"OK","not_found":"ALERTE","error":"ERREUR"}.get(info["status"],"?")
            lines.append(f"{emoji} {code} - {info['name']}: {info['detail']}")
        bot.send_message(chat_id=CHAT_ID, text="Rapport:\n\n" + "\n\n".join(lines))

def cmd_start(update, ctx):
    update.message.reply_text(
        "Etihad Monitor - Bonjour!\n\nCommandes:\n/add CODE NOM\n/remove CODE\n/check\n/list\n/status"
    )

def cmd_add(update, ctx):
    args = ctx.args
    if len(args) < 2:
        update.message.reply_text("Usage : /add CODE NOM")
        return
    code = args[0].upper()
    name = " ".join(args[1:]).upper()
    data = load_data()
    if code in data:
        update.message.reply_text(f"{code} est deja dans la liste.")
        return
    data[code] = {"name": name, "status": "unknown", "last_check": "jamais", "detail": "Pas encore verifie", "checkin_notified": False}
    save_data(data)
    update.message.reply_text(f"Ajoute ! {code} - {name}\nUtilise /check pour verifier.")

def cmd_remove(update, ctx):
    if not ctx.args:
        update.message.reply_text("Usage : /remove CODE")
        return
    code = ctx.args[0].upper()
    data = load_data()
    if code not in data:
        update.message.reply_text(f"{code} introuvable.")
        return
    name = data[code]["name"]
    del data[code]
    save_data(data)
    update.message.reply_text(f"{code} ({name}) supprime.")

def cmd_check(update, ctx):
    update.message.reply_text("Verification en cours...")
    do_check_all(ctx.bot)

def cmd_list(update, ctx):
    data = load_data()
    if not data:
        update.message.reply_text("Aucune reservation. Utilise /add CODE NOM.")
        return
    lines = []
    for code, info in data.items():
        emoji = {"confirmed":"OK","not_found":"ALERTE","error":"ERREUR","unknown":"?"}.get(info["status"],"?")
        lines.append(f"{emoji} {code} - {info['name']} (check: {info['last_check']})")
    update.message.reply_text(f"{len(data)} reservation(s):\n\n" + "\n\n".join(lines))

def cmd_status(update, ctx):
    data = load_data()
    update.message.reply_text(
        f"Statut du bot\n\nReservations: {len(data)}\nConfirmees: {sum(1 for v in data.values() if v['status']=='confirmed')}\nDisparues: {sum(1 for v in data.values() if v['status']=='not_found')}\nVerification: toutes les 2h\nHeure: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )

def auto_check(ctx):
    log.info("Verification automatique")
    do_check_all(ctx.bot, silent=True)

def main():
    print("ETIHAD MONITOR - Demarrage...")
    updater = Updater(token=BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start",  cmd_start))
    dp.add_handler(CommandHandler("add",    cmd_add))
    dp.add_handler(CommandHandler("remove", cmd_remove))
    dp.add_handler(CommandHandler("check",  cmd_check))
    dp.add_handler(CommandHandler("list",   cmd_list))
    dp.add_handler(CommandHandler("status", cmd_status))
    updater.job_queue.run_repeating(auto_check, interval=CHECK_INTERVAL, first=60)
    print("Bot actif!")
    updater.start_polling()
    updater.idle()

if name == "__main__":
    main()
