"""
gcal_setup.py — одноразовий скрипт для авторизації Google Calendar.

Запускати ЛОКАЛЬНО (не на Railway):
    pip install google-auth-oauthlib
    python gcal_setup.py

Після виконання скрипт виведе GOOGLE_REFRESH_TOKEN.
Скопіюй його в Railway → Variables.
"""

import json
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from google_auth_oauthlib.flow import Flow

# ─── Заповни свої дані (з Google Cloud Console) ───────────────────────────────
CLIENT_ID     = input("Введи GOOGLE_CLIENT_ID: ").strip()
CLIENT_SECRET = input("Введи GOOGLE_CLIENT_SECRET: ").strip()
# ──────────────────────────────────────────────────────────────────────────────

REDIRECT_URI = "http://localhost:8765/callback"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

client_config = {
    "web": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [REDIRECT_URI],
    }
}

flow = Flow.from_client_config(
    client_config,
    scopes=SCOPES,
    redirect_uri=REDIRECT_URI,
)

auth_url, _ = flow.authorization_url(
    access_type="offline",
    prompt="consent",
    include_granted_scopes="true",
)

print(f"\nВідкриваю браузер для авторизації...")
print(f"Якщо не відкрилось — перейди вручну:\n{auth_url}\n")
webbrowser.open(auth_url)

# Простий сервер для перехоплення callback
auth_code = None

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        params = parse_qs(urlparse(self.path).query)
        auth_code = params.get("code", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<h2>Gotcha! Закрий це вікно та повернись до терміналу.</h2>")

    def log_message(self, *args):
        pass

print("Очікую callback на http://localhost:8765 ...")
server = HTTPServer(("localhost", 8765), _Handler)
server.handle_request()

if not auth_code:
    print("❌ Не вдалося отримати код авторизації.")
    exit(1)

flow.fetch_token(code=auth_code)
creds = flow.credentials

print("\n" + "="*60)
print("✅ Авторизація успішна! Додай ці змінні в Railway → Variables:")
print("="*60)
print(f"GOOGLE_CLIENT_ID     = {CLIENT_ID}")
print(f"GOOGLE_CLIENT_SECRET = {CLIENT_SECRET}")
print(f"GOOGLE_REFRESH_TOKEN = {creds.refresh_token}")
print(f"GOOGLE_CALENDAR_ID   = primary")
print("="*60)
print("\n💡 GOOGLE_CALENDAR_ID можна змінити на конкретний ID календаря зі списку:")
print("   https://calendar.google.com/calendar/r/settings/exportimport\n")
