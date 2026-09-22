"""
Shared tool schemas and handler factories for the document-producing agents.

Every schema here is declared with STRUCTURED parameters — `sections`, `sheets`
and `slides` are real arrays of objects, not JSON serialised into a string. The
API then enforces the shape, so the model cannot mis-nest brackets (Pro and
Flash both did so ~1 in 4 calls when asked to hand-serialise JSON).

Agents pass a per-tool description override where their prompt needs domain
wording; everything else is shared.
"""

from __future__ import annotations

from copy import deepcopy

# ── Schemas ──────────────────────────────────────────────────────────────────

_TABLE = {
    "type": "object",
    "description": "Optional table.",
    "properties": {
        "headers": {"type": "array", "items": {"type": "string"}},
        "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}},
                 "description": "Each row must have exactly one value per header."},
    },
    "required": ["headers", "rows"],
}

WORD_SECTION = {
    "type": "object",
    "properties": {
        "heading": {"type": "string", "description": "Section heading text"},
        "level": {"type": "integer", "description": "Heading level 1-4 (default 1)"},
        "content": {"type": "string", "description": "Body text; blank line between paragraphs"},
        "bullet_points": {"type": "array", "items": {"type": "string"}},
        "table": _TABLE,
    },
    "required": ["heading"],
}

EXCEL_SHEET = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Sheet/tab name (max 31 chars)"},
        "headers": {"type": "array", "items": {"type": "string"}},
        "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}},
                 "description": "Data rows. Use plain numbers as strings ('12500', not '$12,500') "
                                "so they stay numeric; one value per header."},
        "column_widths": {"type": "array", "items": {"type": "integer"}},
        "formulas": {"type": "array", "items": {"type": "object", "properties": {
            "cell": {"type": "string", "description": "e.g. 'C10'"},
            "formula": {"type": "string", "description": "e.g. '=SUM(C2:C9)'"},
        }, "required": ["cell", "formula"]}},
        "freeze_panes": {"type": "string", "description": "e.g. 'A2' to freeze the header row"},
    },
    "required": ["name", "headers", "rows"],
}

_COLUMN = {
    "type": "object",
    "properties": {
        "heading": {"type": "string"},
        "bullet_points": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["bullet_points"],
}

PPTX_SLIDE = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "layout": {"type": "string", "enum": ["content", "metrics", "table", "two_column", "section", "blank"]},
        "content": {"type": "string", "description": "Intro sentence above bullets, or subtitle for a section slide"},
        "bullet_points": {"type": "array", "items": {"type": "string"},
                          "description": "Max 5, max 8 words each (content layout)"},
        "metrics": {"type": "array", "items": {"type": "object", "properties": {
            "icon": {"type": "string"}, "label": {"type": "string"}, "value": {"type": "string"},
        }, "required": ["label", "value"]}, "description": "3-5 KPI cards (metrics layout)"},
        "table": _TABLE,
        "left": _COLUMN,
        "right": _COLUMN,
        "notes": {"type": "string", "description": "Speaker notes — always include"},
    },
    "required": ["title", "layout"],
}


def _spec(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties, "required": required}}


def knowledge_search(description: str = "Search the project management knowledge base and any uploaded project documents.") -> dict:
    return _spec("knowledge_search", description, {
        "query": {"type": "string", "description": "Search query"},
        "num_results": {"type": "integer", "description": "Number of results (max 10)", "default": 5},
    }, ["query"])


def execute_python(description: str = "Execute Python code.") -> dict:
    return _spec("execute_python", description, {
        "code": {"type": "string", "description": "Python source to execute"},
    }, ["code"])


def read_output_file(description: str = (
        "Read a previously generated .xlsx, .docx or .pptx from the output folder. Use it to pull exact "
        "figures from another agent's file before writing your own.")) -> dict:
    return _spec("read_output_file", description, {
        "filename": {"type": "string", "description": "Filename with extension, e.g. 'Project_Plan.xlsx'"},
    }, ["filename"])


def create_word_document(description: str = "Create a professional Word document (.docx).") -> dict:
    return _spec("create_word_document", description, {
        "title": {"type": "string", "description": "Document title"},
        "sections": {"type": "array", "items": deepcopy(WORD_SECTION), "description": "Document sections in order"},
        "filename": {"type": "string", "description": "Output filename without extension", "default": ""},
    }, ["title", "sections"])


def create_excel(description: str = "Create an Excel workbook (.xlsx).") -> dict:
    return _spec("create_excel", description, {
        "title": {"type": "string", "description": "Workbook title"},
        "sheets": {"type": "array", "items": deepcopy(EXCEL_SHEET), "description": "Sheets in order"},
        "filename": {"type": "string", "description": "Output filename without extension", "default": ""},
    }, ["title", "sheets"])


def create_powerpoint(description: str = "Create a professional PowerPoint presentation (.pptx).") -> dict:
    return _spec("create_powerpoint", description, {
        "title": {"type": "string", "description": "Presentation title"},
        "slides": {"type": "array", "items": deepcopy(PPTX_SLIDE), "description": "Slides in order"},
        "filename": {"type": "string", "description": "Output filename without extension", "default": ""},
    }, ["title", "slides"])


# ── Handlers ─────────────────────────────────────────────────────────────────

def standard_handlers(tool_names: list[str]) -> dict:
    """Default handler map for the given tool names. Import lazily so agents that
    don't use a tool never import its dependencies."""
    from tools.vertex_search import vertex_search
    from tools.python_executor import execute_python as _exec
    from tools.file_reader import read_output_file as _read
    from tools import document_generator as dg

    catalogue = {
        "knowledge_search": lambda query, num_results=5, username=None, **_: vertex_search(query, num_results, username=username),
        "execute_python": lambda code, **_: _exec(code),
        "read_output_file": lambda filename, **_: _read(filename),
        "create_word_document": lambda **kw: dg.create_word_document(**kw),
        "create_excel": lambda **kw: dg.create_excel(**kw),
        "create_powerpoint": lambda **kw: dg.create_powerpoint(**kw),
    }
    return {n: catalogue[n] for n in tool_names}
