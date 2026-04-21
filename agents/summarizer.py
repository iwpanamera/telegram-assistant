"""
summarizer.py — стискає стару історію діалогу у секцію памʼяті "Background".

Ідея: замість обрізати повідомлення старші 7 днів — підсумовувати їх у 3-5
речень і додавати до MEMORY.md у секцію Background. Старі рядки з БД потім
видаляються. Економить токени довгостроково і зберігає сенс розмов.
"""
import os
import logging
import anthropic
from dotenv import load_dotenv

from db import history_get_older_than, history_delete_by_ids
from agents.memory_loop import read_memory, update_memory
from agents.metrics import log_anthropic_usage

load_dotenv()

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    max_retries=3,
    timeout=30.0,
)
_MODEL = "claude-haiku-4-5-20251001"

# Мінімум повідомлень для запуску суммаризації
_MIN_MESSAGES = 10


def summarize_old_history(days: int = 7) -> str:
    """
    Знайти повідомлення старше N днів, стиснути в короткий підсумок,
    оновити секцію Background у MEMORY.md і видалити оригінали з БД.

    Повертає: діагностичне повідомлення про результат.
    """
    old = history_get_older_than(days=days, limit=200)
    if len(old) < _MIN_MESSAGES:
        logger.info("summarize: only %d old messages, skip", len(old))
        return f"Тільки {len(old)} старих повідомлень — пропускаю."

    # Формуємо діалог для Claude
    transcript = "\n".join(
        f"{m['role']}: {m['content']}" for m in old
    )

    # Забираємо існуючий Background (щоб доповнити, не затерти)
    memory = read_memory() or ""
    existing_bg = ""
    if "## Background" in memory:
        try:
            existing_bg = memory.split("## Background", 1)[1].split("\n##", 1)[0].strip()
        except Exception:
            existing_bg = ""

    prompt = (
        "Ось шматок старого діалогу між користувачем (user) та його асистентом (assistant). "
        "Стисни у 3-5 речень головні факти про користувача, його рішення, "
        "контекст проектів і звичок. Без прямих цитат. Українською мовою.\n\n"
    )
    if existing_bg:
        prompt += f"Вже знане про користувача:\n{existing_bg}\n\nНова частина діалогу:\n"
    prompt += transcript

    try:
        resp = _client.messages.create(
            model=_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        log_anthropic_usage(resp, label="summarize_history")
        summary = resp.content[0].text.strip()
    except Exception as e:
        logger.error("summarize_old_history LLM error: %s", e)
        return f"Помилка LLM: {e}"

    # Оновлюємо секцію Background — дописуємо новий абзац
    if existing_bg:
        new_bg = f"{existing_bg}\n\n{summary}"
    else:
        new_bg = summary
    update_memory("Background", new_bg)

    # Видаляємо зі свого діалогу те, що вже стиснули
    ids_to_delete = [m["id"] for m in old]
    history_delete_by_ids(ids_to_delete)

    logger.info("summarize: compressed %d messages → %d chars in Background",
                len(old), len(summary))
    return f"Стиснуто {len(old)} повідомлень у {len(summary)} символів."
