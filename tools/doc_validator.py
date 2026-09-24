"""
Structural validation for generated documents.

The generators only checked that their input parsed. Everything the agent
prompts call "a failure" — a missing Functional Requirements section, a Labor
Detail sheet with no TOTAL row, a heading with no body, "TBD" left in the text,
a Word table whose figures contradict the workbook — still reported
`{"status": "created"}`. This module opens the finished file and checks it.

Issues are returned to the sub-agent as the tool result so its existing loop
fixes them (bounded by MAX_REVISIONS in event_orchestrator), rather than the
user receiving a broken deliverable.

A spec is a plain dict, declared as DOC_SPEC on each agent:

    {"docx": {"required_headings": [...], "required_tables": [...], "min_sections": 5},
     "xlsx": {"required_sheets": [...], "total_rows": {"Labor Detail": "TOTAL LABOR"},
              "min_rows": 3},
     "pptx": {"min_slides": 8, "max_slides": 12},
     "cross_check": {"xlsx_totals_match_docx": True}}

Every check is advisory in one direction only: it can report a problem, never
modify the file.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Text that means the model left a gap. Matched case-insensitively on whole words
# so "STANDBY" or a legitimate "..." inside prose does not trip it.
PLACEHOLDER_PATTERNS = [
    r"\bTBD\b", r"\bTBA\b", r"\bT\.B\.D\b", r"\bXXX+\b", r"\bN/?A\s*-\s*fill\b",
    r"\[\s*(insert|add|your|placeholder|todo|tbd|fill|example)[^\]]*\]",
    r"\blorem ipsum\b", r"\bplaceholder\b", r"\bfill in\b", r"\bTODO\b",
    r"<\s*(insert|placeholder|todo)[^>]*>",
]
_PLACEHOLDER_RE = re.compile("|".join(PLACEHOLDER_PATTERNS), re.IGNORECASE)

# A "total" row label in any of the forms the prompts ask for.
_TOTAL_RE = re.compile(r"\b(total|subtotal|grand total)\b", re.IGNORECASE)

MAX_ISSUES = 25          # keep the message a model can act on
_MONEY_RE = re.compile(r"-?\$?\s?\d[\d,]*\.?\d*")


@dataclass
class ValidationResult:
    ok: bool = True
    issues: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    def fail(self, issue: str) -> None:
        self.ok = False
        if len(self.issues) < MAX_ISSUES:
            self.issues.append(issue)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "issues": self.issues, "checks_run": self.checked}


# ── shared helpers ────────────────────────────────────────────────────────────

def _placeholders(text: str) -> str | None:
    m = _PLACEHOLDER_RE.search(text or "")
    return m.group(0) if m else None


def _norm(s: str) -> str:
    """Normalise a heading/sheet name for comparison: lowercase, no numbering,
    no punctuation, collapsed whitespace."""
    s = re.sub(r"^[\s\d]+[.)\-–:]\s*", "", (s or "").strip())
    s = re.sub(r"[^\w\s&]", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _heading_matches(required: str, actual: str) -> bool:
    """Prefix matching, not containment: "Functional Requirements" must NOT satisfy
    a required "Non-Functional Requirements" (plain substring matching did, which
    made a genuinely missing section look present)."""
    r, a = _norm(required), _norm(actual)
    return r == a or a.startswith(r) or r.startswith(a)


def _numbers(text: str) -> set[float]:
    out = set()
    for raw in _MONEY_RE.findall(text or ""):
        try:
            value = float(raw.replace("$", "").replace(",", "").strip())
        except ValueError:
            continue
        if abs(value) >= 1000:       # ignore counts, percentages, row numbers
            out.add(round(value))
    return out


# ── Word ──────────────────────────────────────────────────────────────────────

def validate_docx(path: str | Path, spec: dict | None = None) -> ValidationResult:
    from docx import Document

    spec = spec or {}
    result = ValidationResult()
    try:
        doc = Document(str(path))
    except Exception as e:
        result.fail(f"The .docx file could not be opened ({e}). Regenerate it.")
        return result

    headings, blocks = [], []
    for para in doc.paragraphs:
        text = (para.text or "").strip()
        style = (para.style.name or "") if para.style else ""
        if style.startswith("Heading") or style == "Title":
            headings.append(text)
            blocks.append({"heading": text, "body": [], "tables": 0})
        elif text and blocks:
            blocks[-1]["body"].append(text)

    result.checked.append("opened")
    if not blocks and not doc.tables:
        result.fail("The document is empty — it has no sections and no tables.")
        return result

    # Tables belong to whichever section precedes them; python-docx keeps them in
    # a separate collection, so attribute by document order.
    section_idx = -1
    table_counts = [0] * max(len(blocks), 1)
    para_iter = iter(doc.paragraphs)
    for child in doc.element.body:
        tag = child.tag.split("}")[-1]
        if tag == "p":
            para = next(para_iter, None)
            if para is not None and para.style is not None and \
                    ((para.style.name or "").startswith("Heading") or para.style.name == "Title") \
                    and (para.text or "").strip():
                section_idx += 1
        elif tag == "tbl" and 0 <= section_idx < len(table_counts):
            table_counts[section_idx] += 1
    for i, count in enumerate(table_counts[:len(blocks)]):
        blocks[i]["tables"] = count

    # 1. placeholder text anywhere
    full_text = "\n".join(p.text for p in doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            full_text += "\n" + " ".join(c.text for c in row.cells)
    found = _placeholders(full_text)
    if found:
        result.fail(f"The document still contains placeholder text ({found!r}). "
                    "Replace every placeholder with a real value.")
    result.checked.append("placeholders")

    # 2. headings with no content at all
    for block in blocks:
        if not block["body"] and not block["tables"]:
            result.fail(f"Section {block['heading']!r} has a heading but no content — "
                        "add its paragraphs, bullets or table.")
    result.checked.append("empty_sections")

    # 3. tables: header-only or ragged
    for t_index, table in enumerate(doc.tables, 1):
        rows = table.rows
        if len(rows) < 2:
            result.fail(f"Table {t_index} has a header row but no data rows.")
            continue
        width = len(rows[0].cells)
        for r_index, row in enumerate(rows[1:], 2):
            cells = [c.text.strip() for c in row.cells]
            if len(cells) != width:
                result.fail(f"Table {t_index} row {r_index} has {len(cells)} cells "
                            f"but the header has {width}.")
                break
            if not any(cells):
                result.fail(f"Table {t_index} row {r_index} is completely empty.")
                break
    result.checked.append("tables")

    # 4. spec: required headings, in order
    required = spec.get("required_headings") or []
    if required:
        missing = [h for h in required
                   if not any(_heading_matches(h, actual) for actual in headings)]
        if missing:
            result.fail("The document is missing these required sections: "
                        + ", ".join(repr(m) for m in missing))
        else:
            positions = [next(i for i, a in enumerate(headings) if _heading_matches(h, a))
                         for h in required]
            if positions != sorted(positions):
                result.fail("The required sections are present but out of order. "
                            "Expected order: " + " → ".join(required))
        result.checked.append("required_headings")

    # 5. spec: sections that must contain a table
    for heading in spec.get("required_tables") or []:
        block = next((b for b in blocks if _heading_matches(heading, b["heading"])), None)
        if block is None:
            continue          # already reported as a missing heading
        if not block["tables"]:
            result.fail(f"Section {heading!r} must contain a table but has none.")
    if spec.get("required_tables"):
        result.checked.append("required_tables")

    # 6. spec: minimum counts
    min_sections = spec.get("min_sections")
    if min_sections and len(blocks) < min_sections:
        result.fail(f"The document has {len(blocks)} sections but needs at least {min_sections}.")

    for pattern, minimum, label in spec.get("min_numbered", []):
        count = len(set(re.findall(pattern, full_text, re.IGNORECASE)))
        if count < minimum:
            result.fail(f"Only {count} {label} found — at least {minimum} are required.")
    if spec.get("min_numbered"):
        result.checked.append("min_numbered")

    return result


# ── Excel ─────────────────────────────────────────────────────────────────────

def validate_xlsx(path: str | Path, spec: dict | None = None) -> ValidationResult:
    from openpyxl import load_workbook

    spec = spec or {}
    result = ValidationResult()
    try:
        wb = load_workbook(str(path))
    except Exception as e:
        result.fail(f"The .xlsx file could not be opened ({e}). Regenerate it.")
        return result
    result.checked.append("opened")

    sheet_names = wb.sheetnames
    if not sheet_names:
        result.fail("The workbook has no sheets.")
        return result

    for ws in wb.worksheets:
        values = [[c.value for c in row] for row in ws.iter_rows()]
        non_empty = [r for r in values if any(v not in (None, "") for v in r)]
        if not non_empty:
            result.fail(f"Sheet {ws.title!r} is empty.")
            continue
        if len(non_empty) < 2:
            result.fail(f"Sheet {ws.title!r} has a header row but no data rows.")

        text = " ".join(str(v) for r in values for v in r if isinstance(v, str))
        found = _placeholders(text)
        if found:
            result.fail(f"Sheet {ws.title!r} contains placeholder text ({found!r}).")

        # A formula that openpyxl reports with no cached value shows blank in Excel
        # until the user recalculates — the prompts require computed numbers.
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("=") and \
                        spec.get("no_formulas"):
                    result.fail(f"Sheet {ws.title!r} cell {cell.coordinate} contains the formula "
                                f"{cell.value!r}. Write the computed number instead.")
                    break
            else:
                continue
            break
    result.checked.append("sheets_have_data")

    required = spec.get("required_sheets") or []
    missing = [s for s in required if not any(_heading_matches(s, n) for n in sheet_names)]
    if missing:
        result.fail("The workbook is missing these required sheets: "
                    + ", ".join(repr(m) for m in missing)
                    + f". It has: {', '.join(sheet_names)}")
    if required:
        result.checked.append("required_sheets")

    for sheet_name, label in (spec.get("total_rows") or {}).items():
        ws = next((w for w in wb.worksheets if _heading_matches(sheet_name, w.title)), None)
        if ws is None:
            continue          # already reported as missing
        labels = [str(row[0].value or "") for row in ws.iter_rows()]
        pattern = label if label is not True else None
        if pattern:
            hit = any(_norm(pattern) in _norm(l) for l in labels)
        else:
            hit = any(_TOTAL_RE.search(l) for l in labels)
        if not hit:
            result.fail(f"Sheet {ws.title!r} has no "
                        f"{pattern or 'TOTAL'} row — add it as the final row.")
    if spec.get("total_rows"):
        result.checked.append("total_rows")

    min_rows = spec.get("min_rows")
    if min_rows:
        for ws in wb.worksheets:
            count = sum(1 for row in ws.iter_rows(min_row=2)
                        if any(c.value not in (None, "") for c in row))
            if count < min_rows:
                result.fail(f"Sheet {ws.title!r} has only {count} data rows "
                            f"but needs at least {min_rows}.")

    return result


# ── PowerPoint ────────────────────────────────────────────────────────────────

def validate_pptx(path: str | Path, spec: dict | None = None) -> ValidationResult:
    from pptx import Presentation

    spec = spec or {}
    result = ValidationResult()
    try:
        prs = Presentation(str(path))
    except Exception as e:
        result.fail(f"The .pptx file could not be opened ({e}). Regenerate it.")
        return result
    result.checked.append("opened")

    slides = list(prs.slides)
    if not slides:
        result.fail("The presentation has no slides.")
        return result

    all_text = []
    for index, slide in enumerate(slides, 1):
        texts = []
        has_table = has_picture = False
        for shape in slide.shapes:
            if shape.has_text_frame:
                content = (shape.text_frame.text or "").strip()
                if content:
                    texts.append(content)
            if getattr(shape, "has_table", False):
                has_table = True
            if shape.shape_type is not None and "PICTURE" in str(shape.shape_type):
                has_picture = True
        all_text.extend(texts)
        # A title-only slide is fine for section dividers; a slide with nothing is not.
        if not texts and not has_table and not has_picture:
            result.fail(f"Slide {index} is completely empty.")

    found = _placeholders("\n".join(all_text))
    if found:
        result.fail(f"The presentation contains placeholder text ({found!r}).")
    result.checked.append("slides_have_content")

    min_slides, max_slides = spec.get("min_slides"), spec.get("max_slides")
    if min_slides and len(slides) < min_slides:
        result.fail(f"The deck has {len(slides)} slides but needs at least {min_slides}.")
    if max_slides and len(slides) > max_slides:
        result.fail(f"The deck has {len(slides)} slides, more than the {max_slides} allowed. "
                    "Merge or cut slides.")
    if min_slides or max_slides:
        result.checked.append("slide_count")

    max_bullets = spec.get("max_bullets_per_slide")
    if max_bullets:
        for index, slide in enumerate(slides, 1):
            for shape in slide.shapes:
                if not shape.has_text_frame:
                    continue
                bullets = [p for p in shape.text_frame.paragraphs if (p.text or "").strip()]
                # The first paragraph of the body placeholder is often the title line.
                if len(bullets) > max_bullets + 1:
                    result.fail(f"Slide {index} has {len(bullets)} bullet lines; "
                                f"the limit is {max_bullets}.")
                    break
        result.checked.append("bullet_count")

    return result


# ── Cross-document figure agreement ───────────────────────────────────────────

def cross_check_totals(xlsx_path: str | Path, docx_path: str | Path) -> ValidationResult:
    """Every large number in the Word document should also appear in the workbook.
    Catches the common failure where the narrative is written from the model's
    memory rather than from the numbers it just computed."""
    from docx import Document
    from openpyxl import load_workbook

    result = ValidationResult()
    try:
        wb = load_workbook(str(xlsx_path), data_only=True)
        doc = Document(str(docx_path))
    except Exception as e:
        log.warning("cross-check skipped: %s", e)
        return result
    result.checked.append("cross_check")

    workbook_numbers = set()
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, (int, float)) and abs(cell.value) >= 1000:
                    workbook_numbers.add(round(float(cell.value)))
                elif isinstance(cell.value, str):
                    workbook_numbers |= _numbers(cell.value)

    doc_numbers = set()
    for table in doc.tables:                      # tables carry the authoritative figures
        for row in table.rows:
            for cell in row.cells:
                doc_numbers |= _numbers(cell.text)

    if not workbook_numbers or not doc_numbers:
        return result

    # Allow rounding in the narrative ($1,234,567 → $1.23M is handled by the 1% band).
    def close(value: float) -> bool:
        return any(abs(value - other) <= max(1.0, abs(value) * 0.01) for other in workbook_numbers)

    orphans = sorted(v for v in doc_numbers if not close(v))
    if orphans:
        shown = ", ".join(f"{v:,.0f}" for v in orphans[:6])
        result.fail(
            f"These figures appear in the Word document but not in the Excel workbook: {shown}. "
            "Read the workbook with read_output_file and correct the document so every figure matches."
        )
    return result


# ── Dispatch ──────────────────────────────────────────────────────────────────

VALIDATORS = {".docx": validate_docx, ".xlsx": validate_xlsx, ".pptx": validate_pptx}


def validate_file(path: str | Path, spec: dict | None = None) -> ValidationResult:
    """Validate one file against the part of `spec` matching its extension."""
    path = Path(path)
    validator = VALIDATORS.get(path.suffix.lower())
    if validator is None or not path.exists():
        return ValidationResult()
    section = (spec or {}).get(path.suffix.lower().lstrip("."), {})
    try:
        return validator(path, section)
    except Exception as e:                        # never let validation break a run
        log.exception("validator crashed for %s", path)
        return ValidationResult()


def validate_outputs(paths: list[str | Path], spec: dict | None = None) -> dict:
    """Validate several files plus any configured cross-file check.
    Returns {"ok": bool, "issues": {filename: [issue, ...]}}."""
    spec = spec or {}
    issues: dict[str, list[str]] = {}
    for path in paths:
        result = validate_file(path, spec)
        if not result.ok:
            issues[Path(path).name] = result.issues

    if (spec.get("cross_check") or {}).get("xlsx_totals_match_docx"):
        xlsx = next((p for p in paths if str(p).lower().endswith(".xlsx")), None)
        docx = next((p for p in paths if str(p).lower().endswith(".docx")), None)
        if xlsx and docx:
            cross = cross_check_totals(xlsx, docx)
            if not cross.ok:
                issues.setdefault(Path(docx).name, []).extend(cross.issues)

    return {"ok": not issues, "issues": issues}
