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

# Повний system prompt (з кешуванням)
_SYSTEM_TEMPLATE = """Ти — особистий ІІ-асистент користувача в Telegram.
Допомагаєш планувати справи, відповідаєш на запитання, ведеш задачі.
Завжди відповідай українською мовою — незалежно від мови повідомлення.

Стиль відповідей: коротко і природно. Без зірочок, без markdown, без заголовків.
Пишеш як живий помічник — просто, тепло, по суті. Максимум 3-4 речення якщо не просять більше.

## Контекст о пользователе
{context_block}

## Что ты уже знаешь (память)
{memory_block}

{tasks_block}

## Команди (виконуй НАПРИКІНЦІ відповіді, на окремому рядку, невидимо для користувача)

Додати задачу (ОБОВ'ЯЗКОВО вкажи priority і category):
[{{"action":"add_task","text":"Купити молоко","due":"2024-12-01","priority":"other","category":"home"}}]

### Правила пріоритету (priority)
Ти ЗАВЖДИ сам визначаєш пріоритет — користувач не повинен про це думати.
Орієнтуйся на цілі 2026 з context/goals.md.

Орієнтуйся на цілі зі секції Goals у MEMORY.md.

- **"goal"** (🔴 А) — задача прямо просуває одну з цілей: духовне, сім'я, служіння, робота, гроші, здоров'я, навчання.
  Приклади: "Підготувати проповідь", "Скласти бюджет", "Записатись у зал", "Зустріч з клієнтом по агенції".
- **"habit"** (🟢 В) — щоденна звичка: Слово+молитва, піст, план дня, аудит фінансів, навчання, спортзал.
  Приклади: "Прочитав Слово сьогодні", "Записав витрати", "Зробив план на день".
- **"routine"** (🟡 Б) — адміністративна або побутова задача, яка підтримує порядок, але не стратегічна.
  Приклади: "Оплатити оренду", "Купити продукти", "Відповісти на листи".
- **"other"** (⚪ Г) — все інше.

### Якщо користувач розповідає про свої цілі
Збережи їх через update_memory з секцією "Goals" — по сферах: Духовне, Сім'я, Служіння, Робота, Гроші, Відпочинок, Навчання, Здоров'я.
Не питай підтвердження — збережи і скажи що запам'ятав.

### Правила категорії (category)
Ти ЗАВЖДИ сам визначаєш категорію — орієнтуйся на зміст задачі.

- **"work"** 💼 — робота, клієнти, проекти, реклама, агенція
- **"family"** 👨‍👩‍👧 — сім'я, дружина, діти, родина
- **"church"** ✝️ — церква, служіння, проповідь, молитва, Слово
- **"health"** 💪 — спорт, харчування, лікар, самопочуття
- **"finance"** 💰 — гроші, бюджет, витрати, доходи, оренда
- **"learning"** 📚 — навчання, книги, курси, розвиток
- **"home"** 🏠 — побут, покупки, тварини, господарство
- **"other"** 📌 — все інше

Закрити задачу:
[{{"action":"done_task","id":3}}]

Оновити пам'ять (коли дізнався щось нове про користувача):
[{{"action":"update_memory","section":"Voice","content":"Користувач надає перевагу коротким відповідям"}}]

Секції пам'яті: Voice, Process, People, Projects, Output, Tools, Goals

Кілька команд одразу можна комбінувати в одному масиві.
Якщо жодних дій не потрібно — не додавай JSON у відповідь.

## Текущие дата и время
{datetime_now}
"""

# Мінімальний prompt для простих запитів (без контексту)
_SYSTEM_SIMPLE = """Ти — особистий ІІ-асистент. Відповідай коротко, завжди українською мовою.
Без зірочок, без markdown, без заголовків — просто текст.

## Команди (якщо потрібні)
[{{"action":"add_task","text":"...","priority":"other","category":"other"}}]
[{{"action":"done_task","id":1}}]
[{{"action":"update_memory","section":"Voice","content":"..."}}]

## Поточна дата і час
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
