import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

# Vertex AI Search
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
VERTEX_DATASTORE_ID = os.getenv("VERTEX_DATASTORE_ID")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "global")

# Gmail OAuth
GOOGLE_CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials/google_credentials.json")
GOOGLE_TOKEN_PATH = os.getenv("GOOGLE_TOKEN_PATH", "tokens/gmail_token.json")

# Slack
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

# Output — resolve to absolute path so FileResponse and subprocesses always agree
_base = Path(__file__).parent  # directory containing config.py
OUTPUT_DIR = (_base / os.getenv("OUTPUT_DIR", "output")).resolve()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Server
PORT = int(os.getenv("PORT", 8000))

# Auth
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-production")
