import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Gemini — model tiers. Every agent declares a TIER; the model behind each tier is
# set here (env-overridable) so an upgrade or a rollback is a one-line change.
#   reasoning : orchestrator + long-form document agents (planning, scope, financial, business)
#   fast      : lighter agents (prioritization, orchestration, comms), guest sessions
#   lite      : cheap structured-output side calls (project-state extraction)
# Previous-generation fallbacks: gemini-2.5-pro / gemini-2.5-flash / gemini-2.5-flash-lite.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL_REASONING = os.getenv("MODEL_REASONING", "gemini-3.1-pro-preview")
MODEL_FAST = os.getenv("MODEL_FAST", "gemini-3.8-flash")
MODEL_LITE = os.getenv("MODEL_LITE", "gemini-3.1-flash-lite")
MODEL_TIERS = {"reasoning": MODEL_REASONING, "fast": MODEL_FAST, "lite": MODEL_LITE}
# Per-agent override: MODEL_OVERRIDE_<AGENT_KEY>=<model id>, e.g. MODEL_OVERRIDE_FINANCIAL_MANAGER
def model_for(tier: str, agent_key: str | None = None) -> str:
    if agent_key:
        override = os.getenv(f"MODEL_OVERRIDE_{agent_key.upper()}")
        if override:
            return override
    return MODEL_TIERS[tier]

GEMINI_MODEL = os.getenv("GEMINI_MODEL") or MODEL_REASONING  # orchestrator

# Vertex AI Search
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
VERTEX_DATASTORE_ID = os.getenv("VERTEX_DATASTORE_ID")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "global")

# Gmail OAuth
GOOGLE_CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials/google_credentials.json")
GOOGLE_TOKEN_PATH = os.getenv("GOOGLE_TOKEN_PATH", "tokens/gmail_token.json")

# Slack — global bot token (fallback for regular users) + OAuth app creds for
# the per-user "Connect Slack" flow
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CLIENT_ID = os.getenv("SLACK_CLIENT_ID", "")
SLACK_CLIENT_SECRET = os.getenv("SLACK_CLIENT_SECRET", "")
SLACK_REDIRECT_URI = os.getenv("SLACK_REDIRECT_URI", "")
SLACK_OAUTH_SCOPES = "chat:write,chat:write.public,channels:read,groups:read,im:write,users:read"

# Output — resolve to absolute path so FileResponse and subprocesses always agree
_base = Path(__file__).parent  # directory containing config.py
OUTPUT_DIR = (_base / os.getenv("OUTPUT_DIR", "output")).resolve()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Server
PORT = int(os.getenv("PORT", 8000))

# Auth
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-production")

# Trial mode (guest access)
GUEST_GEMINI_MODEL = os.getenv("GUEST_GEMINI_MODEL", "gemini-2.5-flash")
GUEST_MAX_OUTPUT_TOKENS = int(os.getenv("GUEST_MAX_OUTPUT_TOKENS", 16000))
TRIAL_SESSION_HOURS = int(os.getenv("TRIAL_SESSION_HOURS", 24))
TRIAL_MAX_GENERATIONS = int(os.getenv("TRIAL_MAX_GENERATIONS", 3))
TRIAL_DAILY_CAP = int(os.getenv("TRIAL_DAILY_CAP", 40))
TRIAL_LEADS_SHEET_ID = os.getenv("TRIAL_LEADS_SHEET_ID", "")
PURGE_SECRET = os.getenv("PURGE_SECRET", "")
