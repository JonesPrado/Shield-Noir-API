import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


# Chaves de API

COMIC_VINE_API_KEY = os.getenv("COMIC_VINE_API_KEY")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")


# Modelos das IAs

GEMINI_MODEL = "gemini-3.7-flash"
GROQ_MODEL = "openai/gpt-oss-120b"
OPENROUTER_MODEL = "qwen/qwen3.8-27b:free"