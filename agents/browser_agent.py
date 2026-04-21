"""
browser_agent.py — веб-браузер агент для АИ АС

Можливості:
  - browse_url(url, task)   — відкрити URL і витягти релевантну інформацію
  - search_web(query, task) — пошук через DuckDuckGo і витяг результатів
  - execute_browse(cmd)     — dispatch для JSON-команди від Claude

Використовує Playwright (headless Chromium) + Claude Haiku для аналізу вмісту.

Команда Claude для активації:
  [{"action": "browse", "url": "https://...", "task": "знайди ціну"}]
  [{"action": "browse", "query": "iPhone 16 ціна", "task": "знайди найкращу ціну"}]
"""

import asyncio
import logging
import os
import urllib.parse

import anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
_MODEL = "claude-haiku-4-5-20251001"

# Максимальна кількість символів тексту сторінки для передачі в Claude
_MAX_PAGE_CHARS = 6000

# Таймаут завантаження сторінки (мс)
_PAGE_TIMEOUT_MS = 30_000


# ---------------------------------------------------------------------------
# Внутрішні утиліти
# ---------------------------------------------------------------------------

def _clean_page_text(raw: str) -> str:
    """Прибрати зайві пробіли і порожні рядки з тексту сторінки."""
    lines = [line.strip() for line in raw.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    return cleaned[:_MAX_PAGE_CHARS]


async def _fetch_page_text(url: str) -> str:
    """
    Завантажити сторінку через Playwright і повернути текстовий вміст.
    Повертає рядок з помилкою якщо щось пішло не так.
    """
    try:
        from playwright.async_api import async_playwright

        logger.info("browser_agent: launching Chromium for %s", url)
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--single-process",
                ],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()

            try:
                await page.goto(
                    url,
                    timeout=_PAGE_TIMEOUT_MS,
                    wait_until="domcontentloaded",
                )
                # Чекаємо трохи на JS-рендеринг
                await page.wait_for_timeout(1500)

                raw_text = await page.evaluate("""
                    () => {
                        // Прибираємо непотрібні елементи
                        const remove = ['script', 'style', 'noscript',
                                        'nav', 'footer', 'header', 'aside',
                                        'iframe', 'svg', 'form'];
                        remove.forEach(tag => {
                            document.querySelectorAll(tag).forEach(el => el.remove());
                        });
                        return document.body ? document.body.innerText : '';
                    }
                """)
            except Exception as e:
                logger.warning("Помилка при завантаженні %s: %s", url, e)
                raw_text = ""
            finally:
                await browser.close()

        return _clean_page_text(raw_text)

    except ImportError:
        logger.error("Playwright not installed!")
        return "Помилка: Playwright не встановлений."
    except Exception as e:
        logger.error("_fetch_page_text error for %s: %s", url, e, exc_info=True)
        return f"Помилка при завантаженні сторінки: {e}"


def _analyze_with_claude(task: str, page_text: str, url: str = "") -> str:
    """
    Надіслати вміст сторінки в Claude Haiku і отримати відповідь по задачі.
    Повертає стислу відповідь українською.
    """
    if not page_text or "Помилка:" in page_text:
        return page_text or "Не вдалося отримати вміст сторінки."

    source_hint = f" (джерело: {url})" if url else ""
    prompt = (
        f"Задача: {task}\n\n"
        f"Вміст сторінки{source_hint}:\n{page_text}\n\n"
        "Витягни тільки інформацію, потрібну для виконання задачі. "
        "Відповідай коротко і чітко, українською мовою. "
        "Без markdown, без зірочок, без заголовків."
    )

    try:
        response = _client.messages.create(
            model=_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.error("Claude analyze error: %s", e)
        return f"Не вдалося проаналізувати вміст: {e}"


# ---------------------------------------------------------------------------
# Публічні async функції
# ---------------------------------------------------------------------------

async def browse_url(url: str, task: str) -> str:
    """
    Відкрити URL і витягти інформацію по задачі.

    Args:
        url:  Повна URL-адреса
        task: Що саме потрібно знайти / зробити

    Returns:
        Текстова відповідь з результатом
    """
    logger.info("browse_url: url=%s task=%s", url, task)
    page_text = await _fetch_page_text(url)
    return _analyze_with_claude(task, page_text, url)


async def search_web(query: str, task: str) -> str:
    """
    Пошук через DuckDuckGo і витяг релевантних результатів.

    Args:
        query: Пошуковий запит
        task:  Що саме потрібно знайти

    Returns:
        Текстова відповідь з результатами
    """
    encoded = urllib.parse.quote_plus(query)
    search_url = f"https://html.duckduckgo.com/html/?q={encoded}"
    logger.info("search_web: query=%s", query)
    page_text = await _fetch_page_text(search_url)
    return _analyze_with_claude(task or query, page_text, search_url)


async def execute_browse(cmd: dict) -> str:
    """
    Виконати команду browse від Claude.

    Підтримувані формати:
        {"action": "browse", "url": "https://...", "task": "знайди ціну"}
        {"action": "browse", "query": "iPhone 16 ціна", "task": "знайди ціну"}

    Returns:
        Результат у вигляді тексту
    """
    logger.info("execute_browse: cmd=%s", cmd)

    url = (cmd.get("url") or "").strip()
    query = (cmd.get("query") or "").strip()
    task = (cmd.get("task") or "витягни корисну інформацію").strip()

    logger.info("execute_browse: url=%r query=%r task=%r", url, query, task)

    if url:
        result = await browse_url(url, task)
    elif query:
        result = await search_web(query, task)
    else:
        result = "Потрібно вказати url або query для команди browse."

    logger.info("execute_browse: result preview: %s", result[:300])
    return result
