import os
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

_client = Groq(api_key=os.getenv("GROQ_API_KEY"))


def transcribe(file_path: str) -> str:
    """
    Транскрибировать аудиофайл с помощью Groq Whisper.

    Args:
        file_path: Путь к .ogg / .mp3 / .wav файлу.

    Returns:
        Распознанный текст на русском языке.
    """
    with open(file_path, "rb") as audio_file:
        result = _client.audio.transcriptions.create(
            model="whisper-large-v3-turbo",
            file=audio_file,
            language="ru",
        )
    return result.text
