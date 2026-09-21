import logging
import os
import sqlite3
from pathlib import Path

from telegram import Update
from telegram.constants import ChatType
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN_1 = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
GROUP_ID_1 = int(os.getenv("TEACHER_GROUP_ID", "-1003839598400"))
DB_PATH_1 = Path(os.getenv("DB_PATH", "/var/data/students.db"))

TOKEN_2 = os.getenv("TELEGRAM_BOT_TOKEN_2")
GROUP_ID_2 = int(os.getenv("TEACHER_GROUP_ID_2", "-5516510250"))
DB_PATH_2 = Path(os.getenv("DB_PATH_2", "/var/data/students_bot2.db"))


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS students (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            topic_id INTEGER
        )
        """
    )
    connection.commit()
    return connection


def cfg(context):
    return context.application.bot_data


def forget_student(db, user_id: int) -> None:
    """Drop a student's stored topic id so the next submission creates a fresh one."""
    db.execute("DELETE FROM students WHERE user_id = ?", (user_id,))
    db.commit()


async def create_topic_for(user, bot, group_id: int, db) -> int:
    """Create a fresh forum topic for the given user and persist it."""
    last_name = (user.last_name or "").strip()
    display = f"{user.first_name} {last_name}".strip()
    if user.username:
        display = f"{display} (@{user.username})"

    topic = await bot.create_forum_topic(chat_id=group_id, name=display or "Schüler")
    topic_id = topic.message_thread_id

    db.execute(
        "INSERT OR REPLACE INTO students (user_id, name, topic_id) VALUES (?, ?, ?)",
        (user.id, user.first_name, topic_id),
    )
    db.commit()
    return topic_id


async def get_or_create_topic(user, bot, group_id: int, db) -> int:
    """Return the forum topic id for this student, creating one if needed."""
    cur = db.execute(
        "SELECT topic_id FROM students WHERE user_id = ?",
        (user.id,),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    return await create_topic_for(user, bot, group_id, db)


def is_missing_topic_error(err: BadRequest) -> bool:
    """Detect Telegram errors that mean the stored topic no longer exists."""
    msg = (err.message or "").lower()
    return (
        "message thread not found" in msg
        or "topic_deleted" in msg
        or "topic deleted" in msg
        or "thread_not_found" in msg
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "🇩🇪 Hallo! Schick mir deine Hausaufgabe (Text, Foto, Datei). "
        "Sie wird automatisch an deine Lehrkraft weitergeleitet.\n\n"
        "Befehle:\n"
        "/status – prüfen, ob du schon ein Abgabe-Topic hast\n"
        "/reset – neues Abgabe-Topic anfordern (z. B. wenn dein altes gelöscht wurde)\n\n"
        "————————————————\n\n"
        "🇷🇺 Привет! Отправь мне своё домашнее задание (текст, фото, файл). "
        "Оно будет автоматически переслано твоему учителю.\n\n"
        "Команды:\n"
        "/status – проверить, есть ли у тебя уже тема для сдачи заданий\n"
        "/reset – запросить новую тему (например, если старая была удалена)"
    )


async def students_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = cfg(context)["db"]
    group_id = cfg(context)["group_id"]
    """List every registered student → topic mapping. Only works in the teachers' group."""
    if not update.message or not update.effective_chat:
        return
    if update.effective_chat.id != group_id:
        await update.message.reply_text(
            "Dieser Befehl funktioniert nur in der Lehrer-Gruppe."
        )
        return
    cur = db.execute(
        "SELECT user_id, name, topic_id FROM students ORDER BY name COLLATE NOCASE"
    )
    rows = cur.fetchall()
    if not rows:
        await update.message.reply_text("Noch keine Schüler registriert.")
        return
    lines = ["Registrierte Schüler:", ""]
    for user_id, name, topic_id in rows:
        lines.append(f"• {name or 'Unbenannt'} — Topic #{topic_id} (user {user_id})")
    text = "\n".join(lines)
    # Telegram caps messages around 4096 chars; chunk if needed.
    for i in range(0, len(text), 3500):
        await update.message.reply_text(text[i:i + 3500])

async def link_here_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = cfg(context)["db"]
    group_id = cfg(context)["group_id"]
    """Link a student user_id to the current forum topic."""
    if not update.message or not update.effective_chat:
        return

    # Nur in der Lehrer-Gruppe erlauben
    if update.effective_chat.id != group_id:
        await update.message.reply_text(
            "Dieser Befehl funktioniert nur in der Lehrer-Gruppe."
        )
        return

    # Der Befehl muss in einem Thema/Topic geschrieben werden
    topic_id = update.message.message_thread_id
    if not topic_id:
        await update.message.reply_text(
            "Bitte diesen Befehl direkt im vorhandenen Schüler-Thema ausführen."
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Bitte so nutzen:\n"
            "/link_here USER_ID Name\n\n"
            "Beispiel:\n"
            "/link_here 123456789 Max"
        )
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Die USER_ID muss eine Zahl sein.")
        return

    name = " ".join(context.args[1:]).strip() or "Unbenannt"

    db.execute(
        "INSERT OR REPLACE INTO students (user_id, name, topic_id) VALUES (?, ?, ?)",
        (user_id, name, topic_id),
    )
    db.commit()

    await update.message.reply_text(
        f"✅ Verknüpft:\n"
        f"{name} — user {user_id} → Topic #{topic_id}"
    )

async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    user = update.effective_user

    await update.message.reply_text(
        f"Deine Telegram User-ID ist:\n{user.id}"
    )

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = cfg(context)["db"]
    group_id = cfg(context)["group_id"]
    """Drop the student's stored topic so a fresh one is created on the next message."""
    if not update.message or not update.effective_user:
        return
    if update.effective_chat and update.effective_chat.type != ChatType.PRIVATE:
        return
    user = update.effective_user
    cur = db.execute(
        "SELECT topic_id FROM students WHERE user_id = ?",
        (user.id,),
    )
    row = cur.fetchone()
    if not row:
        await update.message.reply_text(
            "🇩🇪 ℹ️ Du hattest noch kein Abgabe-Topic. Schick einfach deine Hausaufgabe — ein neues wird automatisch erstellt.\n"
            "🇷🇺 ℹ️ У тебя ещё не было темы. Просто отправь домашнее задание — новая тема создастся автоматически."
        )
        return
    forget_student(db, user.id)
    await update.message.reply_text(
        "🇩🇪 🔄 Dein Abgabe-Topic wurde zurückgesetzt. Schick deine nächste Hausaufgabe und ich erstelle ein neues Topic.\n"
        "🇷🇺 🔄 Твоя тема сброшена. Отправь следующее домашнее задание — я создам новую тему."
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = cfg(context)["db"]
    group_id = cfg(context)["group_id"]
    if not update.message:
        return
    user = update.effective_user
    cur = db.execute(
        "SELECT topic_id FROM students WHERE user_id = ?",
        (user.id,),
    )
    row = cur.fetchone()
    if not row:
        await update.message.reply_text(
            "🇩🇪 ❌ Noch keine Abgaben vorhanden.\n"
            "🇷🇺 ❌ Пока ещё нет сданных заданий."
        )
    else:
        await update.message.reply_text(
            "🇩🇪 📚 Du hast bereits ein Abgabe-Topic.\n"
            "🇷🇺 📚 У тебя уже есть тема для сдачи заданий."
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = cfg(context)["db"]
    group_id = cfg(context)["group_id"]
    """Forward every private DM into the student's topic in the teacher group."""
    if not update.message or not update.effective_user:
        return

    # Only process private chats — ignore the teacher group / channels.
    if update.effective_chat.type != ChatType.PRIVATE:
        return

    user = update.effective_user

    try:
        topic_id = await get_or_create_topic(user, context.bot, group_id, db)
    except TelegramError:
        logger.exception("Failed to create or load topic for user %s", user.id)
        await update.message.reply_text(
            "🇩🇪 ⚠️ Konnte kein Abgabe-Topic erstellen. Bitte später erneut versuchen.\n"
            "🇷🇺 ⚠️ Не удалось создать тему для сдачи заданий. Пожалуйста, попробуй позже."
        )
        return

    forwarded = False
    for attempt in (1, 2):
        try:
            await context.bot.forward_message(
                chat_id=group_id,
                from_chat_id=update.effective_chat.id,
                message_id=update.message.message_id,
                message_thread_id=topic_id,
            )
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

    db1 = open_db(DB_PATH_1)
    db2 = open_db(DB_PATH_2)

    app1 = build_app(TOKEN_1, GROUP_ID_1, db1, "Bot 1")
    app2 = build_app(TOKEN_2, GROUP_ID_2, db2, "Bot 2")
    apps = [app1, app2]

    logger.info("Bot 1 database: %s | teacher group: %s", DB_PATH_1.resolve(), GROUP_ID_1)
    logger.info("Bot 2 database: %s | teacher group: %s", DB_PATH_2.resolve(), GROUP_ID_2)

    try:
        for app in apps:
            await app.initialize()
            await app.start()
            await app.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
            )

        logger.info("Both Telegram bots are running.")
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
            retry_delay = min(retry_delay * 2, max_delay)


if __name__ == "__main__":
    main()
