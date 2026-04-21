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

from db import (
    init_db,
    habit_reset_stale_streaks,
    reminders_pending,
)
from agents.brain_agent import think, think_browse_result
from agents.browser_agent import execute_browse
from agents.task_agent import (
    format_tasks_for_user,
    parse_commands_from_response,
    execute_commands,
    close,
    _fmt_due,
)
from agents.memory_agent import cleanup as cleanup_history
from agents.voice_agent import transcribe, summarize_transcript
from agents.optimization_utils import (
    extract_voice_duration_from_telegram,
    should_transcribe_voice,
    should_summarize_transcript,
)
from agents.scheduler_agent import start as start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_MY_CHAT_ID = int(os.getenv("MY_CHAT_ID", "0"))
_TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")

# Блокировка: не допускаем параллельных вызовов think()
# чтобы избежать гонки в истории диалога
_think_lock = asyncio.Lock()


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
        "👋 Привіт! Я твій особистий ІІ-асистент.\n\n"
        "Що вмію:\n"
        "✅ Відповідати на запитання і вести діалог\n"
        "🎯 Вести цілі, звички, рутину — просто скажи що треба зробити\n"
        "📅 Вести події — зустрічі, дзвінки, заходи\n"
        "🔔 Напоминання на певний час\n"
        "🎤 Розпізнавати голосові повідомлення\n\n"
        "Команди:\n"
        "/tasks — показати всі задачі, звички, рутину і події\n"
        "/reminders — показати активні напоминання\n"
        "/done <id> — закрити задачу\n"
        "/start — це повідомлення"
    )


# ---------------------------------------------------------------------------
# Команда /tasks
# ---------------------------------------------------------------------------

async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    text = format_tasks_for_user()
    await update.message.reply_text(text)


# ---------------------------------------------------------------------------
# Команда /reminders
# ---------------------------------------------------------------------------

async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    reminders = reminders_pending()
    if not reminders:
        await update.message.reply_text("🔔 Активных напоминаний нет.")
        return

    lines = ["🔔 **Напоминания:**"]
    for reminder in reminders:
        due_part = f" 📅 {_fmt_due(reminder['remind_at'])}" if reminder.get("remind_at") else ""
        lines.append(f"  [{reminder['id']}] {reminder['text']}{due_part}")

    await update.message.reply_text("\n".join(lines))


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
        await update.message.reply_text(f"🎉 Задача [{task_id}] — виконано!")
    else:
        await update.message.reply_text(
            f"❌ Задача [{task_id}] не знайдена або вже закрита."
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
        async with _think_lock:
            raw_answer = await asyncio.to_thread(think, user_text)
    except Exception as e:
        typing_task.cancel()
        logger.error("think() error: %s", e, exc_info=True)
        await update.message.reply_text(f"Помилка: {e}")
        return
    finally:
        typing_task.cancel()

    try:
        clean_text, commands = parse_commands_from_response(raw_answer)

        # Розділяємо browse і звичайні команди
        browse_cmds = [c for c in commands if c.get("action") == "browse"]
        other_cmds  = [c for c in commands if c.get("action") != "browse"]

        cmd_result = ""
        if other_cmds:
            cmd_result = execute_commands(other_cmds)

        reply = clean_text or ("Зараз перевірю..." if browse_cmds else "…")
        await update.message.reply_text(reply)

        # Виконуємо browse команди
        for browse_cmd in browse_cmds:
            logger.info("handle_text: executing browse: %s", browse_cmd)
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, action=ChatAction.TYPING
            )
            browse_result = await execute_browse(browse_cmd)
            logger.info("handle_text: browse_result length=%d", len(browse_result))
            final_answer = await asyncio.to_thread(
                think_browse_result, user_text, browse_result
            )
            await update.message.reply_text(final_answer)

        if cmd_result:
            await update.message.reply_text(cmd_result)
    except Exception as e:
        logger.error("reply error: %s", e, exc_info=True)
        await update.message.reply_text(f"Помилка при відправці відповіді: {e}")


# ---------------------------------------------------------------------------
# Обработка голосовых сообщений
# ---------------------------------------------------------------------------

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return

    voice = update.message.voice
    duration = extract_voice_duration_from_telegram(voice)

    # Оптимизация: не транскрибируем очень короткие сообщения (< 2 сек)
    if not should_transcribe_voice(duration):
        await update.message.reply_text("Повідомлення занадто коротке, не вдалося розпізнати.")
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )

    # Скачиваем голосовое сообщение во временный файл
    voice_file = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name

    await voice_file.download_to_drive(tmp_path)

    try:
        user_text = transcribe(tmp_path)

        # Оптимизация: суммаризируем очень длинные транскрипты (> 500 слов)
        if should_summarize_transcript(user_text):
            logger.info("Summarizing long voice transcript (%d words)", len(user_text.split()))
            user_text = await asyncio.to_thread(summarize_transcript, user_text)

    except Exception as e:
        logger.error("Transcription error: %s", e)
        await update.message.reply_text("Не вдалося розпізнати голосове повідомлення.")
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
        async with _think_lock:
            raw_answer = await asyncio.to_thread(think, user_text)
    finally:
        typing_task2.cancel()

    clean_text, commands = parse_commands_from_response(raw_answer)

    # Розділяємо browse і звичайні команди
    browse_cmds = [c for c in commands if c.get("action") == "browse"]
    other_cmds  = [c for c in commands if c.get("action") != "browse"]

    cmd_result = ""
    if other_cmds:
        cmd_result = execute_commands(other_cmds)

    reply = clean_text or ("Зараз перевірю..." if browse_cmds else "…")
    await update.message.reply_text(reply)

    # Виконуємо browse команди
    for browse_cmd in browse_cmds:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action=ChatAction.TYPING
        )
        browse_result = await execute_browse(browse_cmd)
        final_answer = await asyncio.to_thread(
            think_browse_result, user_text, browse_result
        )
        await update.message.reply_text(final_answer)

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

    # Очищаем историю старше 30 дней (для экономии памяти БД)
    try:
        cleanup_history()
        logger.info("История очищена (удалены сообщения старше 30 дней).")
    except Exception as e:
        logger.warning("Ошибка при очистке истории: %s", e)

    # Сбрасываем старые полоски привычек (>24 часов)
    try:
        habit_reset_stale_streaks()
        logger.info("Полоски привычек сброшены (старше 24 часов).")
    except Exception as e:
        logger.warning("Ошибка при сбросе полосок: %s", e)

    app = Application.builder().token(_TELEGRAM_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("reminders", cmd_reminders))
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
