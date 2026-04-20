import os
import asyncio
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from agents.task_agent import get_tasks, format_tasks_for_user
from db import events_past_unreviewed, event_mark_reviewed

load_dotenv()

_MY_CHAT_ID = int(os.getenv("MY_CHAT_ID", "0"))

_scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")


async def _morning_checkin(bot):
    """Щоденне ранкове нагадування о 9:00 — постановка задач."""
    tasks = get_tasks()
    if not tasks:
        text = "Доброго ранку. Відкритих задач немає — саме час поставити цілі на сьогодні."
    else:
        header = "Доброго ранку. Ось твої відкриті задачі:\n\n"
        body = format_tasks_for_user()
        text = header + body
    await bot.send_message(chat_id=_MY_CHAT_ID, text=text)


async def _midday_checkin(bot):
    """Денний чек-ін о 13:00 — перевірка прогресу."""
    text = "Як справи з задачами? Є прогрес?"
    await bot.send_message(chat_id=_MY_CHAT_ID, text=text)


async def _evening_checkin(bot):
    """Вечірній підсумок о 18:00 — виконання задач за день."""
    text = "Добрий вечір. Як пройшов день? Вдалося виконати все заплановане?"
    await bot.send_message(chat_id=_MY_CHAT_ID, text=text)


async def _check_past_events(bot):
    """
    Перевіряє кожні 30 хвилин чи є минулі події,
    по яких ще не питали 'як пройшло?'
    """
    past_events = events_past_unreviewed()
    for event in past_events:
        text = f"Як пройшло: {event['text']}?"
        await bot.send_message(chat_id=_MY_CHAT_ID, text=text)
        event_mark_reviewed(event["id"])


def start(bot):
    """
    Запустити планувальник.
    Щоденні нагадування:
      09:00 — постановка задач на день
      13:00 — перевірка прогресу
      18:00 — вечірній підсумок
    Кожні 30 хв — перевірка минулих подій.
    """
    _scheduler.add_job(
        _morning_checkin,
        trigger="cron",
        hour=9,
        minute=0,
        args=[bot],
        id="morning_checkin",
        replace_existing=True,
    )
    _scheduler.add_job(
        _midday_checkin,
        trigger="cron",
        hour=13,
        minute=0,
        args=[bot],
        id="midday_checkin",
        replace_existing=True,
    )
    _scheduler.add_job(
        _evening_checkin,
        trigger="cron",
        hour=18,
        minute=0,
        args=[bot],
        id="evening_checkin",
        replace_existing=True,
    )
    _scheduler.add_job(
        _check_past_events,
        trigger="interval",
        minutes=30,
        args=[bot],
        id="check_past_events",
        replace_existing=True,
    )
    _scheduler.start()
