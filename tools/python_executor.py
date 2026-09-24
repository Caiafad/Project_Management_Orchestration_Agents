import logging
import subprocess
import sys
import json
import tempfile
from pathlib import Path


def execute_python(code: str, timeout: int = 120) -> str:
    """Execute Python code in a sandboxed subprocess and return the output.

    The variable OUTPUT_DIR (a pathlib.Path) is automatically injected into
    every script so agents can save files directly to the output folder.
    """
    # Dynamically resolve OUTPUT_DIR so it is always correct regardless of cwd
    from config import OUTPUT_DIR

    abs_output_dir = str(OUTPUT_DIR.resolve())

    # Standard Excel formatting helpers — always available in every script.
    # Mirrors the styling used by create_excel: blue headers, alternating rows,
    # borders, frozen panes, auto-width. Agents call these instead of writing
    # raw openpyxl style code from scratch.
    excel_helpers = '''
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter as _gcl

log = logging.getLogger(__name__)

_HEADER_FONT   = Font(bold=True, color="FFFFFF", size=11)
_HEADER_FILL   = PatternFill(start_color="1F3864", end_color="1F3864", fill_type="solid")
_HEADER_ALIGN  = Alignment(horizontal="center", vertical="center", wrap_text=True)
_ALT_FILL      = PatternFill(start_color="D6E4F7", end_color="D6E4F7", fill_type="solid")
_TOTAL_FONT    = Font(bold=True, size=11)
_THIN          = Side(style="thin")
_BORDER        = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

def _xl_header(cell):
    """Apply standard blue header style to a cell."""
    cell.font      = _HEADER_FONT
    cell.fill      = _HEADER_FILL
    cell.alignment = _HEADER_ALIGN
    cell.border    = _BORDER

def _xl_data(cell, row_idx):
    """Apply standard data row style (alternating shading + border)."""
    cell.border    = _BORDER
    cell.alignment = Alignment(vertical="center")
    if row_idx % 2 == 0:
        cell.fill  = _ALT_FILL
    if isinstance(cell.value, (int, float)):
        cell.alignment = Alignment(horizontal="right", vertical="center")
        if isinstance(cell.value, float) or (isinstance(cell.value, int) and cell.value > 999):
            cell.number_format = "#,##0.00"

def _xl_total(cell):
    """Apply bold total-row style to a cell."""
    cell.font      = _TOTAL_FONT
    cell.border    = _BORDER
    if isinstance(cell.value, (int, float)):
        cell.alignment  = Alignment(horizontal="right", vertical="center")
        cell.number_format = "#,##0.00"

def xl_style_sheet(ws, freeze="A2"):
    """
    Auto-style an entire worksheet after data has been written.
    Row 1 = headers (blue), subsequent rows = alternating shading.
    Call this AFTER all data and formulas are written.
    """
    for col_idx, cell in enumerate(ws[1], start=1):
        _xl_header(cell)
    for row_idx, row in enumerate(ws.iter_rows(min_row=2), start=2):
        for cell in row:
            _xl_data(cell, row_idx)
    # Auto-size columns
    for col in ws.columns:
        max_len = 0
        col_letter = _gcl(col[0].column)
        for cell in col:
            try:
                max_len = max(max_len, len(str(cell.value or "")))
            except Exception:
                pass
        ws.column_dimensions[col_letter].width = min(max_len + 4, 65)
    if freeze:
        ws.freeze_panes = freeze

def xl_safe_num(val):
    """Coerce a value to int or float where possible — prevents numbers being stored
    as text strings, which cause #VALUE! errors and break cross-sheet formulas.
    Formula strings (starting with '=') are returned unchanged.
    Percentage strings ('15%') are converted to decimals (0.15).
    """
    if isinstance(val, (int, float)):
        return val
    if isinstance(val, str):
        if val.startswith("="):          # preserve Excel formulas
            return val
        v = val.strip()
        if v.endswith("%"):
            try:
                return float(v[:-1]) / 100
            except ValueError:
                return val
        try:
            f = float(v.replace(",", "").replace("$", ""))
            return int(f) if f == int(f) else f
        except ValueError:
            return val
    return val

def xl_last_data_row(ws):
    """Return the last row number that contains data on this worksheet.
    ALWAYS use this instead of hardcoding row numbers in cross-sheet formulas.

    Example:
        last = xl_last_data_row(labor_ws)
        summary_ws['C2'] = f"='Labor Detail'!G{last}"   # always correct
    """
    return ws.max_row

def xl_col_sum(col_letter, first_row, last_row):
    """Build a SUM formula string for a full column range.
    Use xl_last_data_row(ws) for last_row — never hardcode the number.

    Example:
        ws['G19'] = xl_col_sum('G', 2, xl_last_data_row(ws) - 1)
        # sums G2 through the row before the total row
    """
    return f"=SUM({col_letter}{first_row}:{col_letter}{last_row})"

def xl_add_header_row(ws, headers):
    """Append a styled header row to a worksheet."""
    ws.append(headers)
    for cell in ws[ws.max_row]:
        _xl_header(cell)

def xl_add_data_row(ws, values):
    """Append a styled data row. Numeric strings are auto-coerced to int/float
    so they are never stored as text (prevents #VALUE! errors in formulas)."""
    ws.append([xl_safe_num(v) for v in values])
    row_idx = ws.max_row
    for cell in ws[row_idx]:
        _xl_data(cell, row_idx)

def xl_add_total_row(ws, values):
    """Append a bold total row. Numeric strings are auto-coerced to int/float."""
    ws.append([xl_safe_num(v) for v in values])
    for cell in ws[ws.max_row]:
        _xl_total(cell)

# ── Gantt & Task-Table helpers ───────────────────────────────────────────────

_PHASE_COLORS = {
    "design":     ("1F4E79", "2E75B6", "BDD7EE"),
    "development":("375623", "548235", "E2EFDA"),
    "testing":    ("843C0C", "ED7D31", "FCE4D6"),
    "management": ("3F1F6E", "7030A0", "E8D5F5"),
    "deployment": ("7B0000", "C00000", "FFD7D7"),
    "planning":   ("1F3864", "4472C4", "D6E4F7"),
    "launch":     ("7B3F00", "C55A11", "FCE4D6"),
}

def _phase_colors(phase_name):
    """Return (dark_hex, mid_hex, light_hex) for a given phase name."""
    key = phase_name.lower()
    for k, v in _PHASE_COLORS.items():
        if k in key:
            return v
    return ("404040", "808080", "E0E0E0")

def xl_gantt(ws, tasks, num_periods, period_label="Week"):
    """
    Build a colour-coded Gantt bar chart on worksheet ws.

    tasks — list of dicts:
        name         (str)  : Task / phase label
        phase        (str)  : Phase name used for colour lookup
        start        (int)  : 1-based start period
        end          (int)  : 1-based end period (inclusive)
        is_phase     (bool) : True = phase header row (bold, dark fill)
    num_periods — total number of time columns
    period_label — "Week", "Month", or "Quarter"
    """
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    THIN   = Side(style="thin")
    BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    NAVY   = "1F3864"

    ws.column_dimensions["A"].width = 48
    ws.row_dimensions[1].height = 28

    # ── Header row ──────────────────────────────────────────────────
    h = ws.cell(row=1, column=1, value="Phase / Task")
    h.font      = Font(bold=True, color="FFFFFF", size=11)
    h.fill      = PatternFill(start_color=NAVY, end_color=NAVY, fill_type="solid")
    h.alignment = Alignment(horizontal="center", vertical="center")
    h.border    = BORDER

    for p in range(1, num_periods + 1):
        c = ws.cell(row=1, column=p + 1, value=f"{period_label} {p}")
        c.font      = Font(bold=True, color="FFFFFF", size=8)
        c.fill      = PatternFill(start_color=NAVY, end_color=NAVY, fill_type="solid")
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border    = BORDER
        ws.column_dimensions[get_column_letter(p + 1)].width = 5

    # ── Task rows ───────────────────────────────────────────────────
    for ri, task in enumerate(tasks, start=2):
        name     = task.get("name", "")
        phase    = task.get("phase", "")
        start    = task.get("start", 1)
        end      = task.get("end", 1)
        is_phase = task.get("is_phase", False)
        dark, mid, light = _phase_colors(phase)

        ws.row_dimensions[ri].height = 18 if is_phase else 15

        # Task name cell
        nc = ws.cell(row=ri, column=1, value=name)
        nc.border = BORDER
        if is_phase:
            nc.font      = Font(bold=True, color="FFFFFF", size=10)
            nc.fill      = PatternFill(start_color=dark, end_color=dark, fill_type="solid")
            nc.alignment = Alignment(vertical="center")
        else:
            nc.font      = Font(size=9)
            nc.fill      = PatternFill(start_color="F5F5F5", end_color="F5F5F5", fill_type="solid")
            nc.alignment = Alignment(vertical="center", indent=2)

        # Period bar cells
        # Phase rows use dark fill; sub-task rows use mid fill.
        # Empty cells use a very light gray so bars pop against the background.
        for p in range(1, num_periods + 1):
            c = ws.cell(row=ri, column=p + 1)
            c.border = BORDER
            if start <= p <= end:
                fill_hex = dark if is_phase else mid
                c.fill = PatternFill(start_color=fill_hex, end_color=fill_hex, fill_type="solid")
            else:
                c.fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")

    ws.freeze_panes = "B2"


def xl_task_table(ws, tasks, period_label="Week"):
    """
    Build a structured task/dependency table on worksheet ws.

    tasks — same list as xl_gantt, with additional keys:
        duration     (int|str)  : Length in periods
        dependencies (str)      : Predecessor task IDs / "N/A"
        resources    (str)      : Roles assigned
    """
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    THIN   = Side(style="thin")
    BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

    headers = [
        "Phase / Task",
        f"Start {period_label}",
        f"End {period_label}",
        f"Duration ({period_label}s)",
        "Dependencies",
        "Resources",
    ]
    col_widths = [52, 13, 13, 18, 28, 38]

    # Header row
    ws.append(headers)
    for ci, cell in enumerate(ws[1], start=1):
        cell.font      = Font(bold=True, color="FFFFFF", size=11)
        cell.fill      = PatternFill(start_color="1F3864", end_color="1F3864", fill_type="solid")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border    = BORDER
        ws.column_dimensions[get_column_letter(ci)].width = col_widths[ci - 1]
    ws.row_dimensions[1].height = 24

    for task in tasks:
        name     = task.get("name", "")
        phase    = task.get("phase", "")
        start    = task.get("start", "")
        end      = task.get("end", "")
        duration = task.get("duration", "")
        deps     = task.get("dependencies", "N/A")
        resources= task.get("resources", "")
        is_phase = task.get("is_phase", False)
        dark, mid, light = _phase_colors(phase)

        ws.append([name, start, end, duration, deps, resources])
        ri = ws.max_row
        ws.row_dimensions[ri].height = 20 if is_phase else 15

        for ci, cell in enumerate(ws[ri], start=1):
            cell.border = BORDER
            cell.alignment = Alignment(
                vertical="center",
                wrap_text=(ci in (1, 5, 6)),
                indent=(2 if (ci == 1 and not is_phase) else 0),
            )
            if is_phase:
                cell.font = Font(bold=True, color="FFFFFF", size=10)
                cell.fill = PatternFill(start_color=dark, end_color=dark, fill_type="solid")
            else:
                cell.font = Font(size=9)
                if ri % 2 == 0:
                    cell.fill = PatternFill(start_color=light, end_color=light, fill_type="solid")

    ws.freeze_panes = "A2"

# ── Word document Gantt & Task-Table helpers ─────────────────────────────────

def _docx_cell_bg(cell, hex_color):
    """Set a python-docx table cell background colour via OOXML."""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    # Remove any existing shading
    for existing in tcPr.findall(qn("w:shd")):
        tcPr.remove(existing)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"),   "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"),  hex_color.upper())
    tcPr.append(shd)


def _docx_set_cell_width(cell, emu_width):
    """Set a table cell's preferred width via OOXML (twips/DXA units).

    python-docx's table.columns[i].width is unreliable — setting width on
    each cell's tcW element is the only method Word actually respects.
    """
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcW  = tcPr.find(qn("w:tcW"))
    if tcW is None:
        tcW = OxmlElement("w:tcW")
        tcPr.insert(0, tcW)
    # Convert EMU → twips (DXA): 1 inch = 914400 EMU = 1440 twips
    twips = str(int(emu_width * 1440 / 914400))
    tcW.set(qn("w:w"),    twips)
    tcW.set(qn("w:type"), "dxa")

def docx_set_landscape(doc):
    """Switch a Word document to landscape orientation.
    Call this BEFORE adding content when the Gantt chart has many periods (>16).
    """
    from docx.shared import Inches
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    section = doc.sections[0]
    # Swap width and height
    new_width, new_height = section.page_height, section.page_width
    section.page_width  = new_width
    section.page_height = new_height
    # Set landscape attribute in XML
    pgSz = section._sectPr.find(qn("w:pgSz"))
    if pgSz is None:
        pgSz = OxmlElement("w:pgSz")
        section._sectPr.append(pgSz)
    pgSz.set(qn("w:orient"), "landscape")
    # Tighten margins for more content space
    section.left_margin   = Inches(0.75)
    section.right_margin  = Inches(0.75)
    section.top_margin    = Inches(0.75)
    section.bottom_margin = Inches(0.75)


def docx_gantt_table(doc, tasks, num_periods, period_label="Week"):
    """
    Insert a colour-coded Gantt bar chart as a Word table into doc.

    tasks        — same format as xl_gantt
    num_periods  — number of time columns
    period_label — "Week", "Month", or "Quarter"

    IMPORTANT: For num_periods > 16, call docx_set_landscape(doc) BEFORE this
    function so the table fits on the page without truncation.
    Content width: landscape Letter = ~9.5 in; portrait Letter = ~6.0 in.
    """
    from docx.shared import Pt, RGBColor, Inches, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    HEADER_BG = "2E75B6"   # mid-blue — distinct from phase rows (dark navy) so header is clearly separate
    n_cols = num_periods + 1  # task name col + period cols

    table = doc.add_table(rows=1 + len(tasks), cols=n_cols)
    table.style = "Table Grid"
    table.autofit = False  # must be False before setting cell widths

    # Determine usable content width based on current page orientation.
    section = doc.sections[0]
    is_landscape = section.page_width > section.page_height
    if is_landscape:
        # Landscape Letter with 0.75" margins: 11 - 1.5 = 9.5 inches = 24.13 cm
        content_cm = 24.0
    else:
        # Portrait Letter with 1.25" margins: 8.5 - 2.5 = 6.0 inches = 15.24 cm
        content_cm = 15.0

    # Name column gets 38% of content width; period columns share the rest equally.
    name_cm   = content_cm * 0.38
    bar_cm    = (content_cm - name_cm) / num_periods
    name_width   = Cm(max(3.5, name_cm))
    period_width = Cm(max(0.28, bar_cm))
    for row in table.rows:
        _docx_set_cell_width(row.cells[0], name_width)
        for ci in range(1, n_cols):
            _docx_set_cell_width(row.cells[ci], period_width)

    # Header row — mid-blue background (visually distinct from dark-navy phase rows)
    hdr_row = table.rows[0]
    hdr_row.height = Cm(0.7)
    _docx_cell_bg(hdr_row.cells[0], HEADER_BG)
    p = hdr_row.cells[0].paragraphs[0]
    run = p.add_run("Phase / Task")
    run.bold = True
    run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    run.font.size = Pt(9)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Short prefix so narrow columns don't show truncated "Week 1" text
    _abbr_map = {"Week": "W", "Month": "M", "Quarter": "Q"}
    _pfx = _abbr_map.get(period_label, period_label[0] if period_label else "")
    for p_idx in range(1, num_periods + 1):
        cell = hdr_row.cells[p_idx]
        _docx_cell_bg(cell, HEADER_BG)
        para = cell.paragraphs[0]
        run = para.add_run(f"{_pfx}{p_idx}")
        run.bold = True
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        run.font.size = Pt(7)
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Task rows
    for ri, task in enumerate(tasks, start=1):
        name     = task.get("name", "")
        phase    = task.get("phase", "")
        start    = task.get("start", 1)
        end      = task.get("end", 1)
        is_phase = task.get("is_phase", False)
        dark, mid, light = _phase_colors(phase)

        row = table.rows[ri]
        row.height = Cm(0.55 if is_phase else 0.45)

        # Name cell
        nc = row.cells[0]
        _docx_cell_bg(nc, dark if is_phase else "F5F5F5")
        para = nc.paragraphs[0]
        run  = para.add_run(name)
        run.bold = is_phase
        run.font.size = Pt(9 if is_phase else 8)
        run.font.color.rgb = (RGBColor(0xFF, 0xFF, 0xFF) if is_phase
                              else RGBColor(0x1A, 0x1A, 0x2E))

        # Bar cells
        for p_idx in range(1, num_periods + 1):
            c = row.cells[p_idx]
            if start <= p_idx <= end:
                _docx_cell_bg(c, mid if is_phase else light)
            else:
                _docx_cell_bg(c, "FFFFFF")

    doc.add_paragraph("")  # spacer


def docx_task_table(doc, tasks, period_label="Week"):
    """
    Insert a structured Task & Dependencies table as a Word table into doc.

    tasks — same format as xl_task_table (includes duration, dependencies, resources)
    """
    from docx.shared import Pt, RGBColor, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    HEADER_BG = "2E75B6"   # mid-blue — distinct from dark-navy phase rows
    headers = [
        "Phase / Task",
        f"Start {period_label}",
        f"End {period_label}",
        f"Duration ({period_label}s)",
        "Dependencies",
        "Resources",
    ]
    # Total must fit A4 portrait content width (~15.9 cm with 1-inch margins).
    # [Phase/Task, Start, End, Duration, Dependencies, Resources] = 15.5 cm total
    col_widths_cm = [6.5, 1.3, 1.3, 1.6, 2.4, 2.8]

    table = doc.add_table(rows=1 + len(tasks), cols=len(headers))
    table.style = "Table Grid"
    table.autofit = False  # must be False before setting cell widths

    # Set column widths cell-by-cell for reliable enforcement
    for row in table.rows:
        for ci, w in enumerate(col_widths_cm):
            _docx_set_cell_width(row.cells[ci], Cm(w))

    # Header row — mid-blue so it's visually distinct from dark-navy phase rows
    hdr = table.rows[0]
    hdr.height = Cm(0.7)
    for ci, label in enumerate(headers):
        cell = hdr.cells[ci]
        _docx_cell_bg(cell, HEADER_BG)
        para = cell.paragraphs[0]
        run  = para.add_run(label)
        run.bold = True
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Data rows
    for ri, task in enumerate(tasks, start=1):
        name     = task.get("name", "")
        phase    = task.get("phase", "")
        start    = str(task.get("start", ""))
        end      = str(task.get("end", ""))
        duration = str(task.get("duration", ""))
        deps     = task.get("dependencies", "N/A")
        resources= task.get("resources", "")
        is_phase = task.get("is_phase", False)
        dark, mid, light = _phase_colors(phase)

        row = table.rows[ri]
        row.height = Cm(0.6 if is_phase else 0.45)
        values = [name, start, end, duration, deps, resources]

        for ci, val in enumerate(values):
            cell = row.cells[ci]
            _docx_cell_bg(cell, dark if is_phase else ("F5F5F5" if ri % 2 == 0 else "FFFFFF"))
            para = cell.paragraphs[0]
            run  = para.add_run(val)
            run.bold = is_phase
            run.font.size = Pt(9 if is_phase else 8)
            run.font.color.rgb = (RGBColor(0xFF, 0xFF, 0xFF) if is_phase
                                  else RGBColor(0x26, 0x26, 0x26))
            if ci > 0:
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph("")  # spacer

# ── PowerPoint Gantt helper ──────────────────────────────────────────────────

def pptx_gantt_slide(prs, tasks, num_periods, period_label="Week", slide_title="Project Gantt Chart", notes=None, **_kwargs):
    """
    Append a Gantt chart slide to an existing python-pptx Presentation object.

    tasks        — same format as xl_gantt / docx_gantt_table
    num_periods  — number of time period columns
    period_label — "Week", "Month", or "Quarter"
    slide_title  — title shown in the dark header bar

    Colours match the Excel and Word Gantt helpers exactly.
    """
    from pptx import Presentation as _Prs
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    from pptx.oxml.ns import qn
    from lxml import etree

    SLIDE_W = prs.slide_width
    SLIDE_H = prs.slide_height

    # Use a blank layout
    blank = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank)

    # ── Dark header bar ──────────────────────────────────────────────
    NAVY     = RGBColor(0x1F, 0x38, 0x64)   # 1F3864 — dark navy (title bar)
    MID_BLUE = RGBColor(0x2E, 0x75, 0xB6)   # 2E75B6 — mid blue (accent + table header)

    header = slide.shapes.add_shape(1, 0, 0, SLIDE_W, Inches(1.0))
    header.fill.solid()
    header.fill.fore_color.rgb = NAVY
    header.line.fill.background()

    accent = slide.shapes.add_shape(1, 0, Inches(1.0), SLIDE_W, Inches(0.045))
    accent.fill.solid()
    accent.fill.fore_color.rgb = MID_BLUE
    accent.line.fill.background()

    txb = slide.shapes.add_textbox(Inches(0.3), Inches(0.15), Inches(12.5), Inches(0.75))
    tf  = txb.text_frame
    p   = tf.paragraphs[0]
    p.text = slide_title
    p.font.bold  = True
    p.font.size  = Pt(24)
    p.font.color.rgb = RGBColor(255, 255, 255)

    # ── Gantt table ──────────────────────────────────────────────────
    n_cols    = num_periods + 1
    n_rows    = len(tasks) + 1          # +1 for header
    tbl_left  = Inches(0.2)
    tbl_top   = Inches(1.1)
    tbl_w     = SLIDE_W - Inches(0.4)
    tbl_h     = SLIDE_H - Inches(1.25)

    tbl_shape = slide.shapes.add_table(n_rows, n_cols, tbl_left, tbl_top, tbl_w, tbl_h)
    tbl       = tbl_shape.table

    # Column widths — name col gets ~22%, rest shared equally
    name_w   = int(tbl_w * 0.22)
    period_w = int((tbl_w - name_w) / num_periods)
    tbl.columns[0].width = name_w
    for ci in range(1, n_cols):
        tbl.columns[ci].width = period_w

    def _set_cell(cell, text, bg_rgb, fg_rgb, bold=False, font_size=8, center=False):
        cell.fill.solid()
        cell.fill.fore_color.rgb = bg_rgb
        tf = cell.text_frame
        tf.word_wrap = False
        p  = tf.paragraphs[0]
        p.text = text
        p.font.size  = Pt(font_size)
        p.font.bold  = bold
        p.font.color.rgb = fg_rgb
        if center:
            p.alignment = PP_ALIGN.CENTER

    WHITE     = RGBColor(255, 255, 255)
    DARK_TEXT = RGBColor(26, 26, 46)
    EMPTY_BG  = RGBColor(245, 245, 245)

    # Header row — mid-blue to match Word/Excel Gantt header style
    _set_cell(tbl.cell(0, 0), "Phase / Task", MID_BLUE, WHITE, bold=True, font_size=9)
    for p_i in range(1, num_periods + 1):
        _set_cell(tbl.cell(0, p_i), str(p_i), MID_BLUE, WHITE, bold=True, font_size=7, center=True)

    # Task rows
    for ri, task in enumerate(tasks, start=1):
        name     = task.get("name", "")
        phase    = task.get("phase", "")
        start    = task.get("start", 1)
        end      = task.get("end", 1)
        is_phase = task.get("is_phase", False)
        dark_h, mid_h, light_h = _phase_colors(phase)

        def _hex(h):
            return RGBColor(int(h[0:2],16), int(h[2:4],16), int(h[4:6],16))

        dark_rgb  = _hex(dark_h)
        mid_rgb   = _hex(mid_h)
        light_rgb = _hex(light_h)
        name_bg   = dark_rgb  if is_phase else RGBColor(242, 242, 242)
        name_fg   = WHITE     if is_phase else DARK_TEXT

        _set_cell(tbl.cell(ri, 0), name, name_bg, name_fg,
                  bold=is_phase, font_size=8 if is_phase else 7)

        for p_i in range(1, num_periods + 1):
            if start <= p_i <= end:
                bar_bg = mid_rgb if is_phase else light_rgb
                _set_cell(tbl.cell(ri, p_i), "", bar_bg, bar_bg)
            else:
                _set_cell(tbl.cell(ri, p_i), "", EMPTY_BG, EMPTY_BG)

# ── PowerPoint slide helpers ─────────────────────────────────────────────────
# Pre-injected into every script. Use these instead of writing raw python-pptx
# slide code from scratch — they handle positioning, fonts, and colours correctly.

def _pptx_rgb(hex_str):
    from pptx.dml.color import RGBColor as _C
    h = hex_str.lstrip("#")
    return _C(int(h[0:2],16), int(h[2:4],16), int(h[4:6],16))

def _pptx_hdr(slide, prs, title):
    """Dark navy header bar + white title text — shared by all slide types."""
    from pptx.util import Inches, Pt
    from pptx.enum.text import PP_ALIGN
    W = prs.slide_width
    hdr = slide.shapes.add_shape(1, 0, 0, W, Inches(1.1))
    hdr.fill.solid(); hdr.fill.fore_color.rgb = _pptx_rgb("1F3864"); hdr.line.fill.background()
    txb = slide.shapes.add_textbox(Inches(0.3), Inches(0.2), W - Inches(0.6), Inches(0.75))
    p = txb.text_frame.paragraphs[0]; p.text = title
    p.font.size = Pt(24); p.font.bold = True
    p.font.color.rgb = _pptx_rgb("FFFFFF"); p.alignment = PP_ALIGN.LEFT

def pptx_add_title_slide(prs, title, subtitle="", notes=""):
    """Branded title slide — dark navy background, large title, optional subtitle."""
    from pptx.util import Inches, Pt
    from pptx.enum.text import PP_ALIGN
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    W, H = prs.slide_width, prs.slide_height
    bg = slide.shapes.add_shape(1, 0, 0, W, H)
    bg.fill.solid(); bg.fill.fore_color.rgb = _pptx_rgb("1F3864"); bg.line.fill.background()
    bar = slide.shapes.add_shape(1, 0, int(H * 0.58), W, Inches(0.06))
    bar.fill.solid(); bar.fill.fore_color.rgb = _pptx_rgb("4472C4"); bar.line.fill.background()
    txb = slide.shapes.add_textbox(Inches(0.8), int(H * 0.28), W - Inches(1.6), Inches(1.8))
    tf = txb.text_frame; tf.word_wrap = True
    p = tf.paragraphs[0]; p.text = title
    p.font.size = Pt(36); p.font.bold = True; p.font.color.rgb = _pptx_rgb("FFFFFF"); p.alignment = PP_ALIGN.LEFT
    if subtitle:
        stxb = slide.shapes.add_textbox(Inches(0.8), int(H * 0.63), W - Inches(1.6), Inches(0.8))
        sp = stxb.text_frame.paragraphs[0]; sp.text = subtitle
        sp.font.size = Pt(18); sp.font.color.rgb = _pptx_rgb("B0C4DE"); sp.alignment = PP_ALIGN.LEFT
    if notes:
        slide.notes_slide.notes_text_frame.text = notes

def pptx_add_section_slide(prs, title, subtitle="", notes=""):
    """Section divider slide — dark background, centred large text."""
    from pptx.util import Inches, Pt
    from pptx.enum.text import PP_ALIGN
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    W, H = prs.slide_width, prs.slide_height
    bg = slide.shapes.add_shape(1, 0, 0, W, H)
    bg.fill.solid(); bg.fill.fore_color.rgb = _pptx_rgb("2C3E50"); bg.line.fill.background()
    txb = slide.shapes.add_textbox(Inches(1), int(H * 0.35), W - Inches(2), Inches(1.6))
    tf = txb.text_frame; tf.word_wrap = True
    p = tf.paragraphs[0]; p.text = title
    p.font.size = Pt(40); p.font.bold = True; p.font.color.rgb = _pptx_rgb("FFFFFF"); p.alignment = PP_ALIGN.CENTER
    if subtitle:
        stxb = slide.shapes.add_textbox(Inches(1), int(H * 0.58), W - Inches(2), Inches(0.6))
        sp = stxb.text_frame.paragraphs[0]; sp.text = subtitle
        sp.font.size = Pt(18); sp.font.color.rgb = _pptx_rgb("95A5A6"); sp.alignment = PP_ALIGN.CENTER
    if notes:
        slide.notes_slide.notes_text_frame.text = notes

def pptx_add_content_slide(prs, title, bullet_points, notes=""):
    """Content slide — navy header bar + up to 5 bullet points."""
    from pptx.util import Inches, Pt
    from pptx.enum.text import PP_ALIGN
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    W, H = prs.slide_width, prs.slide_height
    _pptx_hdr(slide, prs, title)
    btxb = slide.shapes.add_textbox(Inches(0.5), Inches(1.3), W - Inches(1.0), H - Inches(1.6))
    btf = btxb.text_frame; btf.word_wrap = True
    for i, bullet in enumerate(bullet_points[:5]):
        bp = btf.paragraphs[0] if i == 0 else btf.add_paragraph()
        bp.text = f"▸  {bullet}"; bp.font.size = Pt(18)
        bp.font.color.rgb = _pptx_rgb("1A1A2E"); bp.space_after = Pt(8)
    if notes:
        slide.notes_slide.notes_text_frame.text = notes

def pptx_add_metrics_slide(prs, title, metrics, notes=""):
    """KPI metrics slide — light-blue cards each with icon / value / label.

    Palette matches create_powerpoint exactly: navy header bar, light-blue
    card background, mid-blue value text, dark body label.

    metrics — list of dicts: [{"icon": "💰", "label": "Budget", "value": "$276K"}, ...]
    """
    from pptx.util import Inches, Pt
    from pptx.enum.text import PP_ALIGN
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    W, H = prs.slide_width, prs.slide_height
    _pptx_hdr(slide, prs, title)
    n = max(len(metrics), 1)
    card_w = int((W - Inches(0.6)) / n)
    card_h = int(H * 0.55)
    top = int(H * 0.22)
    CARD_BG   = "D6E4F7"   # light blue — matches Excel alt-row and PPTX table rows
    VAL_COLOR = "2E75B6"   # mid blue  — matches create_powerpoint MID_BLUE
    LBL_COLOR = "1A1A2E"   # dark text
    for i, m in enumerate(metrics[:5]):
        left = Inches(0.3) + i * card_w
        card = slide.shapes.add_shape(1, left, top, card_w - Inches(0.1), card_h)
        card.fill.solid(); card.fill.fore_color.rgb = _pptx_rgb(CARD_BG); card.line.fill.background()
        itxb = slide.shapes.add_textbox(left, top + Inches(0.15), card_w - Inches(0.1), Inches(0.7))
        ip = itxb.text_frame.paragraphs[0]; ip.text = m.get("icon", "●")
        ip.font.size = Pt(28); ip.alignment = PP_ALIGN.CENTER
        vtxb = slide.shapes.add_textbox(left, top + Inches(0.85), card_w - Inches(0.1), Inches(0.9))
        vp = vtxb.text_frame.paragraphs[0]; vp.text = str(m.get("value", ""))
        vp.font.size = Pt(26); vp.font.bold = True
        vp.font.color.rgb = _pptx_rgb(VAL_COLOR); vp.alignment = PP_ALIGN.CENTER
        ltxb = slide.shapes.add_textbox(left, top + Inches(1.7), card_w - Inches(0.1), Inches(0.5))
        lp = ltxb.text_frame.paragraphs[0]; lp.text = m.get("label", "")
        lp.font.size = Pt(13); lp.font.color.rgb = _pptx_rgb(LBL_COLOR); lp.alignment = PP_ALIGN.CENTER
    if notes:
        slide.notes_slide.notes_text_frame.text = notes

def pptx_add_two_column_slide(prs, title, left_heading, left_bullets, right_heading, right_bullets, notes=""):
    """Two-column comparison slide (Before/After, Pros/Cons, etc.)."""
    from pptx.util import Inches, Pt
    from pptx.enum.text import PP_ALIGN
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    W, H = prs.slide_width, prs.slide_height
    _pptx_hdr(slide, prs, title)
    col_w = (W - Inches(0.9)) // 2
    HCOLORS = ["4472C4", "27AE60"]
    for ci, (heading, bullets) in enumerate([(left_heading, left_bullets), (right_heading, right_bullets)]):
        left = Inches(0.3) + ci * (col_w + Inches(0.3))
        chdr = slide.shapes.add_shape(1, left, Inches(1.2), col_w, Inches(0.45))
        chdr.fill.solid(); chdr.fill.fore_color.rgb = _pptx_rgb(HCOLORS[ci]); chdr.line.fill.background()
        chtxb = slide.shapes.add_textbox(left, Inches(1.22), col_w, Inches(0.4))
        chp = chtxb.text_frame.paragraphs[0]; chp.text = heading
        chp.font.size = Pt(14); chp.font.bold = True
        chp.font.color.rgb = _pptx_rgb("FFFFFF"); chp.alignment = PP_ALIGN.CENTER
        btxb = slide.shapes.add_textbox(left + Inches(0.1), Inches(1.75), col_w - Inches(0.1), H - Inches(2.0))
        btf = btxb.text_frame; btf.word_wrap = True
        for i, b in enumerate(bullets[:6]):
            bp = btf.paragraphs[0] if i == 0 else btf.add_paragraph()
            bp.text = f"• {b}"; bp.font.size = Pt(14)
            bp.font.color.rgb = _pptx_rgb("1A1A2E"); bp.space_after = Pt(6)
    if notes:
        slide.notes_slide.notes_text_frame.text = notes

def pptx_add_table_slide(prs, title, headers, rows, col_widths=None, notes=""):
    """Table slide — navy header row, alternating shaded data rows.

    col_widths — optional list of relative weights (e.g. [2, 1, 1, 3]).
                 If omitted, columns are distributed equally.
    Font size scales down automatically for wide tables so text never clips.
    Word wrap is enabled on every cell.
    """
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RGBColor as _RC
    from pptx.enum.text import PP_ALIGN
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    W, H = prs.slide_width, prs.slide_height
    _pptx_hdr(slide, prs, title)
    n_cols = len(headers); n_rows = len(rows) + 1
    tbl_w = W - Inches(0.6)
    tbl_h = H - Inches(1.45)
    shape = slide.shapes.add_table(n_rows, n_cols, Inches(0.3), Inches(1.25), tbl_w, tbl_h)
    tbl = shape.table

    # Distribute column widths
    if col_widths and len(col_widths) == n_cols:
        total = sum(col_widths)
        for ci, w in enumerate(col_widths):
            tbl.columns[ci].width = int(tbl_w * w / total)
    else:
        equal_w = int(tbl_w / n_cols)
        for ci in range(n_cols):
            tbl.columns[ci].width = equal_w

    # Adaptive font size — shrink for wide tables
    hdr_pt  = max(8, 12 - max(0, n_cols - 4))
    data_pt = max(7, 10 - max(0, n_cols - 4))

    NAVY  = _RC(31,  56, 100);  WHITE = _RC(255, 255, 255)
    ALT   = _RC(214, 228, 247); DARK  = _RC(26,  26,  46)

    for ci, h in enumerate(headers):
        cell = tbl.cell(0, ci)
        cell.fill.solid(); cell.fill.fore_color.rgb = NAVY
        tf = cell.text_frame; tf.word_wrap = True
        p = tf.paragraphs[0]; p.text = str(h)
        p.font.size = Pt(hdr_pt); p.font.bold = True
        p.font.color.rgb = WHITE; p.alignment = PP_ALIGN.CENTER

    for ri, row in enumerate(rows, start=1):
        bg = ALT if ri % 2 == 0 else _RC(255, 255, 255)
        for ci, val in enumerate(row[:n_cols]):
            cell = tbl.cell(ri, ci)
            cell.fill.solid(); cell.fill.fore_color.rgb = bg
            tf = cell.text_frame; tf.word_wrap = True
            p = tf.paragraphs[0]; p.text = str(val)
            p.font.size = Pt(data_pt); p.font.color.rgb = DARK

    if notes:
        slide.notes_slide.notes_text_frame.text = notes
'''

    # Compatibility shims injected AFTER helpers so agent code that accidentally
    # uses the wrong names still works.  These must come after excel_helpers
    # (which defines the correct versions) so they don't shadow them.
    compat_shims = (
        # python-docx: RGBColor is correct; 'RGB' does not exist in docx.shared.
        "try:\n"
        "    from docx.shared import RGBColor as RGB\n"
        "except Exception:\n"
        "    pass\n"
        "try:\n"
        "    from docx.shared import RGBColor\n"
        "except Exception:\n"
        "    pass\n"
        # WD_SECTION_ORIENTATION does not exist — landscape handled by docx_set_landscape()
        "try:\n"
        "    from docx.enum.section import WD_ORIENT\n"
        "    WD_SECTION_ORIENTATION = WD_ORIENT  # compatibility alias\n"
        "except Exception:\n"
        "    class WD_SECTION_ORIENTATION:\n"
        "        LANDSCAPE = 'landscape'\n"
        "        PORTRAIT  = 'portrait'\n"
        "    pass\n"
    )

    preamble = (
        "from pathlib import Path as _Path\n"
        f"OUTPUT_DIR = _Path(r'{abs_output_dir}')\n"
        "OUTPUT_DIR.mkdir(parents=True, exist_ok=True)\n"
        + excel_helpers
        + "\n"
        + compat_shims
        + "\n"
    )

    # ── Guard: prevent agent from shadowing pre-injected helpers ────────────────
    # When the agent redefines docx_gantt_table / docx_task_table / etc., its
    # (usually buggy) version shadows the correct injected one.  We rename any
    # such redefinition to _agent_override_<name> so the call sites still resolve
    # to the injected version which is already in scope from the preamble.
    import re as _re
    _PROTECTED = [
        "docx_gantt_table", "docx_task_table", "docx_set_landscape",
        "_phase_colors", "_docx_cell_bg", "_docx_set_cell_width",
        "xl_gantt", "xl_task_table",
    ]
    _clean_code = code
    for _fn in _PROTECTED:
        _clean_code = _re.sub(
            rf"^(def\s+{_re.escape(_fn)}\s*\()",
            rf"def _agent_override_{_fn}(",
            _clean_code,
            flags=_re.MULTILINE,
        )

    # ── Additional import compatibility shims in the agent's code ────────────
    # Some non-existent symbols the model sometimes tries to import:
    _clean_code = _clean_code.replace(
        "from docx.enum.section import WD_SECTION_ORIENTATION", ""
    ).replace(
        "import WD_SECTION_ORIENTATION", ""
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(preamble + _clean_code)
        temp_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, temp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        output = {
            "stdout": "",
            "stderr": f"Execution timed out after {timeout} seconds",
            "exit_code": -1,
        }
    finally:
        Path(temp_path).unlink(missing_ok=True)

    # The event wrapper logs exit code, duration and stderr; keep stdout here since
    # agent scripts print their own progress ("Saved Financial_Plan.xlsx").
    if output["stdout"]:
        log.info("script stdout", extra={"event": "python_stdout",
                                         "stdout": output["stdout"][:1000]})

    return json.dumps(output, indent=2)
