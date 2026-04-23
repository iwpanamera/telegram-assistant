"""
calendar_agent.py — Google Calendar інтеграція.

Використовує OAuth2 з refresh_token (без файлів, тільки env vars).

Змінні середовища (Railway Variables):
    GOOGLE_CLIENT_ID       — OAuth2 Client ID
    GOOGLE_CLIENT_SECRET   — OAuth2 Client Secret
    GOOGLE_REFRESH_TOKEN   — Refresh token (отримати через gcal_setup.py)
    GOOGLE_CALENDAR_ID     — ID календаря (за замовч. 'primary')
"""

import os
import logging
from datetime import datetime, timedelta

import pytz
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
_TZ = "Europe/Kyiv"


def _get_credentials():
    """Побудувати Google OAuth2 credentials з env vars."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        raise RuntimeError(
            "google-api-python-client не встановлений. "
            "Додай до requirements.txt: google-api-python-client google-auth-oauthlib"
        )

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        raise RuntimeError(
            "Відсутні Google OAuth env vars. "
            "Потрібні: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN. "
            "Запусти gcal_setup.py для отримання токенів."
        )

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=_SCOPES,
    )

    # Refresh access token
    creds.refresh(Request())
    return creds


def _get_service():
    """Повернути Google Calendar API service."""
    from googleapiclient.discovery import build
    creds = _get_credentials()
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def add_event_to_calendar(
    title: str,
    due_iso: str,
    description: str = "",
    duration_minutes: int = 60,
) -> str | None:
    """
    Додати подію в Google Calendar.

    Args:
        title:            Назва події
        due_iso:          Дата/час у форматі ISO: "2026-04-25T14:00" або "2026-04-25"
        description:      Опис (необов'язково)
        duration_minutes: Тривалість у хвилинах (за замовч. 60 хв)

    Returns:
        HTML-посилання на подію або None при помилці
    """
    if not os.getenv("GOOGLE_CLIENT_ID"):
        logger.debug("GOOGLE_CLIENT_ID не задано — пропускаємо Google Calendar")
        return None

    try:
        service = _get_service()
        calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")
        tz = pytz.timezone(_TZ)

        # Парсимо due_iso
        dt_start = None
        all_day = False
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt_start = datetime.strptime(due_iso, fmt)
                if fmt == "%Y-%m-%d":
                    all_day = True
                break
            except ValueError:
                continue

        if dt_start is None:
            logger.warning(f"[calendar] Не вдалося розпарсити дату: {due_iso}")
            return None

        if all_day:
            event_body = {
                "summary": title,
                "description": description,
                "start": {"date": dt_start.strftime("%Y-%m-%d")},
                "end": {"date": dt_start.strftime("%Y-%m-%d")},
            }
        else:
            dt_start_aware = tz.localize(dt_start)
            dt_end_aware = dt_start_aware + timedelta(minutes=duration_minutes)
            event_body = {
                "summary": title,
                "description": description,
                "start": {
                    "dateTime": dt_start_aware.isoformat(),
                    "timeZone": _TZ,
                },
                "end": {
                    "dateTime": dt_end_aware.isoformat(),
                    "timeZone": _TZ,
                },
            }

        created = service.events().insert(
            calendarId=calendar_id,
            body=event_body,
        ).execute()

        link = created.get("htmlLink")
        logger.info(f"[calendar] Подію додано: {title} → {link}")
        return link

    except Exception as e:
        logger.error(f"[calendar] Помилка при додаванні події '{title}': {e}")
        return None
