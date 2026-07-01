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

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("TEACHER_GROUP_ID", "-1003839598400"))

DB_PATH = Path(os.getenv("DB_PATH", "/var/data/students.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute(
    """
    CREATE TABLE IF NOT EXISTS students (
        user_id INTEGER PRIMARY KEY,
        name TEXT,
        topic_id INTEGER
    )
    """
)
db.commit()


def forget_student(user_id: int) -> None:
    """Drop a student's stored topic id so the next submission creates a fresh one."""
    db.execute("DELETE FROM students WHERE user_id = ?", (user_id,))
    db.commit()


async def create_topic_for(user, bot) -> int:
    """Create a fresh forum topic for the given user and persist it."""
    last_name = (user.last_name or "").strip()
    display = f"{user.first_name} {last_name}".strip()
    if user.username:
        display = f"{display} (@{user.username})"

    topic = await bot.create_forum_topic(chat_id=GROUP_ID, name=display or "Schüler")
    topic_id = topic.message_thread_id

    db.execute(
        "INSERT OR REPLACE INTO students (user_id, name, topic_id) VALUES (?, ?, ?)",
        (user.id, user.first_name, topic_id),
    )
    db.commit()
    return topic_id


async def get_or_create_topic(user, bot) -> int:
    """Return the forum topic id for this student, creating one if needed."""
    cur = db.execute(
        "SELECT topic_id FROM students WHERE user_id = ?",
        (user.id,),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    return await create_topic_for(user, bot)


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
    """List every registered student → topic mapping. Only works in the teachers' group."""
    if not update.message or not update.effective_chat:
        return
    if update.effective_chat.id != GROUP_ID:
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
    """Link a student user_id to the current forum topic."""
    if not update.message or not update.effective_chat:
        return

    # Nur in der Lehrer-Gruppe erlauben
    if update.effective_chat.id != GROUP_ID:
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
    forget_student(user.id)
    await update.message.reply_text(
        "🇩🇪 🔄 Dein Abgabe-Topic wurde zurückgesetzt. Schick deine nächste Hausaufgabe und ich erstelle ein neues Topic.\n"
        "🇷🇺 🔄 Твоя тема сброшена. Отправь следующее домашнее задание — я создам новую тему."
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    """Forward every private DM into the student's topic in the teacher group."""
    if not update.message or not update.effective_user:
        return

    # Only process private chats — ignore the teacher group / channels.
    if update.effective_chat.type != ChatType.PRIVATE:
        return

    user = update.effective_user

    try:
        topic_id = await get_or_create_topic(user, context.bot)
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
                chat_id=GROUP_ID,
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
                forget_student(user.id)
                try:
                    topic_id = await create_topic_for(user, context.bot)
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


def build_app(token: str) -> ApplicationBuilder:
    app = ApplicationBuilder().token(token).build()
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


def main() -> None:
    if not TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is not set. Add it as a Replit secret and restart."
        )

    logger.info("Database path: %s", DB_PATH.resolve())
    logger.info("Starting Homework Submission Bot. Teacher group: %s", GROUP_ID)

    retry_delay = 5
    max_delay = 60

    while True:
        try:
            app = build_app(TOKEN)
            app.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
            )
            retry_delay = 5
        except Exception:
            logger.exception(
                "Bot crashed — restarting in %s seconds.", retry_delay
            )
            import time
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)


if __name__ == "__main__":
    main()
