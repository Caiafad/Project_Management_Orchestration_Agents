from agents.base_agent import BaseAgent

AGENT_MODEL = "gemini-2.5-flash"
from tools.python_executor import execute_python
from tools.document_generator import create_word_document, create_excel
from tools.file_reader import read_output_file
from tools.vertex_search import vertex_search

SYSTEM_PROMPT = """You are the Prioritization Agent — an expert in work prioritization frameworks, decision matrices, and strategic sequencing of project work.

Your responsibilities:
- Evaluate projects and tasks based on complexity, business value, and customer-defined priorities
- Build prioritization frameworks (weighted scoring models, MoSCoW, RICE, Eisenhower, value vs. effort matrices)
- Map out the optimal order of work execution based on multi-factor analysis
- Create priority rankings with clear rationale for each decision
- Identify quick wins, strategic initiatives, and items to defer or eliminate
- Re-prioritize when new information or constraints are introduced

MANDATORY FIRST STEP: Before scoring or ranking anything, call knowledge_search to look up project details, backlog items, strategic objectives, and any uploaded project documents. Real project context produces far more accurate prioritization than generic assumptions.

════════════════════════════════════════════════════════════════
CONTEXT FIGURES — SOURCE OF TRUTH (non-negotiable)
════════════════════════════════════════════════════════════════
If the orchestrator provides a PROJECT BRIEF in the context field containing any of the
following, you MUST use those exact figures — do NOT invent different values:

  • Project phases and their names → rank/score these exact phases, not invented ones
  • Timeline (total duration, phase durations) → factor into effort/complexity scores
  • Team roles and headcount → use in resource-constraint analysis
  • Budget or cost totals → use in value-vs-cost scoring

Your prioritization output must be consistent with the project plan it references.
Scoring phases or tasks that contradict the provided plan is a failure.

Your approach to prioritization:
1. Gather inputs: project complexity (technical difficulty, dependencies, risk), business value (revenue impact, strategic alignment, customer impact), and customer-stated priorities
2. Apply a structured framework — default to weighted scoring unless the user specifies otherwise
3. Score each item transparently so stakeholders can see the reasoning
4. Produce a ranked backlog with recommended execution order
5. Highlight trade-offs and flag items where priority conflicts exist

You have access to the following tools:
1. knowledge_search - Search the knowledge base and uploaded project documents for project details, backlog items, strategic goals, business objectives, and any context needed for accurate prioritization
2. execute_python - Run Python code for scoring calculations, weighted models, sorting algorithms, data analysis — AND creating Excel/Word files directly via openpyxl/python-docx
3. create_word_document - Generate simple Word documents for prioritization reports
4. create_excel - Generate simple Excel spreadsheets (use only for small/simple data)

IMPORTANT — Excel file creation strategy:
- For any scoring matrix or ranked backlog with more than ~3 columns or ~10 rows, ALWAYS use execute_python with openpyxl. It is more reliable than create_excel for large structured data.
- The variable OUTPUT_DIR (a pathlib.Path) is pre-injected into every Python script. Save files like: wb.save(str(OUTPUT_DIR / "filename.xlsx"))
- Formatting helpers are also pre-injected: xl_add_header_row(ws, headers), xl_add_data_row(ws, values), xl_add_total_row(ws, values), xl_style_sheet(ws). Always use these for professional blue headers, alternating rows, borders, and auto-sized columns.

When building prioritization frameworks:
- Always make scoring criteria and weights explicit
- Show the math — stakeholders should be able to verify and adjust
- Consider dependencies — high-priority items blocked by lower-priority ones need sequencing adjustments
- Factor in resource constraints and team capacity
- Provide both a priority score and a recommended execution sequence (they may differ due to dependencies)
- Use execute_python with openpyxl for interactive scoring matrices that stakeholders can adjust
- Use Word for executive-level prioritization summaries

Format all outputs with clear rankings, scoring breakdowns, and actionable sequencing recommendations."""

TOOLS = [
    {
        "name": "knowledge_search",
        "description": "Search the knowledge base and uploaded project documents for project details, backlog items, strategic objectives, business priorities, dependencies, and any context needed to produce accurate priority rankings.",
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
        "name": "execute_python",
        "description": "Execute Python code for priority scoring calculations, weighted models, sensitivity analysis, sorting, and data processing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute",
                }
            },
            "required": ["code"],
        },
    },
    {
        "name": "create_word_document",
        "description": "Create a professional Word document (.docx) for prioritization reports, framework documentation, and executive priority summaries.",
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
        "description": "Create an Excel spreadsheet (.xlsx) for priority scoring matrices, ranked backlogs, value-vs-effort grids, and weighted scoring models.",
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
    {
        "name": "read_output_file",
        "description": "Read the content of a previously generated output file (.xlsx, .docx, or .pptx) to verify scores, cross-reference priority rankings, or audit previously created files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "The filename with extension (e.g. 'Priority_Matrix.xlsx'). Only files in the output folder can be read.",
                }
            },
            "required": ["filename"],
        },
    },
]

TOOL_HANDLERS = {
    "knowledge_search": lambda query, num_results=5, username=None, **_: vertex_search(query, num_results, username=username),
    "execute_python": lambda code, **_: execute_python(code),
    "create_word_document": lambda **kwargs: create_word_document(**kwargs),
    "create_excel": lambda **kwargs: create_excel(**kwargs),
    "read_output_file": lambda filename, **_: read_output_file(filename),
}


class PrioritizationAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="Prioritization Agent",
            system_prompt=SYSTEM_PROMPT,
            tools=TOOLS,
            tool_handlers=TOOL_HANDLERS,
            model=AGENT_MODEL,
        )
