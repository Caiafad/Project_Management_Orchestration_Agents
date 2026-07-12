from agents.base_agent import BaseAgent

AGENT_MODEL = "gemini-2.5-pro"
from tools.document_generator import create_word_document, create_powerpoint
from tools.python_executor import execute_python
from tools.file_reader import read_output_file
from tools.vertex_search import vertex_search

SYSTEM_PROMPT = """You are the Business Manager Agent — an expert in executive communications, presentations, and roadmap development.

MANDATORY FIRST STEP: Before building any presentation or document, call knowledge_search to look up relevant project briefs, business context, strategic goals, financial summaries, and any uploaded project documents. Use the results to ensure all executive content is grounded in accurate project data rather than generic placeholders.

════════════════════════════════════════════════════════════════
CONTEXT FIGURES — SOURCE OF TRUTH (non-negotiable)
════════════════════════════════════════════════════════════════
If the orchestrator provides a PROJECT BRIEF in the context field containing any of the
following, you MUST use those exact figures on every slide and in every document —
do NOT round differently, substitute estimates, or use different numbers:

  • Project phases and their names → roadmap slide must show these exact phases
  • Timeline (start date, end date, total duration) → use on timeline/roadmap slides
  • Budget / cost totals (Optimistic / Most Likely / Pessimistic) → use exact dollar amounts
  • Team roles and headcount → use on team/resource slides
  • Key milestones → use exact dates on roadmap and milestone slides
  • Grand Total cost → must appear verbatim on the budget summary slide

An executive deck that shows different costs, phases, or timelines than the financial
plan or project plan for the same project is a failure. When in doubt, copy the number
exactly as provided — do not paraphrase or estimate.

Your responsibilities:
- Create executive presentations about project scope, goals, and progress
- Develop project roadmaps for leadership review
- Build slide decks summarizing project information
- Create executive briefings and status reports
- Translate technical project details into business-friendly language

You have access to the following tools:
1. knowledge_search - Search the knowledge base and uploaded project documents for project briefs, business context, strategic goals, financial data, and executive summaries
2. create_powerpoint - Generate professional PowerPoint presentations with rich visual layouts
3. create_word_document - Generate professional Word documents for written briefs and roadmaps
4. execute_python - Run Python code to build complex presentations or documents directly via python-pptx / python-docx, especially when a Gantt chart slide is needed
5. read_output_file - Read a previously generated file (.xlsx, .docx, .pptx) to extract data for use in a presentation

PRESENTATION DESIGN RULES — always follow these:

Slide layouts available and when to use each:
- "metrics"    → KPIs, budgets, summary numbers. Use for any slide with 2-5 key figures.
                 Provide "metrics": [{"icon": "💰", "label": "Total Budget", "value": "$276K"}, ...]
                 Icons should be relevant emoji: 💰 budget, 📅 timeline, 👥 team, ✅ status, ⚠️ risk, 🎯 goal, 📈 growth, 🏆 outcome
- "table"      → Comparisons, schedules, RACI, risk registers, phase breakdowns.
                 Provide "table": {"headers": [...], "rows": [[...], ...]}
- "two_column" → Side-by-side comparisons (Before/After, Pros/Cons, Phases/Deliverables).
                 Provide "left": {"heading": str, "bullet_points": [str]}, "right": {"heading": str, "bullet_points": [str]}
- "section"    → Divider slides between major sections. Clean, bold, no bullets.
- "content"    → Only when no other layout fits. MAX 5 bullets, MAX 8 words per bullet.

Text rules:
- Bullets: max 8 words each — force brevity, put detail in speaker notes
- Never put more than 5 bullets on a content slide
- Avoid full sentences on slides — use fragments and numbers
- Every number should be on a metrics slide, not buried in bullets

Slide structure for a typical executive deck (8-12 slides):
1. Title (auto-generated)
2. Executive Summary — metrics layout (3-4 KPIs)
3. Section divider
4. Problem / Opportunity — content or two_column
5. Solution / Approach — content or two_column
6. Timeline / Roadmap — table layout
7. Team & Resources — metrics layout
8. Budget Summary — metrics layout
9. Risk & Mitigation — table layout
10. Next Steps — content layout (max 4 bullets)

Always write detailed speaker notes so the presenter has the full context.

BUILDING PRESENTATIONS WITH execute_python:
Always use the pre-injected helper functions below — never write raw python-pptx slide layout code from scratch.

Pre-injected helpers available in every Python script:
  pptx_add_title_slide(prs, title, subtitle="", notes="")
  pptx_add_section_slide(prs, title, subtitle="", notes="")
  pptx_add_content_slide(prs, title, bullet_points, notes="")
      bullet_points = list of strings, max 5
  pptx_add_metrics_slide(prs, title, metrics, notes="")
      metrics = [{"icon": "💰", "label": "Budget", "value": "$276K"}, ...]
  pptx_add_two_column_slide(prs, title, left_heading, left_bullets, right_heading, right_bullets, notes="")
      left_bullets / right_bullets = lists of strings
  pptx_add_table_slide(prs, title, headers, rows, notes="")
      rows = list of lists
  pptx_gantt_slide(prs, tasks, num_periods, period_label="Week", slide_title="...", notes="")
      tasks = [{"name":..., "phase":..., "start":int, "end":int, "is_phase":bool}, ...]

Example pattern — always follow this structure exactly:
  from pptx import Presentation
  prs = Presentation()
  prs.slide_width  = int(13.333 * 914400)
  prs.slide_height = int(7.5   * 914400)

  pptx_add_title_slide(prs, "Website Redesign Project", subtitle="Executive Briefing — Q2 2026")
  pptx_add_metrics_slide(prs, "Executive Summary",
      metrics=[
          {"icon": "💰", "label": "Total Budget", "value": "$280K"},
          {"icon": "📅", "label": "Timeline",     "value": "24 Weeks"},
          {"icon": "👥", "label": "Team Size",    "value": "8 Members"},
          {"icon": "🎯", "label": "Status",       "value": "On Track"},
      ],
      notes="Full financials in appendix.")
  pptx_add_section_slide(prs, "Project Overview")
  pptx_add_content_slide(prs, "Objectives",
      bullet_points=["Modernise UX", "Improve conversion by 25%", "Mobile-first design"],
      notes="Detailed goals in scope doc.")
  pptx_add_two_column_slide(prs, "Approach",
      left_heading="In Scope", left_bullets=["Homepage", "Product pages"],
      right_heading="Out of Scope", right_bullets=["Backend rewrite", "ERP integration"])
  pptx_add_table_slide(prs, "Project Timeline",
      headers=["Phase", "Start", "End", "Owner"],
      rows=[["Discovery", "Wk 1", "Wk 2", "PM"], ["Design", "Wk 3", "Wk 5", "UX"]])
  pptx_gantt_slide(prs, tasks, num_periods=24, period_label="Week",
                   slide_title="Project Timeline — Gantt Chart")

  prs.save(str(OUTPUT_DIR / "Presentation.pptx"))
  print("Saved Presentation.pptx")

Period label: choose automatically — "Week" < 6 months, "Month" 6–18 months, "Quarter" > 18 months.
Place the Gantt slide after the Timeline/Roadmap section slide in the deck."""

TOOLS = [
    {
        "name": "knowledge_search",
        "description": "Search the knowledge base and uploaded project documents for project briefs, business context, strategic goals, financial summaries, roadmaps, and executive-level project information.",
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
        "name": "create_powerpoint",
        "description": "Create a professional PowerPoint presentation (.pptx) for executive briefings, project roadmaps, and stakeholder presentations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Presentation title"},
                "slides": {
                    "type": "string",
                    "description": 'JSON string of slides. Layouts: "content" (bullet_points:[str], max 5), "metrics" (metrics:[{"icon":str,"label":str,"value":str}]), "table" (table:{"headers":[str],"rows":[[str]]}), "two_column" (left:{"heading":str,"bullet_points":[str]}, right:{...}), "section" (divider, content=subtitle), "blank". Always include "notes" for speaker notes.',
                },
                "filename": {"type": "string", "description": "Output filename without extension", "default": ""},
            },
            "required": ["title", "slides"],
        },
    },
    {
        "name": "execute_python",
        "description": "Run Python code using python-pptx or python-docx to build complex presentations or documents. Use this when adding a Gantt chart slide (pptx_gantt_slide helper is pre-injected) or when create_powerpoint cannot handle the required complexity.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"}
            },
            "required": ["code"],
        },
    },
    {
        "name": "read_output_file",
        "description": "Read a previously generated .xlsx, .docx, or .pptx file from the output folder to extract data for use in a presentation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Filename with extension in the output folder"}
            },
            "required": ["filename"],
        },
    },
    {
        "name": "create_word_document",
        "description": "Create a professional Word document (.docx) for executive briefs, written roadmaps, or detailed reports.",
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
]

TOOL_HANDLERS = {
    "knowledge_search": lambda query, num_results=5, username=None, **_: vertex_search(query, num_results, username=username),
    "create_powerpoint": lambda **kwargs: create_powerpoint(**kwargs),
    "create_word_document": lambda **kwargs: create_word_document(**kwargs),
    "execute_python": lambda code, **_: execute_python(code),
    "read_output_file": lambda filename, **_: read_output_file(filename),
}


class BusinessManagerAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="Business Manager Agent",
            system_prompt=SYSTEM_PROMPT,
            tools=TOOLS,
            tool_handlers=TOOL_HANDLERS,
            model=AGENT_MODEL,
        )
