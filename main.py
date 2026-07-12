"""
Project Management Agent — Entry Point

Run this file to start the interactive project management orchestrator.
The orchestrator routes your requests to 7 specialized sub-agents.

Usage:
    python main.py              # Start the interactive orchestrator
    python server.py            # Start the MCP server (for Claude Desktop or external clients)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orchestrator import run_orchestrator

if __name__ == "__main__":
    run_orchestrator()
