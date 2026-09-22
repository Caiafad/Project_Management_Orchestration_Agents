"""
Event-Emitting Orchestrator

Wraps the orchestrator and all sub-agents to emit real-time events
that power the frontend UI: agent activation, tool calls, document
creation, chain of thought, and final responses.
"""

import sys
import os
import json
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from google import genai
from google.genai import types
from config import GEMINI_API_KEY, GEMINI_MODEL, GUEST_GEMINI_MODEL
from agents.base_agent import (
    convert_tools_to_gemini, _generate_with_retry, make_thinking_config, DEFAULT_MAX_OUTPUT_TOKENS,
)
from orchestrator import ORCHESTRATOR_SYSTEM_PROMPT, ORCHESTRATOR_TOOLS

# Metadata for each agent — used by the frontend for display
AGENT_META = {
    "delegate_to_project_planning": {
        "name": "Project Planning",
        "icon": "chart-gantt",
        "color": "#4A9FFF",
        "description": "Building WBS, timelines and dependencies",
    },
    "delegate_to_scope_definition": {
        "name": "Scope Definition",
        "icon": "bullseye",
        "color": "#9B59B6",
        "description": "Defining in-scope and out-of-scope items",
    },
    "delegate_to_project_orchestration": {
        "name": "Project Orchestration",
        "icon": "people-group",
        "color": "#27AE60",
        "description": "Assigning roles, tasks and RACI matrices",
    },
    "delegate_to_business_manager": {
        "name": "Business Manager",
        "icon": "briefcase",
        "color": "#E67E22",
        "description": "Creating executive presentations and roadmaps",
    },
    "delegate_to_financial_manager": {
        "name": "Financial Manager",
        "icon": "calculator",
        "color": "#1ABC9C",
        "description": "Calculating costs and building financial models",
    },
    "delegate_to_internal_comms": {
        "name": "Internal Comms",
        "icon": "comments",
        "color": "#E91E63",
        "description": "Coordinating team via Gmail and Slack",
    },
    "delegate_to_external_comms": {
        "name": "External Comms",
        "icon": "envelope-open-text",
        "color": "#E74C3C",
        "description": "Managing external stakeholder communications",
    },
    "delegate_to_prioritization": {
        "name": "Prioritization",
        "icon": "ranking-star",
        "color": "#F39C12",
        "description": "Ranking work by value, complexity and priority",
    },
}


def _wrap_tool_handlers(tool_handlers: dict, emit: Callable, username: str = None) -> dict:
    """Wrap a sub-agent's tool handlers to emit events and upload output files to GCS."""
    wrapped = {}

    def _gcs_upload(local_path):
        """Upload a file to GCS under the user's prefix (best-effort)."""
        if not username:
            return
        try:
            from tools.gcs_output import upload_file as _upload
            _upload(username, local_path)
        except Exception as e:
            print(f"[gcs_upload] warning: {e}", flush=True)

    for name, handler in tool_handlers.items():

        if name == "execute_python":
            def make_python(h):
                def fn(code, **kw):
                    emit({"type": "tool_call", "tool": "python",
                          "description": "Running Python calculations..."})
                    from config import OUTPUT_DIR
                    _ext_map = {".xlsx": "excel", ".docx": "word", ".pptx": "powerpoint"}
                    before = set(OUTPUT_DIR.glob("*")) if OUTPUT_DIR.exists() else set()
                    result = h(code, **kw)
                    after = set(OUTPUT_DIR.glob("*")) if OUTPUT_DIR.exists() else set()
                    for fpath in sorted(after - before):
                        doc_type = _ext_map.get(fpath.suffix.lower())
                        if doc_type:
                            _gcs_upload(fpath)
                            emit({"type": "document_created", "doc_type": doc_type,
                                  "filename": fpath.name, "path": str(fpath)})
                    return result
                return fn
            wrapped[name] = make_python(handler)

        elif name == "knowledge_search":
            def make_search(h, _username=username):
                def fn(query, num_results=5, **kw):
                    emit({"type": "tool_call", "tool": "search",
                          "description": f'Searching knowledge base: "{query[:55]}"'})
                    return h(query, num_results, username=_username, **kw)
                return fn
            wrapped[name] = make_search(handler)

        elif name == "create_word_document":
            def make_word(h):
                def fn(title="", **kw):
                    emit({"type": "tool_call", "tool": "word",
                          "description": f"Creating Word document: {title}"})
                    result = h(title=title, **kw)
                    try:
                        d = json.loads(result)
                        if d.get("status") == "created":
                            from pathlib import Path as _P
                            _gcs_upload(_P(d["file_path"]))
                            emit({"type": "document_created", "doc_type": "word",
                                  "filename": d["filename"], "path": d["file_path"]})
                    except Exception:
                        pass
                    return result
                return fn
            wrapped[name] = make_word(handler)

        elif name == "create_excel":
            def make_excel(h):
                def fn(title="", **kw):
                    emit({"type": "tool_call", "tool": "excel",
                          "description": f"Creating Excel spreadsheet: {title}"})
                    result = h(title=title, **kw)
                    try:
                        d = json.loads(result)
                        if d.get("status") == "created":
                            from pathlib import Path as _P
                            _gcs_upload(_P(d["file_path"]))
                            emit({"type": "document_created", "doc_type": "excel",
                                  "filename": d["filename"], "path": d["file_path"]})
                    except Exception:
                        pass
                    return result
                return fn
            wrapped[name] = make_excel(handler)

        elif name == "create_powerpoint":
            def make_pptx(h):
                def fn(title="", **kw):
                    emit({"type": "tool_call", "tool": "powerpoint",
                          "description": f"Creating PowerPoint: {title}"})
                    result = h(title=title, **kw)
                    try:
                        d = json.loads(result)
                        if d.get("status") == "created":
                            from pathlib import Path as _P
                            _gcs_upload(_P(d["file_path"]))
                            emit({"type": "document_created", "doc_type": "powerpoint",
                                  "filename": d["filename"], "path": d["file_path"]})
                    except Exception:
                        pass
                    return result
                return fn
            wrapped[name] = make_pptx(handler)

        else:
            wrapped[name] = handler

    return wrapped


def _create_event_agent(tool_name: str, emit: Callable, username: str = None, is_guest: bool = False):
    """Instantiate a sub-agent with event-aware tool handlers."""
    from agents.project_planning import ProjectPlanningAgent
    from agents.scope_definition import ScopeDefinitionAgent
    from agents.project_orchestration import ProjectOrchestrationAgent
    from agents.business_manager import BusinessManagerAgent
    from agents.financial_manager import FinancialManagerAgent
    from agents.internal_comms import InternalCommsAgent
    from agents.external_comms import ExternalCommsAgent
    from agents.prioritization import PrioritizationAgent

    # Comms agents need username so Gmail calls use the right token
    comms_agents = {"delegate_to_internal_comms", "delegate_to_external_comms"}

    agent_map = {
        "delegate_to_project_planning":     ProjectPlanningAgent,
        "delegate_to_scope_definition":     ScopeDefinitionAgent,
        "delegate_to_project_orchestration":ProjectOrchestrationAgent,
        "delegate_to_business_manager":     BusinessManagerAgent,
        "delegate_to_financial_manager":    FinancialManagerAgent,
        "delegate_to_internal_comms":       InternalCommsAgent,
        "delegate_to_external_comms":       ExternalCommsAgent,
        "delegate_to_prioritization":       PrioritizationAgent,
    }

    cls = agent_map[tool_name]
    if tool_name in comms_agents:
        agent = cls(username=username, is_guest=is_guest)
    else:
        agent = cls(is_guest=is_guest)
    agent.tool_handlers = _wrap_tool_handlers(agent.tool_handlers, emit, username)
    return agent


class EventOrchestrator:
    """Orchestrator that emits structured events throughout execution."""

    def __init__(self, event_callback: Callable, username: str = None, is_guest: bool = False):
        self.emit = event_callback
        self.username = username
        self.is_guest = is_guest
        self.model = GUEST_GEMINI_MODEL if is_guest else GEMINI_MODEL
        self.client = genai.Client(api_key=GEMINI_API_KEY)
        self.conversation_history = []
        self.tools = ORCHESTRATOR_TOOLS
        self.gemini_tools = [types.Tool(
            function_declarations=convert_tools_to_gemini(self.tools)
        )]
        self.config = types.GenerateContentConfig(
            system_instruction=ORCHESTRATOR_SYSTEM_PROMPT,
            tools=self.gemini_tools,
            thinking_config=make_thinking_config(self.model, "select"),
            max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
        )

    def _delegate_handler(self, tool_name: str):
        meta = AGENT_META.get(tool_name, {})

        def handler(task: str = "", context: str = "", **_):
            self.emit({
                "type": "agent_activated",
                "tool_name": tool_name,
                "agent": meta.get("name", tool_name),
                "icon": meta.get("icon", "robot"),
                "color": meta.get("color", "#ffffff"),
                "description": meta.get("description", ""),
                "task": task[:120],
            })
            try:
                agent = _create_event_agent(tool_name, self.emit, self.username, self.is_guest)
                result = agent.run(task, context)
            except Exception as e:
                result = f"Error: {str(e)}"
            self.emit({"type": "agent_done", "tool_name": tool_name,
                       "agent": meta.get("name", tool_name)})
            return result

        return handler

    def run(self, user_input: str) -> str:
        self.emit({"type": "thinking", "text": "Analyzing your request..."})

        self.conversation_history.append(
            types.Content(role="user", parts=[types.Part.from_text(text=user_input)])
        )

        handlers = {t["name"]: self._delegate_handler(t["name"]) for t in self.tools}
        malformed_retries = 0

        # Config that forces exactly one delegation tool call.
        # Used for: (a) the very first call so Gemini cannot skip to plain text,
        # and (b) retries after MALFORMED_FUNCTION_CALL.
        forced_tool_config = types.GenerateContentConfig(
            system_instruction=ORCHESTRATOR_SYSTEM_PROMPT,
            tools=self.gemini_tools,
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="ANY")
            ),
            thinking_config=make_thinking_config(self.model, "select"),
            max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
        )

        is_first_call = True
        active_config = forced_tool_config   # first call must call a tool

        while True:
            try:
                response = _generate_with_retry(
                    self.client, self.model, self.conversation_history, active_config
                )
            except Exception as e:
                self.emit({"type": "error", "message": str(e)})
                self.emit({"type": "done"})
                return f"Error: {str(e)}"

            # After the first forced call, switch to AUTO so Gemini can
            # eventually give a text summary response.
            if is_first_call:
                is_first_call = False
                active_config = self.config

            candidate = response.candidates[0]
            finish_reason = str(getattr(candidate, "finish_reason", ""))

            if "MALFORMED_FUNCTION_CALL" in finish_reason:
                malformed_retries += 1
                if malformed_retries > 2:
                    error_msg = (
                        "I encountered a technical issue routing your request. "
                        "Please try again — if the problem persists, try breaking your request into smaller parts."
                    )
                    self.emit({"type": "error", "message": "Malformed tool call after 3 retries."})
                    self.emit({"type": "response", "text": error_msg})
                    self.emit({"type": "done"})
                    return error_msg
                # Force the next call to make a tool call (no plain-text escape hatch)
                active_config = forced_tool_config
                self.conversation_history.append(types.Content(
                    role="user",
                    parts=[types.Part.from_text(
                        text=(
                            "Your tool call arguments were malformed. "
                            "Please call the appropriate delegation tool again "
                            "(delegate_to_project_planning, delegate_to_scope_definition, "
                            "delegate_to_financial_manager, etc.) with valid JSON arguments."
                        )
                    )]
                ))
                continue

            if candidate.content is None or not candidate.content.parts:
                break

            self.conversation_history.append(candidate.content)
            function_calls = [p for p in candidate.content.parts if p.function_call]

            if not function_calls:
                final_text = "".join(p.text for p in candidate.content.parts if p.text)
                self.emit({"type": "response", "text": final_text})
                self.emit({"type": "done"})
                return final_text

            # Process delegations — collect results, detect hard errors
            fn_parts = []
            failed_agents = []   # agents that returned an error
            succeeded_agents = []
            for part in function_calls:
                fc = part.function_call
                meta = AGENT_META.get(fc.name, {})
                self.emit({
                    "type": "thinking",
                    "text": f"Routing to {meta.get('name', fc.name)}...",
                })
                handler = handlers.get(fc.name)
                try:
                    result = handler(**dict(fc.args)) if handler else f"Unknown tool: {fc.name}"
                except Exception as e:
                    result = f"Error: {str(e)}"

                result_str = str(result)
                is_error = result_str.startswith("Error:") or result_str.startswith("AGENT_FAILED")

                if is_error:
                    failed_agents.append(meta.get("name", fc.name))
                    # Wrap the error so Gemini cannot miss it
                    fn_parts.append(types.Part.from_function_response(
                        name=fc.name,
                        response={
                            "status": "AGENT_FAILED",
                            "result": result_str,
                            "files_generated": [],
                            "instruction": (
                                f"AGENT_FAILED: {meta.get('name', fc.name)} did not produce any files. "
                                "Do NOT describe or list any documents from this agent. "
                                "Tell the user this agent failed and to try again."
                            ),
                        }
                    ))
                else:
                    succeeded_agents.append(meta.get("name", fc.name))
                    fn_parts.append(types.Part.from_function_response(
                        name=fc.name, response={"status": "SUCCESS", "result": result_str}
                    ))

            # If EVERY agent failed, surface the error directly
            if failed_agents and not succeeded_agents:
                error_detail = " | ".join(failed_agents)
                print(f"[EventOrchestrator] All agents failed: {error_detail}", flush=True)
                self.emit({"type": "error", "message": f"Agent error: {error_detail}"})
                fallback = (
                    "I ran into a technical problem completing your request. "
                    "Please try again in a moment."
                )
                self.emit({"type": "response", "text": fallback})
                self.emit({"type": "done"})
                return fallback

            self.conversation_history.append(
                types.Content(role="user", parts=fn_parts)
            )

            # If some agents failed, inject a hard warning so Gemini cannot
            # hallucinate a positive summary for the failed ones.
            if failed_agents:
                failed_list = ", ".join(failed_agents)
                warning = (
                    f"SYSTEM NOTICE — partial failure: The following agents FAILED and generated NO files: {failed_list}. "
                    "You MUST tell the user clearly which deliverables are missing. "
                    "Do NOT describe any documents from the failed agents as if they exist. "
                    "Only summarise outputs from the agents that succeeded."
                )
                self.conversation_history.append(
                    types.Content(role="user", parts=[types.Part.from_text(text=warning)])
                )

            self.emit({"type": "thinking", "text": "Synthesizing results..."})

        fallback = "The request could not be completed — the agent returned an empty response. Please try again."
        self.emit({"type": "error", "message": "Empty response from model — no content or tool calls returned."})
        self.emit({"type": "response", "text": fallback})
        self.emit({"type": "done"})
        return fallback
