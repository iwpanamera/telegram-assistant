import os
from datetime import datetime
import anthropic
from dotenv import load_dotenv

from agents.memory_agent import remember, recall
from agents.task_agent import format_tasks_for_prompt

load_dotenv()

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_TEMPLATE = """Ты — личный ИИ-ассистент пользователя в Telegram.
Ты помогаешь планировать дела, отвечаешь на вопросы, ведёшь задачи.

{tasks_block}

## Управление задачами
Если пользователь просит добавить задачу — в КОНЦЕ своего ответа (на отдельной строке)
добавь JSON-массив с командой. Примеры:

Добавить задачу:
[{{"action":"add_task","text":"Купить молоко","due":"2024-12-01"}}]

Закрыть задачу:
[{{"action":"done_task","id":3}}]

Несколько команд сразу:
[{{"action":"add_task","text":"Позвонить врачу","due":null}},{{"action":"done_task","id":1}}]

Если никаких действий с задачами не нужно — не добавляй JSON в ответ.

## Текущие дата и время
{datetime_now}
"""


def _build_system_prompt() -> str:
    tasks_block = format_tasks_for_prompt()
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    return _SYSTEM_TEMPLATE.format(tasks_block=tasks_block, datetime_now=now)


def think(user_text: str) -> str:
    """
    Основной метод: принять текст пользователя, получить ответ от Claude.

    Сохраняет реплику пользователя и ответ ассистента в память.
    Возвращает сырой ответ Claude (возможно, с JSON-командами в конце).
    """
    remember("user", user_text)

    history = recall()

    messages = [
        {"role": msg["role"], "content": msg["content"]}
        for msg in history
    ]

    response = _client.messages.create(
        model=_MODEL,
        max_tokens=1024,
        system=_build_system_prompt(),
        messages=messages,
    )

    answer = response.content[0].text
    remember("assistant", answer)
    return answer
