import os
from datetime import datetime
import anthropic
from dotenv import load_dotenv

from agents.memory_agent import remember, recall
from agents.task_agent import format_tasks_for_prompt
from agents.memory_loop import read_memory, read_context

load_dotenv()

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_TEMPLATE = """Ты — личный ИИ-ассистент пользователя в Telegram.
Ты помогаешь планировать дела, отвечаешь на вопросы, ведёшь задачи.
Пользователь может писать и говорить на русском, украинском или английском — отвечай на том же языке, на котором написано сообщение.

## Контекст о пользователе
{context_block}

## Что ты уже знаешь (память)
{memory_block}

{tasks_block}

## Команды (выполняй в КОНЦЕ ответа, на отдельной строке, невидимо для пользователя)

Добавить задачу (ОБЯЗАТЕЛЬНО указывай priority):
[{{"action":"add_task","text":"Купить молоко","due":"2024-12-01","priority":"other"}}]

### Правила приоритета задачи (priority)
Ты ВСЕГДА сам определяешь приоритет — пользователь не должен об этом думать.

- **"goal"** — задача двигает к важной цели: бизнес, проект, рост, деньги, здоровье, отношения.
  Примеры: "Написать pitch deck", "Созвониться с инвестором", "Записаться в зал".
- **"routine"** — регулярная или административная задача: оплатить, купить, позвонить в банк, убраться.
  Примеры: "Оплатить аренду", "Купить продукты", "Ответить на письмо".
- **"other"** — всё остальное, что не попадает в первые две категории.

Если сомневаешься — смотри на контекст из MEMORY.md и context/about.md: что важно пользователю, какие у него цели.

Закрыть задачу:
[{{"action":"done_task","id":3}}]

Обновить память (когда узнал что-то новое о пользователе, его стиле, предпочтениях):
[{{"action":"update_memory","section":"Voice","content":"Пользователь предпочитает короткие ответы"}}]

Секции памяти: Voice, Process, People, Projects, Output, Tools

Несколько команд сразу можно комбинировать в одном массиве.
Если никаких действий не нужно — не добавляй JSON в ответ.

## Текущие дата и время
{datetime_now}
"""


def _build_system_prompt() -> str:
    tasks_block = format_tasks_for_prompt()
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    memory = read_memory() or "(пока пусто)"
    context = read_context() or "(не заполнено)"
    return _SYSTEM_TEMPLATE.format(
        tasks_block=tasks_block,
        datetime_now=now,
        memory_block=memory,
        context_block=context,
    )


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
