"""
Project Management Orchestrator

The main orchestrator agent that serves as the "project manager." It intakes user
requests, determines the appropriate specialty needed, and delegates to the right
sub-agent through the MCP server tools.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from google import genai
from google.genai import types
from config import GEMINI_API_KEY, GEMINI_MODEL
from agents.base_agent import convert_tools_to_gemini, _generate_with_retry

ORCHESTRATOR_SYSTEM_PROMPT = """You are a senior Project Management professional and orchestrator agent. You provide a premium, white-glove project management experience. You are always polite, professional, and strive to deliver a plus-one customer experience — going above and beyond what is asked.

You manage a team of 8 specialized sub-agents. Your role is to:
1. Understand the user's request thoroughly
2. Determine which specialist(s) to engage
3. Delegate tasks with clear instructions and context — including ALL relevant figures from prior agents
4. Synthesize results into a cohesive, professional response
5. Proactively suggest next steps and additional value

CROSS-DOCUMENT CONSISTENCY — mandatory when multiple agents are engaged:
When one agent's output feeds another, you MUST extract the key figures from the first
agent's result and pass them in the context field of every downstream agent using the
PROJECT BRIEF format below. Every agent is instructed to treat this brief as the source
of truth — but only if you actually populate it with the correct numbers.

PROJECT BRIEF FORMAT — copy this block into the context field and fill every field:
────────────────────────────────────────────────────────────────
PROJECT BRIEF
Project Name: [exact name]
Total Duration: [X months] ([start date] → [end date])
Phases:
  1. [Phase Name] — [duration] ([start] → [end])
  2. [Phase Name] — [duration] ([start] → [end])
  ... (list every phase)
Team Roles: [Role: headcount, Role: headcount, ...]
Key Milestones:
  - [Milestone name]: [date]
  - [Milestone name]: [date]
Budget (if available):
  Optimistic Total:    $[amount]
  Most Likely Total:   $[amount]
  Pessimistic Total:   $[amount]
  Contingency Rate:    [X]%
  Grand Total (ML):    $[amount]
────────────────────────────────────────────────────────────────

RULES:
- Never leave a field as a placeholder — fill it from the prior agent's output.
- Pass this full PROJECT BRIEF to EVERY subsequent agent, not just the next one.
- If a field is genuinely unknown (e.g. budget not yet calculated), write "TBD".
- Documents that contradict each other on phases, timeline, team, or costs are a failure.

YOUR SPECIALIST TEAM:

1. **Project Planning Agent** (delegate_to_project_planning)
   - Project breakdown, WBS, Gantt charts, dependencies, critical path
   - Cost estimation and timeline development
   - Generates Word documents for formal project plans
   - Has Python execution and knowledge base search capabilities

2. **Scope Definition Agent** (delegate_to_scope_definition)
   - In/out scope definition, BRDs, requirements documents
   - Scope change management and acceptance criteria
   - Generates Word documents for scope deliverables

3. **Project Orchestration Agent** (delegate_to_project_orchestration)
   - Team role assignments, RACI matrices, task allocation
   - Internal documentation, accountability structures
   - Generates Word documents for team documentation

4. **Business Manager Agent** (delegate_to_business_manager)
   - Executive presentations, roadmaps, leadership briefings
   - Business-friendly project summaries
   - Generates both PowerPoint and Word documents

5. **Financial Manager Agent** (delegate_to_financial_manager)
   - Cost calculations, budget breakdowns, ROI analysis
   - Financial documentation and projections
   - Has Python execution and knowledge base search capabilities
   - ALWAYS generates a multi-sheet Excel workbook (.xlsx) as primary deliverable
   - Also generates Word documents for narrative financial reports

6. **Internal Communications Agent** (delegate_to_internal_comms)
   - Team emails via Gmail, Slack messages
   - Scrum master duties, standup facilitation
   - Dependency coordination between team members

7. **External Communications Agent** (delegate_to_external_comms)
   - Client/vendor/stakeholder emails via Gmail
   - Professional external correspondence
   - Works with Business Manager outputs

8. **Prioritization Agent** (delegate_to_prioritization)
   - Evaluates work based on complexity, business value, and customer priorities
   - Builds prioritization frameworks (weighted scoring, MoSCoW, RICE, Eisenhower, value vs. effort)
   - Maps optimal execution order considering dependencies and constraints
   - Generates Excel scoring matrices and Word priority reports
   - Has Python execution for scoring calculations

ROUTING GUIDELINES:

- For project breakdowns, timelines, or estimates → Project Planning
- For scope documents, BRDs, or requirements → Scope Definition
- For team assignments, roles, or RACI → Project Orchestration
- For executive presentations or roadmaps → Business Manager
- For budgets, costs, or financial analysis → Financial Manager
- For team emails, Slack messages, or standups → Internal Communications
- For client/vendor/partner emails → External Communications
- For prioritizing work, ranking tasks, or deciding execution order → Prioritization

MULTI-AGENT COLLABORATION:
Some requests may require multiple specialists. For example:
- "Create a full project plan with budget" → Project Planning + Financial Manager
- "Build an executive deck from our scope doc" → Scope Definition + Business Manager
- "Assign the team and notify them" → Project Orchestration + Internal Communications
- "Draft a client update based on our roadmap" → Business Manager + External Communications
- "Plan the project and prioritize the phases" → Project Planning + Prioritization
- "Prioritize the backlog and assign the team" → Prioritization + Project Orchestration

When delegating to multiple agents, pass relevant context from one agent's output to the next.

RESPONSE STYLE:
- Always be warm, professional, and proactive
- Summarize what you did and what was produced
- Highlight any documents that were generated (Word, PowerPoint) and where they were saved
- Suggest logical next steps the user might want to take
- If anything is unclear, ask clarifying questions before proceeding
- Use clear formatting with headers and bullet points

CRITICAL — FAILURE REPORTING (non-negotiable):
- If any agent result contains "AGENT_FAILED" or starts with "Error:", that agent FAILED and produced NO files.
- You MUST explicitly tell the user which agent failed and that the corresponding documents were NOT generated.
- NEVER describe, summarize, or mention files from a failed agent as if they exist.
- NEVER present a complete success summary when any agent failed.
- Example correct response when planning fails: "The Scope Document was created successfully. However, the Project Planning Agent encountered a technical issue and the project plan files were NOT generated. Please try requesting the project plan again." """

# Tool definitions for the orchestrator — these map to the MCP server tools
ORCHESTRATOR_TOOLS = [
    {
        "name": "delegate_to_project_planning",
        "description": "Delegate to the Project Planning Agent for project breakdowns, Gantt charts, dependencies, timelines, cost estimation, and project path development. This agent can execute Python code, search the knowledge base, and generate Word documents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The project planning task to perform"},
                "context": {"type": "string", "description": "Additional context for the task", "default": ""},
            },
            "required": ["task"],
        },
    },
    {
        "name": "delegate_to_scope_definition",
        "description": "Delegate to the Scope Definition Agent for defining project scope, creating BRDs, requirements documents, and in/out scope definitions. This agent can generate Word documents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The scope definition task to perform"},
                "context": {"type": "string", "description": "Additional context for the task", "default": ""},
            },
            "required": ["task"],
        },
    },
    {
        "name": "delegate_to_project_orchestration",
        "description": "Delegate to the Project Orchestration Agent for team role assignments, RACI matrices, task allocation, and internal project documentation. This agent can generate Word documents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The orchestration task to perform"},
                "context": {"type": "string", "description": "Additional context such as team member information", "default": ""},
            },
            "required": ["task"],
        },
    },
    {
        "name": "delegate_to_business_manager",
        "description": "Delegate to the Business Manager Agent for executive presentations, project roadmaps, and leadership briefings. This agent can generate PowerPoint presentations and Word documents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The business/executive communication task to perform"},
                "context": {"type": "string", "description": "Additional context such as project details or audience", "default": ""},
            },
            "required": ["task"],
        },
    },
    {
        "name": "delegate_to_financial_manager",
        "description": "Delegate to the Financial Manager Agent for cost calculations, budget breakdowns, ROI analysis, and financial documentation. This agent ALWAYS produces a multi-sheet Excel workbook (.xlsx) as its primary deliverable — it can execute Python code, search the knowledge base, and generate both Excel spreadsheets and Word documents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The financial analysis task to perform"},
                "context": {"type": "string", "description": "Additional context such as rates, team size, or constraints", "default": ""},
            },
            "required": ["task"],
        },
    },
    {
        "name": "delegate_to_internal_comms",
        "description": "Delegate to the Internal Communications Agent for team emails (Gmail), Slack messages, standup facilitation, and dependency coordination. Acts as a scrum master.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The internal communication task to perform"},
                "context": {"type": "string", "description": "Additional context such as team contacts or project status", "default": ""},
            },
            "required": ["task"],
        },
    },
    {
        "name": "delegate_to_external_comms",
        "description": "Delegate to the External Communications Agent for client, vendor, and stakeholder emails via Gmail. Handles professional external correspondence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The external communication task to perform"},
                "context": {"type": "string", "description": "Additional context such as stakeholder details or prior correspondence", "default": ""},
            },
            "required": ["task"],
        },
    },
    {
        "name": "delegate_to_prioritization",
        "description": "Delegate to the Prioritization Agent for ranking work items, building prioritization frameworks (weighted scoring, MoSCoW, RICE, Eisenhower, value vs. effort), and determining optimal execution order based on project complexity, business value, and customer priorities. This agent can execute Python code and generate Word documents and Excel spreadsheets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The prioritization task to perform"},
                "context": {"type": "string", "description": "Additional context such as project list, complexity ratings, value assessments, or customer priorities", "default": ""},
            },
            "required": ["task"],
        },
    },
]

# Map tool names to actual sub-agent execution
def _get_tool_handlers():
    """Lazy-load tool handlers to avoid circular imports."""
    from agents.project_planning import ProjectPlanningAgent
    from agents.scope_definition import ScopeDefinitionAgent
    from agents.project_orchestration import ProjectOrchestrationAgent
    from agents.business_manager import BusinessManagerAgent
    from agents.financial_manager import FinancialManagerAgent
    from agents.internal_comms import InternalCommsAgent
    from agents.external_comms import ExternalCommsAgent
    from agents.prioritization import PrioritizationAgent

    return {
        "delegate_to_project_planning": lambda task, context="", **_: ProjectPlanningAgent().run(task, context),
        "delegate_to_scope_definition": lambda task, context="", **_: ScopeDefinitionAgent().run(task, context),
        "delegate_to_project_orchestration": lambda task, context="", **_: ProjectOrchestrationAgent().run(task, context),
        "delegate_to_business_manager": lambda task, context="", **_: BusinessManagerAgent().run(task, context),
        "delegate_to_financial_manager": lambda task, context="", **_: FinancialManagerAgent().run(task, context),
        "delegate_to_internal_comms": lambda task, context="", **_: InternalCommsAgent().run(task, context),
        "delegate_to_external_comms": lambda task, context="", **_: ExternalCommsAgent().run(task, context),
        "delegate_to_prioritization": lambda task, context="", **_: PrioritizationAgent().run(task, context),
    }


def run_orchestrator():
    """Run the interactive orchestrator agent loop."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    tool_handlers = _get_tool_handlers()
    conversation_history = []

    # Convert orchestrator tools to Gemini format
    gemini_tools = [types.Tool(function_declarations=convert_tools_to_gemini(ORCHESTRATOR_TOOLS))]

    config = types.GenerateContentConfig(
        system_instruction=ORCHESTRATOR_SYSTEM_PROMPT,
        tools=gemini_tools,
    )

    print("=" * 70)
    print("  PROJECT MANAGEMENT AGENT")
    print("  Your AI-powered project management team")
    print("=" * 70)
    print()
    print("Welcome! I'm your Project Management orchestrator. I have a team of")
    print("8 specialists ready to help you with planning, scope, team management,")
    print("executive presentations, financials, and communications.")
    print()
    print("Type 'quit' or 'exit' to end the session.")
    print("=" * 70)
    print()

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nThank you for using Project Management Agent. Goodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            print("\nThank you for using Project Management Agent. Goodbye!")
            break

        conversation_history.append(
            types.Content(role="user", parts=[types.Part.from_text(text=user_input)])
        )

        # Agentic loop
        while True:
            response = _generate_with_retry(client, GEMINI_MODEL, conversation_history, config)

            candidate = response.candidates[0]
            conversation_history.append(candidate.content)

            # Check for function calls
            function_calls = [
                part for part in candidate.content.parts if part.function_call
            ]

            if not function_calls:
                # Extract and print the final text response
                text_parts = [part.text for part in candidate.content.parts if part.text]
                if text_parts:
                    print(f"\nOrchestrator: {''.join(text_parts)}\n")
                break

            # Handle tool calls
            function_response_parts = []
            for part in function_calls:
                fc = part.function_call
                agent_name = fc.name.replace("delegate_to_", "").replace("_", " ").title()
                print(f"\n  [Delegating to {agent_name}...]")

                handler = tool_handlers.get(fc.name)
                if handler:
                    try:
                        result = handler(**dict(fc.args))
                    except Exception as e:
                        result = f"Error executing {fc.name}: {str(e)}"
                else:
                    result = f"Error: Unknown tool '{fc.name}'"

                function_response_parts.append(
                    types.Part.from_function_response(
                        name=fc.name,
                        response={"result": str(result)},
                    )
                )

            conversation_history.append(
                types.Content(role="user", parts=function_response_parts)
            )


if __name__ == "__main__":
    run_orchestrator()
