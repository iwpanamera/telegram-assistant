import os
import asyncio
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from agents.task_agent import get_tasks, format_tasks_for_user

load_dotenv()

_MY_CHAT_ID = int(os.getenv("MY_CHAT_ID", "0"))

_scheduler = AsyncIOScheduler(timezone="Europe/Moscow")


async def _morning_checkin(bot):
    """Ежедневная утренняя сводка задач в 9:00."""
    tasks = get_tasks()
    if not tasks:
        text = "☀️ Доброе утро\\! Открытых задач нет — наслаждайся свободным днём 🎉"
        await bot.send_message(
            chat_id=_MY_CHAT_ID,
            text=text,
            parse_mode="Markdown",
        )
    else:
        header = f"☀️ *Доброе утро\\!* Вот твои открытые задачи на сегодня:\n\n"
        body = format_tasks_for_user()
        await bot.send_message(
            chat_id=_MY_CHAT_ID,
            text=header + body,
            parse_mode="Markdown",
        )


def start(bot):
    """
    Запустить планировщик.
    Добавляет крон-джобу: ежедневно в 09:00 (МСК) отправляет сводку задач.
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
    _scheduler.start()
