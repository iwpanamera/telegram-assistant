import os
from groq import Groq
from dotenv import load_dotenv
import anthropic

load_dotenv()

_groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
_anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def transcribe(file_path: str) -> str:
    """
    Транскрибировать аудиофайл с помощью Groq Whisper.

    Args:
        file_path: Путь к .ogg / .mp3 / .wav файлу.

    Returns:
        Распознанный текст на русском языке.
    """
    with open(file_path, "rb") as audio_file:
        result = _groq_client.audio.transcriptions.create(
            model="whisper-large-v3-turbo",
            file=audio_file,
            language="ru",
        )
    return result.text


def summarize_transcript(text: str) -> str:
    """
    Суммаризировать длинный транскрипт.

    Используется для голосовых сообщений > 500 слов.
    Экономит токены, сжимая большие блоки текста.

    Args:
        text: Транскрипт голоса.

    Returns:
        Суммаризированный текст (более лаконичный).
    """
    response = _anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        system="Ты помогаешь сжимать длинные тексты. Суммаризируй основные идеи коротко, в 1-2 предложениях. Язык — русский.",
        messages=[
            {"role": "user", "content": f"Суммаризируй: {text}"}
        ]
    )
    return response.content[0].text
