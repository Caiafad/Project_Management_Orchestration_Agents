"""
BaseAgent — the agentic tool-calling loop every sub-agent runs on.

A sub-agent is stateless per call: run(task, context) builds a fresh
conversation, forces research first (knowledge_search when available), then
lets the model call its tools until it produces a final text answer. The result
is an AgentResult carrying the text, the files the run produced, and an honest
status — a tool call that returned an error never counts as a produced file.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import re
import time
from dataclasses import dataclass, field

import httpx
from google import genai
from google.genai import types, errors as genai_errors

from config import (
    GEMINI_API_KEY, GEMINI_MODEL, GUEST_GEMINI_MODEL, GUEST_MAX_OUTPUT_TOKENS, model_for,
)

log = logging.getLogger(__name__)

GEMINI_TIMEOUT = 240          # seconds per API call before treating it as hung
MAX_TURNS = 24                # hard cap on model round-trips per agent run
MAX_CONTINUATIONS = 3         # MAX_TOKENS "continue where you left off" retries
MAX_MALFORMED_RETRIES = 4
DEFAULT_MAX_OUTPUT_TOKENS = 65536

# One executor for all Gemini calls. futures are awaited with a timeout and, on
# expiry, abandoned — the hung call finishes on its own thread without blocking
# the agent (the previous per-call `with ThreadPoolExecutor` blocked on exit).
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=32, thread_name_prefix="gemini")

FILE_GEN_TOOLS = frozenset({
    "execute_python", "create_word_document", "create_excel", "create_powerpoint",
})


class GeminiTimeout(TimeoutError):
    pass


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (GeminiTimeout, httpx.TransportError)):
        return True
    if isinstance(exc, genai_errors.ServerError):
        return True
    if isinstance(exc, genai_errors.APIError):
        return getattr(exc, "code", None) in (429, 500, 502, 503, 504)
    return False


def _generate_with_retry(client, model, contents, config, max_retries=5):
    """generate_content with exponential backoff on transient failures and a real timeout."""
    for attempt in range(max_retries):
        future = _EXECUTOR.submit(
            client.models.generate_content, model=model, contents=contents, config=config,
        )
        try:
            return future.result(timeout=GEMINI_TIMEOUT)
        except concurrent.futures.TimeoutError:
            exc: Exception = GeminiTimeout(f"Gemini call timed out after {GEMINI_TIMEOUT}s")
        except Exception as e:
            exc = e
        if _is_transient(exc) and attempt < max_retries - 1:
            wait = 2 ** attempt
            log.warning("Gemini transient error (%s) — retry %d/%d in %ds",
                        exc, attempt + 1, max_retries - 1, wait)
            time.sleep(wait)
            continue
        raise exc


# ── Model-generation-aware config ─────────────────────────────────────────────

def model_generation(model: str) -> int:
    """Major Gemini generation (2 for gemini-2.5-*, 3 for gemini-3.x-*). Unknown → 3."""
    m = re.search(r"gemini-(\d+)", model)
    return int(m.group(1)) if m else 3


def make_thinking_config(model: str, phase: str) -> types.ThinkingConfig:
    """phase: 'select' (choose a tool) or 'work' (produce the long output).
    Gemini 3.x uses thinking_level; 2.5 uses a token budget."""
    heavy = "pro" in model.lower()
    if model_generation(model) >= 3:
        return types.ThinkingConfig(thinking_level="low" if phase == "select" else "high")
    if phase == "select":
        return types.ThinkingConfig(thinking_budget=2048 if heavy else 1024)
    return types.ThinkingConfig(thinking_budget=16384 if heavy else 8192)


# ── Tool schema conversion ────────────────────────────────────────────────────

def _convert_schema(json_schema: dict) -> types.Schema:
    """Convert a JSON-Schema dict to a Gemini types.Schema."""
    type_map = {
        "string": "STRING", "integer": "INTEGER", "number": "NUMBER",
        "boolean": "BOOLEAN", "array": "ARRAY", "object": "OBJECT",
    }
    kwargs = {"type": type_map.get(json_schema.get("type", "string"), "STRING")}
    if "description" in json_schema:
        kwargs["description"] = json_schema["description"]
    if "properties" in json_schema:
        kwargs["properties"] = {k: _convert_schema(v) for k, v in json_schema["properties"].items()}
    if "required" in json_schema:
        kwargs["required"] = json_schema["required"]
    if "items" in json_schema:
        kwargs["items"] = _convert_schema(json_schema["items"])
    if "enum" in json_schema:
        kwargs["enum"] = json_schema["enum"]
    return types.Schema(**kwargs)


def convert_tools_to_gemini(tools: list[dict]) -> list[types.FunctionDeclaration]:
    declarations = []
    for tool in tools:
        input_schema = tool.get("input_schema", {})
        params = _convert_schema(input_schema) if input_schema.get("properties") else None
        declarations.append(types.FunctionDeclaration(
            name=tool["name"], description=tool.get("description", ""), parameters=params,
        ))
    return declarations


# ── Tool result interpretation ────────────────────────────────────────────────

def tool_result_ok(tool_name: str, result: str) -> bool:
    """Did a file-generating tool actually succeed? Errors and validation
    rejections must not count as a produced file."""
    try:
        data = json.loads(result)
    except (TypeError, ValueError):
        return False
    if not isinstance(data, dict) or data.get("error"):
        return False
    if tool_name == "execute_python":
        return data.get("exit_code", 1) == 0 and data.get("status") != "needs_revision"
    return data.get("status") == "created"


@dataclass
class AgentResult:
    text: str
    files: list[str] = field(default_factory=list)
    status: str = "success"        # success | failed
    error: str | None = None

    def __str__(self) -> str:      # keeps `str(result)` callers working
        return self.text if self.status == "success" else f"Error: {self.error or self.text}"


# ── Base agent ────────────────────────────────────────────────────────────────

class BaseAgent:
    """Base class for all sub-agents."""

    TIER = "reasoning"             # overridden per agent: "reasoning" | "fast"
    AGENT_KEY = ""                 # for MODEL_OVERRIDE_<AGENT_KEY> env overrides
    DOC_SPEC: dict | None = None   # structural expectations for produced files (doc_validator)

    def __init__(
        self,
        name: str,
        system_prompt: str,
        tools: list[dict] | None = None,
        tool_handlers: dict | None = None,
        model: str | None = None,
        max_output_tokens: int | None = None,
        is_guest: bool = False,
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.tools = tools or []
        self.tool_handlers = tool_handlers or {}
        self.is_guest = is_guest
        # Guest (trial) sessions always run the cheapest model with a tight output cap.
        if is_guest:
            self.model = GUEST_GEMINI_MODEL
            self.max_output_tokens = GUEST_MAX_OUTPUT_TOKENS
        else:
            self.model = model or model_for(self.TIER, self.AGENT_KEY or None)
            self.max_output_tokens = max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS
        self.client = genai.Client(api_key=GEMINI_API_KEY)
        # Populated by the event wrappers (event_orchestrator._wrap_tool_handlers)
        # as files are created, so the caller gets an exact file list back.
        self.files_created: list[str] = []

    # ── config helpers ───────────────────────────────────────────────────────

    def _config(self, gemini_tools, phase: str, tool_config=None) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=self.system_prompt,
            tools=gemini_tools,
            tool_config=tool_config,
            thinking_config=make_thinking_config(self.model, phase),
            max_output_tokens=self.max_output_tokens,
        )

    @staticmethod
    def _force(names: list[str] | None = None) -> types.ToolConfig:
        cfg = {"mode": "ANY"}
        if names:
            cfg["allowed_function_names"] = names
        return types.ToolConfig(function_calling_config=types.FunctionCallingConfig(**cfg))

    # ── main loop ────────────────────────────────────────────────────────────

    def run(self, task: str, context: str = "") -> AgentResult:
        user_content = f"Context:\n{context}\n\nTask:\n{task}" if context else task
        contents = [types.Content(role="user", parts=[types.Part.from_text(text=user_content)])]

        gemini_tools = None
        if self.tools:
            gemini_tools = [types.Tool(function_declarations=convert_tools_to_gemini(self.tools))]

        tool_names = [t["name"] for t in self.tools]
        file_tools = [n for n in tool_names if n in FILE_GEN_TOOLS]

        # First call must be a tool call — research first when the agent can search.
        if gemini_tools and "knowledge_search" in tool_names:
            first_cfg = self._config(gemini_tools, "select", self._force(["knowledge_search"]))
        elif gemini_tools:
            first_cfg = self._config(gemini_tools, "select", self._force())
        else:
            first_cfg = self._config(None, "work")
        work_cfg = self._config(gemini_tools, "work")

        file_generated = False
        nudge_sent = False
        malformed_retries = 0
        continuations = 0
        override_cfg = None
        turn = 0

        while True:
            turn += 1
            if turn > MAX_TURNS:
                return self._fail(f"exceeded {MAX_TURNS} model turns without finishing", file_generated)

            active_cfg = override_cfg or (first_cfg if turn == 1 else work_cfg)
            override_cfg = None
            log.info("[%s] turn %d model=%s", self.name, turn, self.model)

            try:
                response = _generate_with_retry(self.client, self.model, contents, active_cfg)
            except Exception as e:
                log.exception("[%s] Gemini call failed", self.name)
                return self._fail(f"model call failed: {e}", file_generated)

            candidate = response.candidates[0]
            finish_reason = str(getattr(candidate, "finish_reason", ""))

            if "MALFORMED_FUNCTION_CALL" in finish_reason:
                malformed_retries += 1
                log.warning("[%s] MALFORMED_FUNCTION_CALL (%d)", self.name, malformed_retries)
                if malformed_retries > MAX_MALFORMED_RETRIES:
                    return self._fail("repeated malformed tool calls", file_generated)
                retry_allowed = file_tools if file_tools and not file_generated else None
                contents.append(types.Content(role="user", parts=[types.Part.from_text(
                    text=self._malformed_hint(file_tools))]))
                override_cfg = self._config(gemini_tools, "work", self._force(retry_allowed))
                continue

            if "MAX_TOKENS" in finish_reason:
                continuations += 1
                log.warning("[%s] MAX_TOKENS hit (%d)", self.name, continuations)
                if continuations > MAX_CONTINUATIONS:
                    return self._fail("output repeatedly exceeded the token limit", file_generated)
                if candidate.content and candidate.content.parts:
                    contents.append(candidate.content)
                contents.append(types.Content(role="user", parts=[types.Part.from_text(text=(
                    "Your previous response was cut off at the output limit. If you were in the "
                    "middle of a tool call, issue the tool call again in full, splitting the work into "
                    "smaller calls. If you were writing text, continue exactly where you left off "
                    "without repeating content."
                ))]))
                continue

            if candidate.content is None or not candidate.content.parts:
                return self._fail(f"model returned no content (finish_reason={finish_reason})", file_generated)

            contents.append(candidate.content)
            function_calls = [p for p in candidate.content.parts if p.function_call]

            if not function_calls:
                if file_tools and not file_generated and not nudge_sent:
                    nudge_sent = True
                    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=(
                        "You have not yet generated any output files. You MUST call one of the "
                        f"file-generation tools now ({', '.join(file_tools)}) to produce the required "
                        "deliverables. Do NOT respond with text — call the tool directly."
                    ))]))
                    override_cfg = self._config(gemini_tools, "work", self._force(file_tools))
                    continue
                text = "".join(p.text for p in candidate.content.parts if p.text)
                if file_tools and not file_generated:
                    return self._fail("agent finished without producing any file", file_generated, text)
                return AgentResult(text=text, files=list(self.files_created))

            malformed_retries = 0
            response_parts = []
            for part in function_calls:
                fc = part.function_call
                handler = self.tool_handlers.get(fc.name)
                log.info("[%s] tool call: %s", self.name, fc.name)
                if handler:
                    try:
                        result = handler(**dict(fc.args))
                    except Exception as e:
                        log.exception("[%s] tool %s raised", self.name, fc.name)
                        result = json.dumps({"error": f"{type(e).__name__}: {e}"})
                else:
                    result = json.dumps({"error": f"No handler registered for tool '{fc.name}'"})
                result_str = str(result)
                if fc.name in FILE_GEN_TOOLS and tool_result_ok(fc.name, result_str):
                    file_generated = True
                log.info("[%s] tool result (%s): %.150s", self.name, fc.name, result_str)
                response_parts.append(types.Part.from_function_response(
                    name=fc.name, response={"result": result_str},
                ))
            contents.append(types.Content(role="user", parts=response_parts))

    # ── helpers ──────────────────────────────────────────────────────────────

    def _fail(self, reason: str, file_generated: bool, text: str = "") -> AgentResult:
        log.error("[%s] failed: %s", self.name, reason)
        # A partial run may still have produced usable files — report them honestly.
        return AgentResult(text=text, files=list(self.files_created), status="failed", error=reason)

    @staticmethod
    def _malformed_hint(file_tools: list[str]) -> str:
        if "execute_python" in file_tools:
            return (
                "Your previous tool call had malformed JSON arguments — usually a Python code string "
                "that is too long or contains syntax errors. Call execute_python again with valid JSON. "
                "Tips: split into two smaller execute_python calls (e.g. one for Excel, one for Word); "
                "inside f-strings use single quotes for dict keys; avoid deeply nested f-string expressions."
            )
        return (
            "Your previous tool call had malformed JSON arguments. Call the tool again with valid, "
            "well-formed JSON — keep strings free of unescaped quotes and newlines, and if the content "
            "is very long, reduce it or split it across two calls."
        )
