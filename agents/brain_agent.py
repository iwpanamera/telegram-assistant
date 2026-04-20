import os
from datetime import datetime
import anthropic
from dotenv import load_dotenv

from agents.memory_agent import remember, recall
from agents.task_agent import format_tasks_for_prompt
from agents.memory_loop import read_memory, read_context
from agents.optimization_utils import is_simple_query, get_system_prompt_size_estimate

load_dotenv()

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
_MODEL = "claude-haiku-4-5-20251001"

# Полный system prompt (с кэшированием)
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

### Правила пріоритету задачі (priority)
Ти ЗАВЖДИ сам визначаєш пріоритет — користувач не повинен про це думати.
Орієнтуйся на цілі 2026 з context/goals.md.

- **"goal"** (🔴 А) — задача прямо просуває одну з цілей 2026: духовне, сім'я, служіння, робота, гроші, здоров'я, навчання.
  Приклади: "Підготувати проповідь", "Скласти бюджет", "Записатись у зал", "Зустріч з клієнтом по агенції".
- **"habit"** (🟢 В) — щоденна звичка зі списку цілей: Слово+молитва, піст, план дня, аудит фінансів, навчання, спортзал.
  Приклади: "Прочитав Слово сьогодні", "Записав витрати", "Зробив план на день".
- **"routine"** (🟡 Б) — адміністративна або побутова задача, яка підтримує порядок, але не стратегічна.
  Приклади: "Оплатити оренду", "Купити продукти", "Відповісти на листи".
- **"other"** (⚪ Г) — все інше, що не стосується жодної з категорій вище.

Якщо сумніваєшся — дивись context/goals.md і MEMORY.md.

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

# Минимальный prompt для простых запросов (без контекста)
_SYSTEM_SIMPLE = """Ты — личный ИИ-ассистент. Отвечай коротко, помогай с простыми вопросами.

## Команды (если нужны)
[{{"action":"add_task","text":"...","priority":"other"}}]
[{{"action":"done_task","id":1}}]
[{{"action":"update_memory","section":"Voice","content":"..."}}]

## Текущие дата и время
{datetime_now}
"""


def _build_system_prompt(simple: bool = False) -> tuple:
    """
    Построить system prompt.

    Returns:
        tuple: (prompt_text, is_cacheable) — вторая часть показывает, можно ли кэшировать
    """
    if simple:
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        return _SYSTEM_SIMPLE.format(datetime_now=now), False

    # Полный prompt
    tasks_block = format_tasks_for_prompt()
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    memory = read_memory() or "(пока пусто)"
    context = read_context() or "(не заполнено)"

    full_prompt = _SYSTEM_TEMPLATE.format(
        tasks_block=tasks_block,
        datetime_now=now,
        memory_block=memory,
        context_block=context,
    )

    return full_prompt, True


def think(user_text: str) -> str:
    """
    Основной метод: принять текст пользователя, получить ответ от Claude.

    Оптимизирует использование токенов:
    - Для простых запросов использует минимальный prompt
    - Для сложных использует умное усечение истории
    - Применяет prompt caching где возможно

    Сохраняет реплику и ответ в память.
    Возвращает сырой ответ Claude (возможно, с JSON-командами).
    """
    remember("user", user_text)

    # Определяем сложность запроса
    is_simple = is_simple_query(user_text)

    # Получаем историю (умное усечение для сложных запросов)
    history = recall(smart=not is_simple)

    messages = [
        {"role": msg["role"], "content": msg["content"]}
        for msg in history
    ]

    # Строим подходящий system prompt
    system_prompt, cacheable = _build_system_prompt(simple=is_simple)

    # Используем cache_control для статичных частей (если простой prompt)
    if cacheable:
        # Для сложных запросов просто отправляем, без кэша
        # (т.к. динамичные данные часто меняются)
        response = _client.messages.create(
            model=_MODEL,
            max_tokens=512,  # для сложных вопросов меньше
            system=system_prompt,
            messages=messages,
        )
    else:
        # Для простых — можно применить кэширование
        response = _client.messages.create(
            model=_MODEL,
            max_tokens=256,  # для простых вопросов достаточно
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"}
                }
            ],
            messages=messages,
        )

    answer = response.content[0].text
    remember("assistant", answer)
    return answer
