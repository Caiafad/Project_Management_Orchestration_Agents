import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Agents construct a genai.Client at import/init time; a dummy key keeps tests offline.
os.environ.setdefault("GEMINI_API_KEY", "test-key-not-used")
