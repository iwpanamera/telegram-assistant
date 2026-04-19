import os
import sys
import logging
import tempfile
import asyncio

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Добавляем корень проекта в sys.path, чтобы импорты из db / agents работали
sys.path.insert(0, os.path.dirname(__file__))

load_dotenv()

from db import init_db
from agents.brain_agent import think
from agents.task_agent import (
    format_tasks_for_user,
    parse_commands_from_response,
    execute_commands,
    close,
)
from agents.voice_agent import transcribe
from agents.scheduler_agent import start as start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_MY_CHAT_ID = int(os.getenv("MY_CHAT_ID", "0"))
_TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")


# ---------------------------------------------------------------------------
# Охранник: реагировать только на сообщения от владельца бота
# ---------------------------------------------------------------------------

def _is_owner(update: Update) -> bool:
    return update.effective_chat.id == _MY_CHAT_ID


# ---------------------------------------------------------------------------
# Команда /start
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    await update.message.reply_text(
        "👋 Привет\\! Я твой личный ИИ-ассистент\\.\n\n"
        "Что умею:\n"
        "• Отвечать на любые вопросы и вести диалог\n"
        "• Вести список задач — просто скажи что нужно сделать\n"
        "• Распознавать голосовые сообщения 🎙\n\n"
        "Команды:\n"
        "/tasks — показать открытые задачи\n"
        "/done <id> — закрыть задачу\n"
        "/start — это сообщение",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ---------------------------------------------------------------------------
# Команда /tasks
# ---------------------------------------------------------------------------

async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    text = format_tasks_for_user()
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# Команда /done <id>
# ---------------------------------------------------------------------------

async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Использование: /done <id задачи>")
        return
    task_id = int(args[0])
    ok = close(task_id)
    if ok:
        await update.message.reply_text(f"✅ Задача [{task_id}] закрыта.")
    else:
        await update.message.reply_text(
            f"⚠️ Задача [{task_id}] не найдена или уже закрыта."
        )


# ---------------------------------------------------------------------------
# Обработка текстовых сообщений
# ---------------------------------------------------------------------------

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return

    user_text = update.message.text.strip()
    if not user_text:
        return

    # Показываем "печатает" пока ждём ответа от Claude
    async def keep_typing():
        while True:
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, action=ChatAction.TYPING
            )
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(keep_typing())
    try:
        raw_answer = await asyncio.to_thread(think, user_text)
    except Exception as e:
        typing_task.cancel()
        logger.error("think() error: %s", e, exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {e}")
        return
    finally:
        typing_task.cancel()

    try:
        clean_text, commands = parse_commands_from_response(raw_answer)
        cmd_result = ""
        if commands:
            cmd_result = execute_commands(commands)
        reply = clean_text or "…"
        await update.message.reply_text(reply)
        if cmd_result:
            await update.message.reply_text(cmd_result)
    except Exception as e:
        logger.error("reply error: %s", e, exc_info=True)
        await update.message.reply_text(f"❌ Ошибка при отправке ответа: {e}")


# ---------------------------------------------------------------------------
# Обработка голосовых сообщений
# ---------------------------------------------------------------------------

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )

    # Скачиваем голосовое сообщение во временный файл
    voice = update.message.voice
    voice_file = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name

    await voice_file.download_to_drive(tmp_path)

    try:
        user_text = transcribe(tmp_path)
    except Exception as e:
        logger.error("Transcription error: %s", e)
        await update.message.reply_text("❌ Не удалось распознать голосовое сообщение.")
        return
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    # Дальше — та же логика, что и handle_text
    async def keep_typing_voice():
        while True:
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, action=ChatAction.TYPING
            )
            await asyncio.sleep(4)

    typing_task2 = asyncio.create_task(keep_typing_voice())
    try:
        raw_answer = await asyncio.to_thread(think, user_text)
    finally:
        typing_task2.cancel()

    clean_text, commands = parse_commands_from_response(raw_answer)

    cmd_result = ""
    if commands:
        cmd_result = execute_commands(commands)

    reply = clean_text or "…"
    await update.message.reply_text(reply)

    if cmd_result:
        await update.message.reply_text(cmd_result)


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main():
    if not _TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN не задан в .env")
    if _MY_CHAT_ID == 0:
        raise RuntimeError("MY_CHAT_ID не задан в .env")

    init_db()
    logger.info("База данных инициализирована.")

    app = Application.builder().token(_TELEGRAM_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("done", cmd_done))

    # Сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    # Планировщик
    start_scheduler(app.bot)
    logger.info("Планировщик запущен.")

    logger.info("Бот запущен. Ожидаю сообщений...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
