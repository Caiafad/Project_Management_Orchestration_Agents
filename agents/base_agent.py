import time
import concurrent.futures
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, GEMINI_MODEL

_GEMINI_TIMEOUT = 240  # seconds per API call before treating as hung


def _generate_with_retry(client, model, contents, config, max_retries=5):
    """Call generate_content with exponential backoff on transient network errors.
    Each call is wrapped in a 120-second thread timeout to prevent indefinite hangs
    (which occur in Cloud Run when the Gemini TCP connection silently stalls).
    """
    for attempt in range(max_retries):
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(
                    client.models.generate_content,
                    model=model,
                    contents=contents,
                    config=config,
                )
                try:
                    return future.result(timeout=_GEMINI_TIMEOUT)
                except concurrent.futures.TimeoutError:
                    raise TimeoutError(
                        f"Gemini API call timed out after {_GEMINI_TIMEOUT}s"
                    )
        except Exception as e:
            err = str(e)
            transient = any(k in err for k in (
                "RemoteProtocolError", "Server disconnected",
                "Connection reset", "ConnectionError", "TimeoutError",
                "Stream idle", "partial response", "stream",
                "503", "502", "429", "500",
            ))
            if transient and attempt < max_retries - 1:
                wait = 2 ** attempt  # 1s, 2s, 4s, 8s, 16s
                time.sleep(wait)
                continue
            raise


def _convert_schema(json_schema: dict) -> types.Schema:
    """Convert a JSON Schema dict (from Anthropic format) to a Gemini types.Schema."""
    type_map = {
        "string": "STRING",
        "integer": "INTEGER",
        "number": "NUMBER",
        "boolean": "BOOLEAN",
        "array": "ARRAY",
        "object": "OBJECT",
    }
    schema_type = type_map.get(json_schema.get("type", "string"), "STRING")

    kwargs = {"type": schema_type}

    if "description" in json_schema:
        kwargs["description"] = json_schema["description"]

    if "properties" in json_schema:
        kwargs["properties"] = {
            k: _convert_schema(v) for k, v in json_schema["properties"].items()
        }

    if "required" in json_schema:
        kwargs["required"] = json_schema["required"]

    if "items" in json_schema:
        kwargs["items"] = _convert_schema(json_schema["items"])

    if "enum" in json_schema:
        kwargs["enum"] = json_schema["enum"]

    return types.Schema(**kwargs)


def convert_tools_to_gemini(tools: list[dict]) -> list[types.FunctionDeclaration]:
    """Convert a list of Anthropic-style tool dicts to Gemini FunctionDeclarations."""
    declarations = []
    for tool in tools:
        input_schema = tool.get("input_schema", {})
        params = _convert_schema(input_schema) if input_schema.get("properties") else None
        declarations.append(
            types.FunctionDeclaration(
                name=tool["name"],
                description=tool.get("description", ""),
                parameters=params,
            )
        )
    return declarations


class BaseAgent:
    """Base class for all sub-agents. Provides an agentic tool-calling loop via the Gemini SDK."""

    def __init__(
        self,
        name: str,
        system_prompt: str,
        tools: list[dict] | None = None,
        tool_handlers: dict | None = None,
        model: str | None = None,
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.tools = tools or []
        self.tool_handlers = tool_handlers or {}
        self.model = model or GEMINI_MODEL
        self.client = genai.Client(api_key=GEMINI_API_KEY)

    def run(self, task: str, context: str = "") -> str:
        """Execute the agent with an agentic tool-calling loop until a final text response."""
        user_content = task
        if context:
            user_content = f"Context:\n{context}\n\nTask:\n{task}"

        contents = [types.Content(role="user", parts=[types.Part.from_text(text=user_content)])]

        # Build config
        gemini_tools = None
        if self.tools:
            gemini_tools = [types.Tool(function_declarations=convert_tools_to_gemini(self.tools))]

        # Force first call to use a tool (prevents model from answering from memory).
        # If the agent has knowledge_search, restrict the first forced call to that
        # tool specifically — this guarantees research happens before file generation,
        # matching the behaviour seen when running locally in AUTO mode.
        has_search = gemini_tools and any(
            t["name"] == "knowledge_search" for t in self.tools
        )
        if has_search:
            first_tool_config = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode="ANY",
                    allowed_function_names=["knowledge_search"],
                )
            )
        elif gemini_tools:
            first_tool_config = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="ANY")
            )
        else:
            first_tool_config = None

        # Thinking budget:
        #   - First call (tool selection): 1 024 tokens — enough to pick a tool,
        #     not so much it hangs for minutes.
        #   - Subsequent calls (document/code generation): 4 096 tokens — gives the
        #     model enough reasoning headroom to produce complete, untruncated sections.
        #     Without sufficient thinking budget, complex documents (9-section scope docs,
        #     multi-sheet Excel scripts) get cut off mid-sentence or mid-section.
        thinking_cfg_first = types.ThinkingConfig(thinking_budget=1024)
        thinking_cfg       = types.ThinkingConfig(thinking_budget=4096)

        # max_output_tokens: Gemini 2.5 Pro supports up to 65 536 output tokens.
        # Without this cap the SDK uses the model default (~8 192), which is too low
        # for large Word documents, multi-sheet Excel scripts, or PowerPoint JSON.
        MAX_OUTPUT_TOKENS = 65536

        forced_config = types.GenerateContentConfig(
            system_instruction=self.system_prompt,
            tools=gemini_tools,
            tool_config=first_tool_config,
            thinking_config=thinking_cfg_first,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        # Subsequent calls use AUTO so model can give final text response
        config = types.GenerateContentConfig(
            system_instruction=self.system_prompt,
            tools=gemini_tools,
            thinking_config=thinking_cfg,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )

        # Tools that produce output files — the agent must call at least one
        # of these before it is allowed to return a plain text response.
        FILE_GEN_TOOLS = frozenset({
            "execute_python", "create_word_document",
            "create_excel", "create_powerpoint",
        })
        agent_file_tools = [t["name"] for t in self.tools if t["name"] in FILE_GEN_TOOLS]
        file_generated = False   # flips True as soon as any file-gen tool is called
        output_nudge_sent = False  # prevent infinite loops

        malformed_retries = 0
        MAX_MALFORMED_RETRIES = 4
        is_first_call = True
        override_config = None   # used for the one-time forced-output call

        while True:
            if override_config is not None:
                active_config = override_config
                override_config = None
            else:
                active_config = forced_config if is_first_call else config
            is_first_call = False
            print(f"[{self.name}] Calling Gemini (forced={active_config is forced_config})", flush=True)
            response = _generate_with_retry(self.client, self.model, contents, active_config)

            candidate = response.candidates[0]
            finish_reason = str(getattr(candidate, "finish_reason", ""))

            # Handle malformed function call — force a retry with a valid tool call
            if "MALFORMED_FUNCTION_CALL" in finish_reason:
                malformed_retries += 1
                print(f"[{self.name}] MALFORMED_FUNCTION_CALL (attempt {malformed_retries})", flush=True)
                if malformed_retries > MAX_MALFORMED_RETRIES:
                    # Return a clear error string — do NOT ask for plain text, which causes
                    # the model to write a polite summary instead of flagging the failure.
                    print(f"[{self.name}] MALFORMED exceeded retries — returning error", flush=True)
                    return "Error: Agent could not generate the required files due to a repeated tool formatting issue. Please try again."
                # Retry: restrict to file-gen tools only so the model cannot fall back
                # to knowledge_search (which was already done and wastes the retry).
                # If no file-gen tools exist, allow any tool.
                retry_allowed = agent_file_tools if agent_file_tools and not file_generated else None
                retry_tool_config = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode="ANY",
                        **({"allowed_function_names": retry_allowed} if retry_allowed else {}),
                    )
                )
                forced_retry_config = types.GenerateContentConfig(
                    system_instruction=self.system_prompt,
                    tools=gemini_tools,
                    tool_config=retry_tool_config,
                    thinking_config=thinking_cfg,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                )
                contents.append(types.Content(role="user", parts=[types.Part.from_text(
                    text=(
                        "Your previous tool call had malformed JSON arguments — this is usually caused by "
                        "a Python code string that is too long or contains syntax errors. "
                        "Call execute_python again with valid JSON. Tips to avoid the error:\n"
                        "- Split into two separate execute_python calls (one for Excel, one for Word)\n"
                        "- Inside f-strings use single quotes for dict keys: f\"{row['cost']:.2f}\" not f\"{row[\\\"cost\\\"]:.2f}\"\n"
                        "- Avoid deeply nested expressions in f-strings"
                    )
                )]))
                override_config = forced_retry_config
                continue

            # Guard: content can be None if the response was blocked or empty
            if candidate.content is None or not candidate.content.parts:
                return f"[Agent stopped: finish_reason={finish_reason}]"

            # Append the assistant response to conversation
            contents.append(candidate.content)

            # Check if any part has a function_call
            function_calls = [
                part for part in candidate.content.parts if part.function_call
            ]

            if not function_calls:
                # If this agent is supposed to produce files but hasn't yet,
                # inject one forced call so it can't slip away with just text.
                if agent_file_tools and not file_generated and not output_nudge_sent:
                    output_nudge_sent = True
                    nudge = (
                        "You have not yet generated any output files. "
                        "You MUST call one of the file-generation tools now "
                        f"({', '.join(agent_file_tools)}) to produce the required deliverables. "
                        "Do NOT respond with text — call the tool directly."
                    )
                    contents.append(types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=nudge)],
                    ))
                    override_config = types.GenerateContentConfig(
                        system_instruction=self.system_prompt,
                        tools=gemini_tools,
                        tool_config=types.ToolConfig(
                            function_calling_config=types.FunctionCallingConfig(
                                mode="ANY",
                                allowed_function_names=agent_file_tools,
                            )
                        ),
                        thinking_config=thinking_cfg,
                        max_output_tokens=MAX_OUTPUT_TOKENS,
                    )
                    continue  # loop back with forced file-generation call

                # No more tool calls — extract and return text
                return "".join(
                    part.text for part in candidate.content.parts if part.text
                )

            # Successful tool call round — reset the malformed counter so transient
            # MALFORMED errors on one tool don't bleed into future calls.
            malformed_retries = 0

            # Process each function call and add results
            function_response_parts = []
            for part in function_calls:
                fc = part.function_call
                if fc.name in FILE_GEN_TOOLS:
                    file_generated = True
                print(f"[{self.name}] Tool call: {fc.name}", flush=True)
                handler = self.tool_handlers.get(fc.name)
                if handler:
                    result = handler(**dict(fc.args))
                else:
                    result = f"Error: No handler registered for tool '{fc.name}'"
                print(f"[{self.name}] Tool result ({fc.name}): {str(result)[:150]}", flush=True)
                function_response_parts.append(
                    types.Part.from_function_response(
                        name=fc.name,
                        response={"result": str(result)},
                    )
                )

            contents.append(types.Content(role="user", parts=function_response_parts))
