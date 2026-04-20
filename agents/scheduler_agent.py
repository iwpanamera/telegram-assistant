import os
import asyncio
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from agents.task_agent import get_tasks, format_tasks_for_user

load_dotenv()

_MY_CHAT_ID = int(os.getenv("MY_CHAT_ID", "0"))

_scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")


async def _morning_checkin(bot):
    """Щоденне ранкове нагадування о 9:00 — постановка задач."""
    tasks = get_tasks()
    if not tasks:
        text = "🌅 Доброго ранку\\! Відкритих задач немає — саме час поставити цілі на сьогодні 🎯"
        await bot.send_message(
            chat_id=_MY_CHAT_ID,
            text=text,
            parse_mode="MarkdownV2",
        )
    else:
        header = "🌅 *Доброго ранку\\!* Ось твої відкриті задачі на сьогодні:\n\n"
        body = format_tasks_for_user()
        await bot.send_message(
            chat_id=_MY_CHAT_ID,
            text=header + body,
            parse_mode="MarkdownV2",
        )


async def _midday_checkin(bot):
    """Денний чек-ін о 13:00 — перевірка прогресу."""
    text = "🙌 Привіт\\! Як справи з задачами? Є прогрес? Якщо щось зависло — розкажи, разом розберемося\\!"
    await bot.send_message(
        chat_id=_MY_CHAT_ID,
        text=text,
        parse_mode="MarkdownV2",
    )


async def _evening_checkin(bot):
    """Вечірній підсумок о 18:00 — виконання задач за день."""
    text = "🌇 Добрий вечір\\! Як пройшов день? Вдалося виконати все заплановане? Підбий підсумки — це допоможе краще спланувати завтра\\."
    await bot.send_message(
        chat_id=_MY_CHAT_ID,
        text=text,
        parse_mode="MarkdownV2",
    )


def start(bot):
    """
    Запустити планувальник.
    Три щоденні нагадування:
      09:00 — постановка задач на день
      13:00 — перевірка прогресу
      18:00 — вечірній підсумок
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
    _scheduler.start()
