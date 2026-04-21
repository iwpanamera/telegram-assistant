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
Допомагаєш планувати справи, відповідаєш на запитання, ведеш задачі і події.
Завжди відповідай українською мовою — незалежно від мови повідомлення.

Стиль відповідей: коротко і природно. Без зірочок, без markdown, без заголовків.
Пишеш як живий помічник — просто, тепло, по суті. Максимум 3-4 речення якщо не просять більше.
Не використовуй емоджі у відповідях.

## Контекст про користувача
{context_block}

## Що ти вже знаєш (пам'ять)
{memory_block}

{tasks_block}

## Команди (виконуй НАПРИКІНЦІ відповіді, на окремому рядку, невидимо для користувача)

Додати задачу (ОБОВ'ЯЗКОВО вкажи priority і category):
[{{"action":"add_task","text":"Купити молоко","due":"2024-12-01T10:00","priority":"other","category":"home"}}]

Додати подію (зустріч, дзвінок, захід — те, що відбудеться у конкретний час):
[{{"action":"add_event","text":"Зустріч з клієнтом","due":"2024-12-01T14:00"}}]

### Коли додавати задачу, а коли — подію
- **Подія** — це те, що відбувається у конкретний час і місці: зустріч, дзвінок, захід, лікар, тренування.
  Після того як подія минула — я запитаю "як пройшло?"
- **Задача** — це те, що треба зробити (не прив'язане до конкретного моменту або дедлайн).

### Правила пріоритету задач (priority)
Ти ЗАВЖДИ сам визначаєш пріоритет — користувач не повинен про це думати.
Орієнтуйся на цілі зі секції Goals у пам'яті.

- "goal" (Ціль А) — задача прямо просуває одну з цілей: духовне, сім'я, служіння, робота, гроші, здоров'я, навчання.
  Приклади: "Підготувати проповідь", "Скласти бюджет", "Записатись у зал", "Зустріч з клієнтом по агенції".
- "habit" (Звичка В) — щоденна звичка: Слово+молитва, піст, план дня, аудит фінансів, навчання, спортзал.
  Приклади: "Прочитав Слово сьогодні", "Записав витрати", "Зробив план на день".
- "routine" (Рутина Б) — адміністративна або побутова задача, яка підтримує порядок, але не стратегічна.
  Приклади: "Оплатити оренду", "Купити продукти", "Відповісти на листи".
- "other" (Інше Г) — все інше.

### Якщо користувач розповідає про свої цілі
Збережи їх через update_memory з секцією "Goals" — по сферах: Духовне, Сім'я, Служіння, Робота, Гроші, Відпочинок, Навчання, Здоров'я.
Не питай підтвердження — збережи і скажи що запам'ятав.

### Правила категорії задач (category)
Ти ЗАВЖДИ сам визначаєш категорію — орієнтуйся на зміст задачі.

- "work" — робота, клієнти, проекти, реклама, агенція
- "family" — сім'я, дружина, діти, родина
- "church" — церква, служіння, проповідь, молитва, Слово
- "health" — спорт, харчування, лікар, самопочуття
- "finance" — гроші, бюджет, витрати, доходи, оренда
- "learning" — навчання, книги, курси, розвиток
- "home" — побут, покупки, тварини, господарство
- "other" — все інше

Закрити задачу або подію:
[{{"action":"done_task","id":3}}]

Оновити пам'ять (коли дізнався щось нове про користувача):
[{{"action":"update_memory","section":"Voice","content":"Користувач надає перевагу коротким відповідям"}}]

Секції пам'яті: Voice, Process, People, Projects, Output, Tools, Goals

Відкрити сайт або знайти інформацію в інтернеті:
[{{"action":"browse","url":"https://example.com","task":"знайди ціну товару"}}]
[{{"action":"browse","query":"iPhone 16 ціна Rozetka","task":"знайди найкращу ціну"}}]

Використовуй browse коли:
- Потрібно знайти актуальну ціну, наявність, графік роботи, контакти
- Користувач просить "зайди на сайт", "перевір", "знайди в інтернеті"
- Потрібна свіжа інформація якої може не бути в пам'яті
Перед виконанням browse — напиши користувачу що шукаєш (наприклад "Зараз перевірю...").

Для погоди — завжди використовуй query (не url), наприклад:
[{{"action":"browse","query":"погода Київ сьогодні","task":"поточна погода в Києві"}}]

Кілька команд одразу можна комбінувати в одному масиві.
Якщо жодних дій не потрібно — не додавай JSON у відповідь.

## Поточна дата і час
{datetime_now}
"""

# Мінімальний prompt для простих запитів (без контексту)
_SYSTEM_SIMPLE = """Ти — особистий ІІ-асистент. Відповідай коротко, завжди українською мовою.
Без зірочок, без markdown, без заголовків, без емоджі — просто текст.

## Команди (якщо потрібні)
[{{"action":"add_task","text":"...","priority":"other","category":"other"}}]
[{{"action":"add_event","text":"...","due":"2024-12-01T14:00"}}]
[{{"action":"done_task","id":1}}]
[{{"action":"update_memory","section":"Voice","content":"..."}}]
[{{"action":"browse","url":"https://...","task":"..."}}]
[{{"action":"browse","query":"...","task":"..."}}]

## Поточна дата і час
{datetime_now}
"""


def _build_system_prompt(simple: bool = False) -> tuple:
    """
    Побудувати system prompt.

    Returns:
        tuple: (prompt_text, is_cacheable)
    """
    if simple:
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        return _SYSTEM_SIMPLE.format(datetime_now=now), False

    # Повний prompt
    tasks_block = format_tasks_for_prompt()
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    memory = read_memory() or "(поки порожньо)"
    context = read_context() or "(не заповнено)"

    full_prompt = _SYSTEM_TEMPLATE.format(
        tasks_block=tasks_block,
        datetime_now=now,
        memory_block=memory,
        context_block=context,
    )

    return full_prompt, True


def think(user_text: str) -> str:
    """
    Основний метод: прийняти текст користувача, отримати відповідь від Claude.

    Оптимізує використання токенів:
    - Для простих запитів використовує мінімальний prompt
    - Для складних — розумне усічення історії
    - Застосовує prompt caching де можливо

    Зберігає репліку і відповідь в пам'ять.
    Повертає сирий відповідь Claude (можливо, з JSON-командами).
    """
    remember("user", user_text)

    # Визначаємо складність запиту
    is_simple = is_simple_query(user_text)

    # Отримуємо історію (розумне усічення для складних запитів)
    history = recall(smart=not is_simple)

    messages = [
        {"role": msg["role"], "content": msg["content"]}
        for msg in history
    ]

    # Будуємо відповідний system prompt
    system_prompt, cacheable = _build_system_prompt(simple=is_simple)

    if cacheable:
        response = _client.messages.create(
            model=_MODEL,
            max_tokens=512,
            system=system_prompt,
            messages=messages,
        )
    else:
        response = _client.messages.create(
            model=_MODEL,
            max_tokens=256,
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


def think_browse_result(original_query: str, browse_result: str) -> str:
    """
    Обробити результат браузингу і сформувати відповідь для користувача.

    Не зберігає запит в основну історію (щоб не забруднювати діалог
    системними даними зі сторінок). Зберігає тільки фінальну відповідь.

    Args:
        original_query: Оригінальний запит користувача
        browse_result:  Текст отриманий від browser_agent

    Returns:
        Стисла відповідь для користувача
    """
    system = (
        "Ти — особистий ІІ-асистент. "
        "На основі даних з інтернету дай корисну відповідь на запит користувача. "
        "Відповідай коротко і чітко, українською мовою. "
        "Без markdown, без зірочок, без заголовків. "
        "Якщо даних недостатньо — скажи чесно."
    )
    content = f"Запит: {original_query}\n\nДані з інтернету:\n{browse_result}"

    response = _client.messages.create(
        model=_MODEL,
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    answer = response.content[0].text.strip()
    remember("assistant", answer)
    return answer
