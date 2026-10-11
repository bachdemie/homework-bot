            forwarded = True
            break
        except BadRequest as err:
            if attempt == 1 and is_missing_topic_error(err):
                logger.info(
                    "Topic %s for user %s is gone — recreating.", topic_id, user.id
                )
                forget_student(db, user.id)
                try:
                    topic_id = await create_topic_for(user, context.bot, group_id, db)
                except TelegramError:
                    logger.exception(
                        "Failed to recreate topic for user %s", user.id
                    )
                    await update.message.reply_text(
                        "🇩🇪 ⚠️ Konnte kein neues Abgabe-Topic erstellen. Bitte später erneut versuchen.\n"
                        "🇷🇺 ⚠️ Не удалось создать новую тему. Пожалуйста, попробуй позже."
                    )
                    return
                continue
            logger.exception("Failed to forward message from user %s", user.id)
            break
        except TelegramError:
            logger.exception("Failed to forward message from user %s", user.id)
            break

    if not forwarded:
        await update.message.reply_text(
            "🇩🇪 ⚠️ Konnte deine Abgabe nicht weiterleiten. Bitte erneut versuchen.\n"
            "🇷🇺 ⚠️ Не удалось переслать твою работу. Пожалуйста, попробуй ещё раз."
        )
        return

    await update.message.reply_text(
        "🇩🇪 ✅ Hausaufgabe abgegeben.\n"
        "🇷🇺 ✅ Домашнее задание сдано."
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error", exc_info=context.error)


def build_app(token: str, group_id: int, db, label: str):
    app = ApplicationBuilder().token(token).build()
    app.bot_data["group_id"] = group_id
    app.bot_data["db"] = db
    app.bot_data["label"] = label
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("students", students_cmd))
    app.add_handler(CommandHandler("link_here", link_here_cmd))
    app.add_handler(CommandHandler("myid", myid_cmd))
    app.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_message)
    )
    app.add_error_handler(on_error)
    return app


async def run_bots() -> None:
    if not TOKEN_1:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set.")
    if not TOKEN_2:
        raise SystemExit("TELEGRAM_BOT_TOKEN_2 is not set.")
    if not TOKEN_3:
        raise SystemExit("TELEGRAM_BOT_TOKEN_3 is not set.")
    if not GROUP_ID_3:
        raise SystemExit("TEACHER_GROUP_ID_3 is not set.")

    db1 = open_db(DB_PATH_1)
    db2 = open_db(DB_PATH_2)
    db3 = open_db(DB_PATH_3)

    app1 = build_app(TOKEN_1, GROUP_ID_1, db1, "Bot 1")
    app2 = build_app(TOKEN_2, GROUP_ID_2, db2, "Bot 2")
    app3 = build_app(TOKEN_3, GROUP_ID_3, db3, "Bot 3")
    apps = [app1, app2, app3]

    logger.info("Bot 1 database: %s | teacher group: %s", DB_PATH_1.resolve(), GROUP_ID_1)
    logger.info("Bot 2 database: %s | teacher group: %s", DB_PATH_2.resolve(), GROUP_ID_2)
    logger.info("Bot 3 database: %s | teacher group: %s", DB_PATH_3.resolve(), GROUP_ID_3)

    try:
        for app in apps:
            await app.initialize()
            await app.start()
            await app.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
            )

        logger.info("All three Telegram bots are running.")
        await __import__("asyncio").Event().wait()
    finally:
        for app in reversed(apps):
            try:
                if app.updater and app.updater.running:
                    await app.updater.stop()
                if app.running:
                    await app.stop()
                await app.shutdown()
            except Exception:
                logger.exception("Error while shutting down %s", app.bot_data.get("label"))

        db1.close()
        db2.close()
        db3.close()


def main() -> None:
    import asyncio

    retry_delay = 5
    max_delay = 60

    while True:
        try:
            asyncio.run(run_bots())
            retry_delay = 5
        except KeyboardInterrupt:
            logger.info("Bot worker stopped.")
            break
        except Exception:
            logger.exception("Bot worker crashed — restarting in %s seconds.", retry_delay)
            import time
            time.sleep(retry_delay)
