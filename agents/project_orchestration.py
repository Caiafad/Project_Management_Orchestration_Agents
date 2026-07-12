from agents.base_agent import BaseAgent

AGENT_MODEL = "gemini-2.5-flash"
from tools.document_generator import create_word_document, create_excel
from tools.vertex_search import vertex_search

SYSTEM_PROMPT = """You are the Project Orchestration Agent — an expert in team management, role assignment, and internal project organization.

MANDATORY FIRST STEP: Before creating any documents, call knowledge_search to look up relevant project context, team information, org charts, role definitions, and any uploaded project documents. This ensures your RACI matrices and team assignments reflect the actual project structure and personnel.

════════════════════════════════════════════════════════════════
CONTEXT FIGURES — SOURCE OF TRUTH (non-negotiable)
════════════════════════════════════════════════════════════════
If the orchestrator provides a PROJECT BRIEF in the context field containing any of the
following, you MUST use those exact figures — do NOT invent different values:

  • Project phases and their names → your RACI rows must map to these exact phases
  • Team roles and headcount → use these exact roles; do not add or remove roles
  • Timeline (start date, end date, total duration) → use in task scheduling and workload tables
  • Key milestones → assign ownership in the RACI for each milestone

Your team documentation must be consistent with the project plan and financial plan
produced for the same project. Contradicting provided roles, phases, or dates is a failure.

Your responsibilities:
- Assign team members to roles, tasks, and timelines
- Create RACI matrices (Responsible, Accountable, Consulted, Informed)
- Draft internal project documentation including role descriptions
- Build structures of accountability for the project team
- Create task assignment matrices and workload distributions
- Develop team onboarding documentation for the project

You have access to the following tools:
1. knowledge_search - Search the knowledge base and uploaded project documents for team structures, role definitions, org charts, RACI templates, and project context
2. create_word_document - Generate professional Word documents for internal project documentation
3. create_excel - Generate Excel spreadsheets for RACI matrices, task trackers, and team schedules

When orchestrating projects:
- Match team member skills to task requirements
- Ensure clear accountability — every task has exactly one owner
- Balance workloads across the team
- Define escalation paths and decision-making authority
- Include handoff procedures between dependent tasks
- Create clear timelines with checkpoints

Format all outputs with tables, matrices, and structured content for clarity."""

TOOLS = [
    {
        "name": "knowledge_search",
        "description": "Search the knowledge base and uploaded project documents for team structures, org charts, role definitions, RACI templates, staffing plans, and project context relevant to team orchestration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "num_results": {"type": "integer", "description": "Number of results (max 10)", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "create_word_document",
        "description": "Create a professional Word document (.docx) for RACI matrices, role descriptions, task assignments, or internal project documentation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Document title"},
                "sections": {
                    "type": "string",
                    "description": 'JSON string of sections. Each section: {"heading": str, "level": int, "content": str, "bullet_points": [str], "table": {"headers": [str], "rows": [[str]]}}',
                },
                "filename": {"type": "string", "description": "Output filename without extension", "default": ""},
                "include_toc": {"type": "boolean", "description": "Include table of contents", "default": False},
            },
            "required": ["title", "sections"],
        },
    },
    {
        "name": "create_excel",
        "description": "Create an Excel spreadsheet (.xlsx) for RACI matrices, task assignment trackers, team schedules, and workload distributions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Workbook title"},
                "sheets": {
                    "type": "string",
                    "description": 'JSON string of sheets. Each sheet: {"name": str, "headers": [str], "rows": [[values]], "column_widths": [int], "formulas": [{"cell": "C10", "formula": "=SUM(C2:C9)"}], "freeze_panes": "A2"}',
                },
                "filename": {"type": "string", "description": "Output filename without extension", "default": ""},
            },
            "required": ["title", "sheets"],
        },
    },
]

TOOL_HANDLERS = {
    "knowledge_search": lambda query, num_results=5, username=None, **_: vertex_search(query, num_results, username=username),
    "create_word_document": lambda **kwargs: create_word_document(**kwargs),
    "create_excel": lambda **kwargs: create_excel(**kwargs),
}


class ProjectOrchestrationAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="Project Orchestration Agent",
            system_prompt=SYSTEM_PROMPT,
            tools=TOOLS,
            tool_handlers=TOOL_HANDLERS,
            model=AGENT_MODEL,
        )
