import os
import logging
import pytz
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from agents.task_agent import get_tasks, format_tasks_for_user, _fmt_due
from db import (
    events_past_unreviewed, event_mark_reviewed,
    reminders_pending, reminder_mark_done,
    habit_daily_reset, habit_reset_stale_streaks,
    tasks_closed_since,
)

logger = logging.getLogger(__name__)
_TZ = pytz.timezone('Europe/Kyiv')

load_dotenv()

_MY_CHAT_ID = int(os.getenv("MY_CHAT_ID", "0"))

_scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")


async def _morning_checkin(bot):
    """Щоденне ранкове нагадування о 9:00."""
    tasks = get_tasks(use_cache=False)
    if not tasks:
        text = "Доброго ранку. Відкритих задач немає — саме час поставити цілі на сьогодні."
    else:
        header = "Доброго ранку. Ось твої відкриті задачі:\n\n"
        body = format_tasks_for_user()
        text = header + body
    await bot.send_message(chat_id=_MY_CHAT_ID, text=text)


async def _midday_checkin(bot):
    """Денний чек-ін о 13:00."""
    await bot.send_message(
        chat_id=_MY_CHAT_ID,
        text="Як справи з задачами? Є прогрес?",
    )


async def _evening_checkin(bot):
    """Вечірній підсумок о 18:00."""
    await bot.send_message(
        chat_id=_MY_CHAT_ID,
        text="Добрий вечір. Як пройшов день? Вдалося виконати все заплановане?",
    )


async def _check_past_events(bot):
    """Чи є минулі події, по яких ще не питали 'як пройшло?'."""
    past_events = events_past_unreviewed()
    for event in past_events:
        text = f"Як пройшло: {event['text']}?"
        await bot.send_message(chat_id=_MY_CHAT_ID, text=text)
        event_mark_reviewed(event["id"])


async def _check_and_send_reminders(bot):
    """
    Надсилає напоминання. ІДЕМПОТЕНТНО: спочатку mark_done, потім send.
    Якщо send впаде — юзер пропустить одне нагадування, але не отримає
    спаму з повторних тиків scheduler'а.
    """
    try:
        pending = reminders_pending()
        for reminder in pending:
            rid = reminder["id"]
            # Спочатку бронюємо — якщо крашне тут, повторної відправки не буде
            reminder_mark_done(rid)
            try:
                msg = (
                    f"🔔 **Напоминання:** {reminder['text']}\n"
                    f"📅 {_fmt_due(reminder['remind_at'])}"
                )
                await bot.send_message(chat_id=_MY_CHAT_ID, text=msg)
            except Exception as e:
                logger.error("Failed to SEND reminder %d (marked done anyway): %s",
                             rid, e)
    except Exception as e:
        logger.error("Error checking reminders: %s", e)


async def _daily_habit_tick(bot):
    """
    Щоденно о 00:05: скинути streak тим, хто пропустив, і знов відкрити
    усі привички на новий день.
    """
    try:
        habit_reset_stale_streaks()
        reopened = habit_daily_reset()
        logger.info("🔄 Щоденний тік привичок: reopened=%d", reopened)
    except Exception as e:
        logger.error("daily_habit_tick error: %s", e)


async def _nightly_history_compaction(bot):
    """
    Щоденно о 00:15: стиснути повідомлення старші 7 днів
    у секцію Background памʼяті, видалити оригінали.
    """
    try:
        from agents.summarizer import summarize_old_history
        result = summarize_old_history(days=7)
        logger.info("📚 Суммаризація історії: %s", result)
    except Exception as e:
        logger.error("nightly_history_compaction error: %s", e)


async def _weekly_review(bot):
    """
    Неділя 20:00: зведення тижня + запит на рефлексію.
    """
    try:
        since = (datetime.now(_TZ) - timedelta(days=7)).isoformat(timespec="seconds")
        closed = tasks_closed_since(since)

        if not closed:
            text = (
                "🗓️ Тижневий огляд\n\n"
                "За цей тиждень немає закритих задач. "
                "Давай подумаємо разом — що завадило і куди рухатись?"
            )
        else:
            by_prio: dict[str, int] = {}
            by_cat: dict[str, int] = {}
            for t in closed:
                p = t.get("priority", "other")
                c = t.get("category", "other")
                by_prio[p] = by_prio.get(p, 0) + 1
                by_cat[c]  = by_cat.get(c, 0) + 1

            prio_label = {
                "goal": "🎯 цілі", "habit": "⚡ звички",
                "routine": "🔄 рутина", "other": "📌 інше",
            }
            prio_str = ", ".join(
                f"{prio_label.get(k, k)}: {v}"
                for k, v in sorted(by_prio.items(), key=lambda x: -x[1])
            )
            text = (
                f"🗓️ Тижневий огляд\n\n"
                f"Закрито: {len(closed)} задач\n"
                f"По пріоритету — {prio_str}\n\n"
                f"Що було найкориснішим? Що перенести на наступний тиждень?"
            )

        await bot.send_message(chat_id=_MY_CHAT_ID, text=text)
    except Exception as e:
        logger.error("weekly_review error: %s", e)


def start(bot):
    """
    Запустити планувальник.
      09:00 — ранковий чек-ін
      13:00 — денний чек-ін
      18:00 — вечірній підсумок
      20:00 (нд) — тижневий огляд
      00:05 — щоденний тік привичок
      кожні 30 хв — минулі події
      кожну хвилину — напоминання
    """
    current_time = datetime.now(_TZ)
    logger.info("🕐 Планувальник стартує. Поточний час: %s (Europe/Kyiv)",
                current_time.strftime("%Y-%m-%d %H:%M:%S %Z"))

    jobs = [
        (_morning_checkin,         {"trigger": "cron", "hour": 9,  "minute": 0},  "morning"),
        (_midday_checkin,          {"trigger": "cron", "hour": 13, "minute": 0},  "midday"),
        (_evening_checkin,         {"trigger": "cron", "hour": 18, "minute": 0},  "evening"),
        (_weekly_review,           {"trigger": "cron", "day_of_week": "sun",
                                    "hour": 20, "minute": 0},                    "weekly_review"),
        (_daily_habit_tick,        {"trigger": "cron", "hour": 0, "minute": 5},   "daily_habits"),
        (_nightly_history_compaction,{"trigger": "cron", "hour": 0, "minute": 15}, "history_compact"),
        (_check_past_events,       {"trigger": "interval", "minutes": 30},       "past_events"),
        (_check_and_send_reminders,{"trigger": "interval", "minutes": 1},        "reminders"),
    ]

    for func, trig, jid in jobs:
        _scheduler.add_job(
            func, args=[bot], id=jid,
            replace_existing=True, **trig,
        )

    _scheduler.start()
