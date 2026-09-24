"""
Event-Emitting Orchestrator

Wraps the orchestrator and all sub-agents to emit real-time events
that power the frontend UI: agent activation, tool calls, document
creation, chain of thought, and final responses.
"""

import sys
import os
import json
import logging
from pathlib import Path
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from google import genai
from google.genai import types
from config import GEMINI_API_KEY, GEMINI_MODEL, GUEST_GEMINI_MODEL
from agents.base_agent import (
    convert_tools_to_gemini, _generate_with_retry, make_thinking_config,
    DEFAULT_MAX_OUTPUT_TOKENS, AgentResult,
)
from orchestrator import ORCHESTRATOR_SYSTEM_PROMPT, ORCHESTRATOR_TOOLS
from project_state import ProjectState, extract_delta
from logging_setup import Timer, bind, context as log_context, new_run_id

log = logging.getLogger(__name__)

MAX_ORCHESTRATOR_TURNS = 12
# Agents whose summaries carry project facts worth folding into the shared state.
STATE_CONTRIBUTORS = {
    "delegate_to_project_planning", "delegate_to_scope_definition",
    "delegate_to_project_orchestration", "delegate_to_financial_manager",
    "delegate_to_business_manager", "delegate_to_prioritization",
}

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


_EXT_TO_DOC_TYPE = {".xlsx": "excel", ".docx": "word", ".pptx": "powerpoint"}
_DOC_TOOLS = {
    "create_word_document": ("word", "Creating Word document"),
    "create_excel": ("excel", "Creating Excel spreadsheet"),
    "create_powerpoint": ("powerpoint", "Creating PowerPoint"),
}


def _wrap_tool_handlers(tool_handlers: dict, emit: Callable, username: str = None,
                        files_out: list | None = None) -> dict:
    """Wrap a sub-agent's tool handlers to emit UI events, upload output files to
    GCS, and record every file created into `files_out` (the agent's
    files_created list) so the orchestrator gets an exact list back."""
    wrapped = {}
    files_out = files_out if files_out is not None else []

    def _gcs_upload(local_path):
        if not username:
            return
        try:
            from tools.gcs_output import upload_file as _upload
            _upload(username, local_path)
        except Exception as e:
            log.warning("GCS upload failed for %s: %s", local_path, e)

    def _record(path, doc_type):
        path = Path(path)
        size = path.stat().st_size if path.exists() else 0
        _gcs_upload(path)
        files_out.append(path.name)
        log.info("file produced", extra={"event": "file_created", "filename": path.name,
                                        "doc_type": doc_type, "bytes": size})
        emit({"type": "document_created", "doc_type": doc_type,
              "filename": path.name, "path": str(path)})

    for name, handler in tool_handlers.items():

        if name == "execute_python":
            def make_python(h):
                def fn(code, **kw):
                    emit({"type": "tool_call", "tool": "python",
                          "description": "Running Python calculations..."})
                    from config import OUTPUT_DIR
                    # Snapshot (name, mtime) so an overwritten file still counts as produced.
                    def snap():
                        return {p: p.stat().st_mtime_ns for p in OUTPUT_DIR.glob("*")} if OUTPUT_DIR.exists() else {}
                    before = snap()
                    with Timer() as t:
                        result = h(code, **kw)
                    after = snap()
                    produced = sorted(p for p, m in after.items() if before.get(p) != m)
                    exit_code, stderr_tail = None, ""
                    try:
                        parsed = json.loads(result)
                        exit_code = parsed.get("exit_code")
                        stderr_tail = (parsed.get("stderr") or "")[-600:]
                    except (TypeError, ValueError):
                        pass
                    level = logging.INFO if exit_code == 0 else logging.ERROR
                    log.log(level, "execute_python finished",
                            extra={"event": "tool_finished", "tool": "execute_python",
                                   "exit_code": exit_code, "duration_ms": t.ms,
                                   "code_chars": len(code or ""), "files_touched": len(produced)})
                    if exit_code not in (0, None) and stderr_tail:
                        # The traceback is the single most useful thing when a doc goes missing.
                        log.error("execute_python stderr", extra={"event": "tool_stderr",
                                                                  "tool": "execute_python",
                                                                  "stderr": stderr_tail})
                    for fpath in produced:
                        doc_type = _EXT_TO_DOC_TYPE.get(fpath.suffix.lower())
                        if doc_type:
                            _record(fpath, doc_type)
                    return result
                return fn
            wrapped[name] = make_python(handler)

        elif name == "knowledge_search":
            def make_search(h, _username=username):
                def fn(query, num_results=5, **kw):
                    emit({"type": "tool_call", "tool": "search",
                          "description": f'Searching knowledge base: "{query[:55]}"'})
                    with Timer() as t:
                        result = h(query, num_results, username=_username, **kw)
                    log.info("knowledge_search finished",
                             extra={"event": "tool_finished", "tool": "knowledge_search",
                                    "query": (query or "")[:120], "duration_ms": t.ms,
                                    "result_chars": len(str(result))})
                    return result
                return fn
            wrapped[name] = make_search(handler)

        elif name in _DOC_TOOLS:
            def make_doc(h, doc_type, label, tool_name):
                def fn(title="", **kw):
                    emit({"type": "tool_call", "tool": doc_type, "description": f"{label}: {title}"})
                    with Timer() as t:
                        result = h(title=title, **kw)
                    try:
                        d = json.loads(result)
                    except (TypeError, ValueError):
                        log.error("%s returned non-JSON output", tool_name,
                                  extra={"event": "tool_failed", "tool": tool_name,
                                         "duration_ms": t.ms, "raw": str(result)[:400]})
                        return result
                    if d.get("status") == "created":
                        log.info("%s created a document", tool_name,
                                 extra={"event": "tool_finished", "tool": tool_name,
                                        "title": str(title)[:120], "duration_ms": t.ms})
                        _record(d["file_path"], doc_type)
                    else:
                        log.error("%s did not create a file", tool_name,
                                  extra={"event": "tool_failed", "tool": tool_name,
                                         "title": str(title)[:120], "duration_ms": t.ms,
                                         "error": str(d.get("error", d))[:400]})
                    return result
                return fn
            wrapped[name] = make_doc(handler, *_DOC_TOOLS[name], name)

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
    agent.tool_handlers = _wrap_tool_handlers(agent.tool_handlers, emit, username, agent.files_created)
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
        self.state = ProjectState()      # shared source of truth across delegations
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

        def handler(task: str = "", ctx: str = "", context: str = "", **_) -> AgentResult:
            agent_label = meta.get("name", tool_name)
            self.emit({
                "type": "agent_activated",
                "tool_name": tool_name,
                "agent": agent_label,
                "icon": meta.get("icon", "robot"),
                "color": meta.get("color", "#ffffff"),
                "description": meta.get("description", ""),
                "task": task[:120],
            })
            supplied = context or ctx
            # The accumulated brief is injected here, in code, so every downstream
            # agent works from the same facts no matter what the router model typed.
            brief_injected = self.state.has_brief() or bool(self.state.files)
            full_context = f"{self.state.to_brief()}\n\n{supplied}".rstrip() if brief_injected else supplied

            with log_context(agent=agent_label):
                log.info("delegating to %s", agent_label,
                         extra={"event": "agent_started", "tool_name": tool_name,
                                "task": task[:200], "brief_injected": brief_injected,
                                "context_chars": len(full_context),
                                "known_files": len(self.state.files)})
                try:
                    with Timer() as t:
                        agent = _create_event_agent(tool_name, self.emit, self.username, self.is_guest)
                        result = agent.run(task, full_context)
                except Exception as e:
                    log.exception("delegation raised", extra={"event": "agent_crashed",
                                                              "tool_name": tool_name})
                    result = AgentResult(text="", status="failed", error=f"{type(e).__name__}: {e}")
                    t = None
                log.log(logging.INFO if result.status == "success" else logging.ERROR,
                        "%s finished (%s)", agent_label, result.status,
                        extra={"event": "agent_finished", "tool_name": tool_name,
                               "status": result.status, "error": result.error or "",
                               "files": ",".join(result.files), "file_count": len(result.files),
                               "duration_ms": getattr(t, "ms", None),
                               "response_chars": len(result.text or "")})
                self.emit({"type": "agent_done", "tool_name": tool_name, "agent": agent_label})
                self._absorb(tool_name, result)
            return result

        return handler

    def _absorb(self, tool_name: str, result: AgentResult) -> None:
        """Fold a finished agent's files and stated facts into the shared state."""
        agent_name = AGENT_META.get(tool_name, {}).get("name", tool_name) + " Agent"
        if result.files:
            self.state.add_files(result.files, agent_name)
        if result.status != "success" or tool_name not in STATE_CONTRIBUTORS:
            return
        self.emit({"type": "thinking", "text": "Recording project facts for downstream agents..."})
        with Timer() as t:
            delta = extract_delta(agent_name, result.text, self.client)
            changed = self.state.merge(delta, agent_name)
        log.info("project state merged from %s", agent_name,
                 extra={"event": "state_merged", "changed": ",".join(changed) or "none",
                        "extracted_fields": ",".join(k for k, v in delta.items() if v) or "none",
                        "duration_ms": t.ms, "state_files": len(self.state.files)})
        if not delta:
            log.warning("no project facts extracted — downstream agents may lack context",
                        extra={"event": "state_extraction_empty", "agent_name": agent_name,
                               "response_chars": len(result.text or "")})

    def run(self, user_input: str) -> str:
        # web_app runs this on a worker thread, where contextvars start empty —
        # rebind so every log line from this run carries the same run_id.
        run_id = new_run_id()
        bind(user=self.username or "anonymous", run_id=run_id, agent="")
        log.info("run started", extra={"event": "run_started", "model": self.model,
                                       "is_guest": self.is_guest,
                                       "prompt": user_input[:300],
                                       "prompt_chars": len(user_input),
                                       "history_turns": len(self.conversation_history)})
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
        turns = 0
        any_agent_succeeded = False          # across the whole run, not just this turn

        while True:
            turns += 1
            if turns > MAX_ORCHESTRATOR_TURNS:
                msg = ("I wasn't able to bring this request to a close within the allowed number of "
                       "steps. The documents produced so far are in your Output Files panel — please "
                       "try a narrower request for the remaining pieces.")
                self.emit({"type": "error", "message": f"Orchestrator exceeded {MAX_ORCHESTRATOR_TURNS} turns."})
                self.emit({"type": "response", "text": msg})
                self.emit({"type": "done"})
                return msg
            try:
                with Timer() as turn_timer:
                    response = _generate_with_retry(
                        self.client, self.model, self.conversation_history, active_config
                    )
            except Exception as e:
                log.exception("orchestrator model call failed",
                              extra={"event": "run_failed", "turn": turns, "model": self.model})
                self.emit({"type": "error", "message": str(e)})
                self.emit({"type": "done"})
                return f"Error: {str(e)}"

            usage = getattr(response, "usage_metadata", None)
            log.info("orchestrator turn %d", turns,
                     extra={"event": "router_turn", "turn": turns, "duration_ms": turn_timer.ms,
                            "finish_reason": str(getattr(response.candidates[0], "finish_reason", "")),
                            "prompt_tokens": getattr(usage, "prompt_token_count", None),
                            "thinking_tokens": getattr(usage, "thoughts_token_count", None),
                            "output_tokens": getattr(usage, "candidates_token_count", None)})

            # After the first forced call, switch to AUTO so Gemini can
            # eventually give a text summary response.
            if is_first_call:
                is_first_call = False
                active_config = self.config

            candidate = response.candidates[0]
            finish_reason = str(getattr(candidate, "finish_reason", ""))

            if "MALFORMED_FUNCTION_CALL" in finish_reason:
                malformed_retries += 1
                log.warning("router emitted a malformed tool call",
                            extra={"event": "router_malformed", "attempt": malformed_retries,
                                   "model": self.model})
                if malformed_retries > 2:
                    error_msg = (
                        "I encountered a technical issue routing your request. "
                        "Please try again — if the problem persists, try breaking your request into smaller parts."
                    )
                    log.error("giving up after repeated malformed routing calls",
                              extra={"event": "run_failed", "reason": "malformed_routing",
                                     "attempts": malformed_retries})
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
                log.info("run complete",
                         extra={"event": "run_finished", "status": "success", "turns": turns,
                                "files_produced": ",".join(f.filename for f in self.state.files),
                                "file_count": len(self.state.files),
                                "contributors": ",".join(self.state.contributors),
                                "response_chars": len(final_text)})
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
                if not handler:
                    result = AgentResult(text="", status="failed", error=f"Unknown tool: {fc.name}")
                else:
                    try:
                        result = handler(**dict(fc.args))
                    except Exception as e:
                        log.exception("handler %s raised", fc.name)
                        result = AgentResult(text="", status="failed", error=str(e))

                agent_label = meta.get("name", fc.name)
                if result.status != "success":
                    failed_agents.append(agent_label)
                    partial = result.files
                    fn_parts.append(types.Part.from_function_response(
                        name=fc.name,
                        response={
                            "status": "AGENT_FAILED",
                            "error": result.error or "unknown error",
                            "result": result.text[:4000],
                            "files_generated": partial,
                            "instruction": (
                                f"AGENT_FAILED: {agent_label} did not complete. "
                                + (f"It produced only these files before failing: {partial}. "
                                   if partial else "It produced NO files. ")
                                + "Do NOT describe any other documents from this agent. "
                                "Tell the user which deliverables are missing and to try again."
                            ),
                        }
                    ))
                else:
                    succeeded_agents.append(agent_label)
                    fn_parts.append(types.Part.from_function_response(
                        name=fc.name, response={
                            "status": "SUCCESS",
                            "result": result.text,
                            "files_generated": result.files,
                        }
                    ))

            any_agent_succeeded = any_agent_succeeded or bool(succeeded_agents)

            # Only bail out when nothing has succeeded in the entire run — a late
            # failure after earlier successes must still be summarised honestly.
            if failed_agents and not any_agent_succeeded:
                # Record the responses first: conversation_history persists across user
                # messages, and a function_call with no matching response would malform
                # the next request in this session.
                self.conversation_history.append(types.Content(role="user", parts=fn_parts))
                error_detail = " | ".join(failed_agents)
                log.error("every delegated agent failed",
                          extra={"event": "run_finished", "status": "all_failed", "turns": turns,
                                 "failed_agents": error_detail})
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
                log.warning("partial failure in this turn",
                            extra={"event": "turn_partial_failure", "turn": turns,
                                   "failed_agents": failed_list,
                                   "succeeded_agents": ", ".join(succeeded_agents)})
                warning = (
                    f"SYSTEM NOTICE — partial failure: these agents did NOT complete: {failed_list}. "
                    "Report exactly the files listed in each agent's files_generated — for a failed "
                    "agent that is usually none, and never more than what is listed. "
                    "You MUST tell the user clearly which deliverables are missing. "
                    "Only summarise outputs from the agents that succeeded."
                )
                self.conversation_history.append(
                    types.Content(role="user", parts=[types.Part.from_text(text=warning)])
                )

            self.emit({"type": "thinking", "text": "Synthesizing results..."})

        fallback = "The request could not be completed — the agent returned an empty response. Please try again."
        log.error("model returned no content and no tool calls",
                  extra={"event": "run_finished", "status": "empty_response", "turns": turns})
        self.emit({"type": "error", "message": "Empty response from model — no content or tool calls returned."})
        self.emit({"type": "response", "text": fallback})
        self.emit({"type": "done"})
        return fallback
