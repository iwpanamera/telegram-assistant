import os
import sys
import time
import signal
import logging
import tempfile
import asyncio
from datetime import datetime
import pytz

# Синхронизація часового поясу на сервері (Railway використовує UTC)
os.environ['TZ'] = 'Europe/Kyiv'
try:
    time.tzset()  # застосувати TZ до libc (Unix only)
except AttributeError:
    pass  # Windows

# Перевіряємо часовий пояс при старті
_tz = pytz.timezone('Europe/Kyiv')

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
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
    reminders_active,
    reminder_mark_done,
)
from agents.brain_agent import think, think_browse_result
from agents.browser_agent import execute_browse
from agents.task_agent import (
    format_tasks_for_user,
    execute_commands,
    get_tasks,
    close,
    _fmt_due,
    _CATEGORY_EMOJI,
)
from agents.memory_agent import cleanup as cleanup_history
from agents.voice_agent import transcribe, summarize_transcript
from agents.optimization_utils import (
    extract_voice_duration_from_telegram,
    should_transcribe_voice,
    should_summarize_transcript,
    cache_set,
)
from agents.scheduler_agent import start as start_scheduler
from agents.metrics import log_stats_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# DEBUG: логуємо часовий пояс при старті
_utc_now = datetime.now(pytz.UTC)
_kyiv_now = datetime.now(_tz)
_offset_hours = (_kyiv_now - _utc_now).total_seconds() / 3600
logger.info(f"🕐 TIMEZONE CHECK: UTC={_utc_now.strftime('%H:%M')}, Kyiv={_kyiv_now.strftime('%H:%M')}, Offset={_offset_hours}h")

_MY_CHAT_ID = int(os.getenv("MY_CHAT_ID", "0"))
_TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")

# Блокировка: не допускаем параллельных вызовов think()
_think_lock = asyncio.Lock()

# Debouncing: буферизуємо вхідні повідомлення від юзера,
# обробляємо через N секунд після останнього
_DEBOUNCE_SECONDS = 2.5
_pending_texts: dict[int, list[str]] = {}
_pending_timers: dict[int, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# Охранник: реагировать только на сообщения от владельца бота
# ---------------------------------------------------------------------------

def _is_owner(update: Update) -> bool:
    return update.effective_chat.id == _MY_CHAT_ID


# ---------------------------------------------------------------------------
# Спільна логіка: обробити текст → Claude → виконати tool calls → відповісти
# ---------------------------------------------------------------------------

async def _process_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_text: str,
):
    async def keep_typing():
        while True:
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, action=ChatAction.TYPING
            )
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(keep_typing())
    try:
        async with _think_lock:
            clean_text, tool_uses = await asyncio.to_thread(think, user_text)
    except Exception as e:
        logger.error("think() error: %s", e, exc_info=True)
        typing_task.cancel()
        await update.message.reply_text(f"Помилка: {e}")
        return
    finally:
        typing_task.cancel()

    try:
        browse_cmds = [c for c in tool_uses if c.get("action") == "browse"]
        other_cmds  = [c for c in tool_uses if c.get("action") != "browse"]

        cmd_result = ""
        if other_cmds:
            cmd_result = execute_commands(other_cmds)

        reply = clean_text or ("Зараз перевірю..." if browse_cmds else "…")
        await update.message.reply_text(reply)

        for browse_cmd in browse_cmds:
            logger.info("browse: %s", browse_cmd)
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
    except Exception as e:
        logger.error("reply error: %s", e, exc_info=True)
        await update.message.reply_text(f"Помилка: {e}")


# ---------------------------------------------------------------------------
# Inline-меню
# ---------------------------------------------------------------------------

_SECTION_LABELS = {
    "goals":     "🎯 Цілі",
    "routine":   "🔄 Рутина",
    "other":     "📋 Прочее",
    "reminders": "🔔 Нагадування",
    "events":    "📅 Зустрічі",
    "habits":    "💪 Звички",
}

_SECTION_PRIORITY = {
    "goals":   "goal",
    "routine": "routine",
    "other":   "other",
    "habits":  "habit",
}


def _build_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎯 Цілі",        callback_data="menu:goals"),
            InlineKeyboardButton("🔄 Рутина",      callback_data="menu:routine"),
            InlineKeyboardButton("📋 Прочее",      callback_data="menu:other"),
        ],
        [
            InlineKeyboardButton("🔔 Нагадування", callback_data="menu:reminders"),
            InlineKeyboardButton("📅 Зустрічі",    callback_data="menu:events"),
            InlineKeyboardButton("💪 Звички",      callback_data="menu:habits"),
        ],
    ])


def _build_section_content(section_key: str) -> tuple[str, InlineKeyboardMarkup]:
    """Повернути текст і клавіатуру для конкретного розділу меню."""
    label = _SECTION_LABELS[section_key]
    text_lines = [label, ""]
    buttons = []

    if section_key == "reminders":
        items = reminders_active()
        if not items:
            text_lines.append("Немає активних нагадувань 🎉")
        else:
            for item in items:
                due_str = f"  ⏰ {_fmt_due(item['remind_at'])}" if item.get("remind_at") else ""
                text_lines.append(f"[{item['id']}] {item['text']}{due_str}")
                buttons.append([InlineKeyboardButton(
                    f"✅ {item['text'][:30]}",
                    callback_data=f"done:r:{item['id']}",
                )])
    else:
        records = get_tasks(use_cache=False)

        if section_key == "events":
            items = [r for r in records if r.get("type") == "event"]
        else:
            priority = _SECTION_PRIORITY[section_key]
            items = [r for r in records if r.get("type") == "task" and r.get("priority") == priority]

        if not items:
            text_lines.append("Нічого немає 🎉")
        else:
            for item in items:
                due_str = f"  ⏰ {_fmt_due(item['due'])}" if item.get("due") else ""
                streak_str = ""
                if item.get("priority") == "habit":
                    streak = item.get("streak", 0) or 0
                    if streak > 0:
                        streak_str = " " + "🟩" * min(streak, 7)
                        if streak > 7:
                            streak_str += f" +{streak - 7}"
                cat_emoji = _CATEGORY_EMOJI.get(item.get("category", "other"), "📌")
                text_lines.append(f"[{item['id']}] {cat_emoji} {item['text']}{streak_str}{due_str}")
                buttons.append([InlineKeyboardButton(
                    f"✅ {item['text'][:30]}",
                    callback_data=f"done:t:{item['id']}:{section_key}",
                )])

    buttons.append([InlineKeyboardButton("← Назад", callback_data="menu:back")])
    return "\n".join(text_lines), InlineKeyboardMarkup(buttons)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    await update.message.reply_text(
        "📋 Мій план — обери розділ:",
        reply_markup=_build_menu_keyboard(),
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != _MY_CHAT_ID:
        await query.answer()
        return

    await query.answer()
    data = query.data

    # Повернутись до головного меню
    if data == "menu:back":
        await query.edit_message_text(
            "📋 Мій план — обери розділ:",
            reply_markup=_build_menu_keyboard(),
        )
        return

    # Відкрити розділ
    if data.startswith("menu:"):
        section_key = data[5:]
        if section_key in _SECTION_LABELS:
            text, markup = _build_section_content(section_key)
            await query.edit_message_text(text, reply_markup=markup)
        return

    # Виконати задачу / подію — done:t:{id}:{section_key}
    if data.startswith("done:t:"):
        parts = data.split(":")
        try:
            task_id = int(parts[2])
            section_key = parts[3] if len(parts) > 3 else None
        except (ValueError, IndexError):
            return
        close(task_id)
        cache_set("tasks_ts", None)
        if section_key and section_key in _SECTION_LABELS:
            text, markup = _build_section_content(section_key)
            await query.edit_message_text(text, reply_markup=markup)
        return

    # Виконати нагадування — done:r:{id}
    if data.startswith("done:r:"):
        try:
            reminder_id = int(data.split(":")[2])
        except (ValueError, IndexError):
            return
        reminder_mark_done(reminder_id)
        text, markup = _build_section_content("reminders")
        await query.edit_message_text(text, reply_markup=markup)
        return


# ---------------------------------------------------------------------------
# Команди
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
        "/menu — відкрити інтерактивне меню (цілі, рутина, звички…)\n"
        "/tasks — показати всі задачі у вигляді тексту\n"
        "/reminders — показати активні напоминання\n"
        "/done <id> — закрити задачу\n"
        "/start — це повідомлення"
    )


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    text = format_tasks_for_user()
    await update.message.reply_text(text)


async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    reminders = reminders_pending()
    if not reminders:
        await update.message.reply_text("🔔 Активних напоминань немає.")
        return

    lines = ["🔔 **Напоминання:**"]
    for reminder in reminders:
        due_part = f" 📅 {_fmt_due(reminder['remind_at'])}" if reminder.get("remind_at") else ""
        lines.append(f"  [{reminder['id']}] {reminder['text']}{due_part}")

    await update.message.reply_text("\n".join(lines))


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Використання: /done <id задачі>")
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
# Debounced text handler
# ---------------------------------------------------------------------------

async def _debounced_dispatch(chat_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Дочекатись _DEBOUNCE_SECONDS, потім зібрати всі накопичені
    повідомлення від цього юзера і обробити як одне.
    """
    try:
        await asyncio.sleep(_DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        return  # прийшло нове повідомлення — цей таймер скасовано

    texts = _pending_texts.pop(chat_id, [])
    _pending_timers.pop(chat_id, None)
    if not texts:
        return

    combined = "\n".join(texts) if len(texts) > 1 else texts[0]
    logger.info("debounce dispatch: %d messages → %d chars",
                len(texts), len(combined))
    await _process_text(update, context, combined)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    user_text = (update.message.text or "").strip()
    if not user_text:
        return

    chat_id = update.effective_chat.id
    _pending_texts.setdefault(chat_id, []).append(user_text)

    # Скасувати попередній таймер (якщо юзер пише швидко) і запустити новий
    prev = _pending_timers.get(chat_id)
    if prev and not prev.done():
        prev.cancel()

    _pending_timers[chat_id] = asyncio.create_task(
        _debounced_dispatch(chat_id, update, context)
    )


# ---------------------------------------------------------------------------
# Голосові повідомлення — обробляються одразу (зазвичай одне за раз)
# ---------------------------------------------------------------------------

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return

    voice = update.message.voice
    duration = extract_voice_duration_from_telegram(voice)

    if not should_transcribe_voice(duration):
        await update.message.reply_text("Повідомлення занадто коротке, не вдалося розпізнати.")
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )

    voice_file = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name

    await voice_file.download_to_drive(tmp_path)

    try:
        user_text = await asyncio.to_thread(transcribe, tmp_path)

        if should_summarize_transcript(user_text):
            logger.info("Summarizing long transcript (%d words)", len(user_text.split()))
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

    await _process_text(update, context, user_text)


# ---------------------------------------------------------------------------
# Heartbeat — кожні 5 хв лог, щоб бачити що процес живий
# ---------------------------------------------------------------------------

async def _heartbeat_loop():
    while True:
        await asyncio.sleep(300)  # 5 хв
        logger.info("💓 heartbeat — бот живий")


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

def _install_shutdown_handler():
    def _on_term(signum, frame):
        logger.info("⚠️ Отримано сигнал %s, зберігаю метрики...", signum)
        try:
            log_stats_summary()
        except Exception:
            pass
        # PTB сам обробить SIGTERM/SIGINT через run_polling

    try:
        signal.signal(signal.SIGTERM, _on_term)
    except (ValueError, OSError):
        pass


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main():
    if not _TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN не задан в .env")
    if _MY_CHAT_ID == 0:
        raise RuntimeError("MY_CHAT_ID не задан в .env")

    from datetime import datetime
    import pytz
    tz = pytz.timezone('Europe/Kyiv')
    current_time = datetime.now(tz)
    logger.info("🕐 Бот стартує. Поточний час: %s (Europe/Kyiv)",
                current_time.strftime("%Y-%m-%d %H:%M:%S %Z"))

    init_db()
    logger.info("База даних ініціалізована.")

    try:
        cleanup_history()
        logger.info("Історія старше 30 днів очищена.")
    except Exception as e:
        logger.warning("cleanup_history error: %s", e)

    try:
        habit_reset_stale_streaks()
        logger.info("Полоски привичок скинуті (> 24 год без виконання).")
    except Exception as e:
        logger.warning("habit_reset_stale_streaks error: %s", e)

    _install_shutdown_handler()

    async def _post_init(application):
        start_scheduler(application.bot)
        asyncio.create_task(_heartbeat_loop())
        logger.info("Планувальник та heartbeat запущені (post_init).")

    app = (
        Application.builder()
        .token(_TELEGRAM_TOKEN)
        .post_init(_post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("reminders", cmd_reminders))
    app.add_handler(CommandHandler("done", cmd_done))

    app.add_handler(CallbackQueryHandler(handle_callback))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    logger.info("Бот запущен. Ожидаю сообщений...")
    try:
        app.run_polling(drop_pending_updates=True)
    finally:
        log_stats_summary()


if __name__ == "__main__":
    main()
