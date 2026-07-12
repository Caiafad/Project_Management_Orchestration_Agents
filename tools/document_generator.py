import json
from pathlib import Path
from datetime import datetime

from docx import Document
from docx.shared import Inches, Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

from pptx import Presentation
from pptx.util import Inches as PptxInches, Pt as PptxPt
from pptx.dml.color import RGBColor as PptxRGBColor
from pptx.enum.text import PP_ALIGN

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

from config import OUTPUT_DIR


def _docx_set_cell_bg(cell, hex_color: str):
    """Set a table cell background colour via XML (hex without #)."""
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def _docx_add_page_number(paragraph):
    """Insert a PAGE field into an existing paragraph."""
    run = paragraph.add_run()
    fldChar1 = OxmlElement("w:fldChar")
    fldChar1.set(qn("w:fldCharType"), "begin")
    instrText = OxmlElement("w:instrText")
    instrText.set(qn("xml:space"), "preserve")
    instrText.text = "PAGE"
    fldChar2 = OxmlElement("w:fldChar")
    fldChar2.set(qn("w:fldCharType"), "end")
    run._r.append(fldChar1)
    run._r.append(instrText)
    run._r.append(fldChar2)


def _docx_apply_standard_formatting(doc, title: str):
    """Apply consistent margins, header (title + date), and footer (page number)."""
    section = doc.sections[0]
    section.top_margin    = Cm(2.0)
    section.bottom_margin = Cm(2.0)
    section.left_margin   = Cm(2.5)
    section.right_margin  = Cm(2.5)

    # ── Header: title left, date right ──────────────────────────────
    header = section.header
    header.is_linked_to_previous = False
    # Clear default empty paragraph
    for p in header.paragraphs:
        p.clear()
    hdr_para = header.paragraphs[0]
    hdr_para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run_title = hdr_para.add_run(title)
    run_title.font.size = Pt(8)
    run_title.font.bold = True
    run_title.font.color.rgb = RGBColor(0x1F, 0x38, 0x64)
    # Tab + right-aligned date
    run_date = hdr_para.add_run(f"\t{datetime.now().strftime('%B %d, %Y')}")
    run_date.font.size = Pt(8)
    run_date.font.color.rgb = RGBColor(0x60, 0x60, 0x60)
    # Set tab stop at right margin
    from docx.oxml import OxmlElement as _OE
    pPr = hdr_para._p.get_or_add_pPr()
    tabs = _OE("w:tabs")
    tab = _OE("w:tab")
    tab.set(qn("w:val"), "right")
    tab.set(qn("w:pos"), "9360")   # ~16.5 cm in twips (1 cm = 567 twips)
    tabs.append(tab)
    pPr.append(tabs)

    # ── Footer: centred page number ──────────────────────────────────
    footer = section.footer
    footer.is_linked_to_previous = False
    for p in footer.paragraphs:
        p.clear()
    ftr_para = footer.paragraphs[0]
    ftr_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    ftr_para.add_run("Page ").font.size = Pt(8)
    _docx_add_page_number(ftr_para)
    ftr_para.add_run(" | Confidential").font.size = Pt(8)


def create_word_document(
    title: str,
    sections: str,
    filename: str = "",
    include_toc: bool = False,   # kept for backwards compat; TOC is auto-omitted
) -> str:
    """
    Create a Word document (.docx) with structured content.

    Args:
        title: Document title.
        sections: JSON string of sections. Each section is an object with:
            - "heading" (str): Section heading
            - "level" (int, optional): Heading level 1-4, default 1
            - "content" (str): Section body text (paragraphs separated by newlines)
            - "bullet_points" (list[str], optional): Bullet point items
            - "table" (dict, optional): {"headers": [...], "rows": [[...], ...]}
        filename: Output filename (without extension). Auto-generated if empty.
        include_toc: Ignored — TOC fields are not included in auto-generated docs.

    Returns:
        JSON string with the file path of the created document.
    """
    try:
        sections_data = json.loads(sections)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid sections JSON: {e}"})

    # Defensive: if the agent passed an empty title, pull it from the first section heading
    if not title or not title.strip():
        for sec in sections_data:
            candidate = sec.get("heading", "").strip()
            if candidate and "table of contents" not in candidate.lower():
                title = candidate
                break
        if not title or not title.strip():
            title = "Project Document"

    doc = Document()
    _docx_apply_standard_formatting(doc, title)

    # ── Title block ──────────────────────────────────────────────────
    title_para = doc.add_heading(title, level=0)
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER

    date_para = doc.add_paragraph(datetime.now().strftime("%B %d, %Y"))
    date_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    date_para.runs[0].font.color.rgb = RGBColor(0x60, 0x60, 0x60)
    date_para.runs[0].font.size = Pt(11)
    doc.add_paragraph("")  # spacer

    # ── Sections ─────────────────────────────────────────────────────
    for section in sections_data:
        heading    = section.get("heading", "")
        # Block any Table of Contents section — TOC placeholders are never useful
        if heading and "table of contents" in heading.lower():
            continue
        level      = section.get("level", 1)
        content    = section.get("content", "")
        bullets    = section.get("bullet_points", [])
        table_data = section.get("table")

        if heading:
            doc.add_heading(heading, level=min(level, 4))

        if content:
            for paragraph_text in content.split("\n"):
                if paragraph_text.strip():
                    doc.add_paragraph(paragraph_text.strip())

        if bullets:
            for bullet in bullets:
                doc.add_paragraph(bullet, style="List Bullet")

        if table_data:
            headers = table_data.get("headers", [])
            rows    = table_data.get("rows", [])
            if headers:
                table = doc.add_table(rows=1 + len(rows), cols=len(headers))
                table.style = "Table Grid"
                table.alignment = WD_TABLE_ALIGNMENT.CENTER

                # Header row — navy background, white bold text
                for i, header in enumerate(headers):
                    cell = table.rows[0].cells[i]
                    _docx_set_cell_bg(cell, "1F3864")
                    cell.text = str(header)
                    for para in cell.paragraphs:
                        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        for run in para.runs:
                            run.bold = True
                            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                            run.font.size = Pt(10)

                # Data rows — light blue alternating shading
                for row_idx, row_data in enumerate(rows):
                    for col_idx, cell_value in enumerate(row_data):
                        cell = table.rows[row_idx + 1].cells[col_idx]
                        if row_idx % 2 == 1:
                            _docx_set_cell_bg(cell, "D6E4F7")
                        cell.text = str(cell_value)
                        for para in cell.paragraphs:
                            for run in para.runs:
                                run.font.size = Pt(10)

                doc.add_paragraph("")  # spacer after table

    if not filename:
        safe_title = "".join(c if c.isalnum() or c in " -_" else "" for c in title)
        filename = f"{safe_title}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    filepath = OUTPUT_DIR / f"{filename}.docx"
    doc.save(str(filepath))

    return json.dumps({
        "status": "created",
        "file_path": str(filepath.resolve()),
        "filename": f"{filename}.docx",
    })


def create_powerpoint(
    title: str,
    slides: str,
    filename: str = "",
) -> str:
    """
    Create a professional PowerPoint presentation (.pptx).

    Each slide dict supports:
      - "title"        (str)  : Slide heading
      - "layout"       (str)  : "content" | "metrics" | "table" | "two_column" | "section" | "blank"
      - "bullet_points"([str]): Max 5 short bullets (content layout)
      - "content"      (str)  : Optional intro sentence above bullets
      - "table"        (dict) : {"headers":[str], "rows":[[str]]}  — used with layout="table"
      - "metrics"      ([dict]): [{"icon":"💰","label":"Budget","value":"$276K"}] — layout="metrics"
      - "left"  / "right" (dict): {"bullet_points":[str]} — layout="two_column"
      - "notes"        (str)  : Speaker notes
    """
    try:
        slides_data = json.loads(slides)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid slides JSON: {e}"})

    # ── Colour palette ──────────────────────────────────────────────
    # Each PptxRGBColor takes three 0-255 integers (R, G, B)
    DARK_BLUE  = PptxRGBColor(31,  56,  100)   # #1F3864 — dark navy header
    MID_BLUE   = PptxRGBColor(46,  117, 182)   # #2E75B6 — table header / card value
    LIGHT_BLUE = PptxRGBColor(214, 228, 247)   # #D6E4F7 — alt row / metric card bg
    WHITE      = PptxRGBColor(255, 255, 255)   # #FFFFFF
    DARK_TEXT  = PptxRGBColor(26,  26,  46)    # #1A1A2E — body text
    ACCENT     = MID_BLUE                       # #2E75B6 — mid-blue accent (matches Word/Excel)

    prs = Presentation()
    prs.slide_width  = PptxInches(13.333)
    prs.slide_height = PptxInches(7.5)
    blank_layout = prs.slide_layouts[6]

    # ── helpers ─────────────────────────────────────────────────────
    def _add_rect(slide, x, y, w, h, fill_rgb, radius=False):
        shape = slide.shapes.add_shape(
            1,  # MSO_SHAPE_TYPE.RECTANGLE
            PptxInches(x), PptxInches(y), PptxInches(w), PptxInches(h)
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill_rgb
        shape.line.fill.background()
        return shape

    def _tf_para(tf, text, size, bold=False, color=WHITE, center=False, first=False):
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        p.text = text
        p.font.size = PptxPt(size)
        p.font.bold = bold
        p.font.color.rgb = color
        if center:
            p.alignment = PP_ALIGN.CENTER
        return p

    def _add_textbox(slide, x, y, w, h, text, size, bold=False,
                     color=DARK_TEXT, center=False, wrap=True):
        txb = slide.shapes.add_textbox(
            PptxInches(x), PptxInches(y), PptxInches(w), PptxInches(h)
        )
        txb.text_frame.word_wrap = wrap
        tf = txb.text_frame
        p = tf.paragraphs[0]
        p.text = text
        p.font.size = PptxPt(size)
        p.font.bold = bold
        p.font.color.rgb = color
        if center:
            p.alignment = PP_ALIGN.CENTER
        return txb

    def _slide_header(slide, slide_title):
        """Dark blue top bar + white title text."""
        _add_rect(slide, 0, 0, 13.333, 1.1, DARK_BLUE)
        _add_textbox(slide, 0.3, 0.18, 12.5, 0.85,
                     slide_title, 28, bold=True, color=WHITE)

    def _slide_accent_line(slide):
        """Thin green accent line below the header."""
        _add_rect(slide, 0, 1.1, 13.333, 0.05, ACCENT)

    def _apply_notes(slide, notes):
        if notes:
            slide.notes_slide.notes_text_frame.text = notes

    # ── Title slide ─────────────────────────────────────────────────
    title_slide = prs.slides.add_slide(blank_layout)
    _add_rect(title_slide, 0, 0, 13.333, 7.5, DARK_BLUE)
    _add_rect(title_slide, 0, 5.8, 13.333, 1.7, MID_BLUE)
    _add_rect(title_slide, 0, 3.5, 0.18, 2.0, ACCENT)   # left accent bar
    _add_textbox(title_slide, 0.5, 1.8, 12.0, 1.5,
                 title, 40, bold=True, color=WHITE)
    _add_textbox(title_slide, 0.5, 3.4, 12.0, 0.7,
                 datetime.now().strftime("%B %d, %Y"), 18, color=LIGHT_BLUE)

    # ── Content slides ──────────────────────────────────────────────
    for slide_data in slides_data:
        slide_title  = slide_data.get("title", "")
        layout_type  = slide_data.get("layout", "content")
        bullets      = slide_data.get("bullet_points", [])
        content      = slide_data.get("content", "")
        notes        = slide_data.get("notes", "")
        table_data   = slide_data.get("table")
        metrics      = slide_data.get("metrics", [])
        left_col     = slide_data.get("left", {})
        right_col    = slide_data.get("right", {})

        slide = prs.slides.add_slide(blank_layout)
        _add_rect(slide, 0, 0, 13.333, 7.5, WHITE)   # white background
        _slide_header(slide, slide_title)
        _slide_accent_line(slide)

        # ── SECTION divider ──────────────────────────────────────
        if layout_type == "section":
            _add_rect(slide, 0, 0, 13.333, 7.5, DARK_BLUE)
            _add_rect(slide, 0, 3.4, 0.25, 1.5, ACCENT)
            _add_textbox(slide, 0.6, 2.8, 11.5, 1.5,
                         slide_title, 36, bold=True, color=WHITE, center=True)
            subtitle = content or ""
            if subtitle:
                _add_textbox(slide, 0.6, 4.5, 11.5, 0.8,
                             subtitle, 20, color=LIGHT_BLUE, center=True)

        # ── METRICS cards ────────────────────────────────────────
        elif layout_type == "metrics" and metrics:
            n = len(metrics)
            card_w = 11.5 / n
            for i, m in enumerate(metrics):
                cx = 0.9 + i * card_w
                # card background
                card = _add_rect(slide, cx, 1.5, card_w - 0.2, 4.2, LIGHT_BLUE)
                # icon / emoji
                icon_text = m.get("icon", "")
                if icon_text:
                    _add_textbox(slide, cx, 1.7, card_w - 0.2, 0.9,
                                 icon_text, 32, center=True, color=DARK_TEXT)
                # big value
                _add_textbox(slide, cx, 2.7, card_w - 0.2, 1.1,
                             str(m.get("value", "")), 30, bold=True,
                             color=MID_BLUE, center=True)
                # label
                _add_textbox(slide, cx, 3.9, card_w - 0.2, 0.9,
                             str(m.get("label", "")), 14,
                             color=DARK_TEXT, center=True)

        # ── TABLE ────────────────────────────────────────────────
        elif layout_type == "table" and table_data:
            headers    = table_data.get("headers", [])
            rows       = table_data.get("rows", [])
            col_widths = table_data.get("col_widths")   # optional relative weights
            if headers and rows:
                n_cols = len(headers)
                n_rows = len(rows) + 1  # +1 for header row
                tbl_w  = PptxInches(11.5)
                row_h  = PptxInches(min(0.55, 4.8 / n_rows))
                tbl = slide.shapes.add_table(
                    n_rows, n_cols,
                    PptxInches(0.9), PptxInches(1.4),
                    tbl_w, PptxInches(row_h.inches * n_rows)
                ).table
                # Distribute column widths (equal by default; respect col_widths if given)
                if col_widths and len(col_widths) == n_cols:
                    total_w = sum(col_widths)
                    for ci, w in enumerate(col_widths):
                        tbl.columns[ci].width = int(tbl_w * w / total_w)
                else:
                    col_w = PptxInches(11.5 / n_cols)
                    for ci in range(n_cols):
                        tbl.columns[ci].width = col_w
                # Adaptive font size for wide tables
                hdr_pt  = max(9,  14 - max(0, n_cols - 4))
                data_pt = max(8,  12 - max(0, n_cols - 4))
                # Header row
                for ci, hdr in enumerate(headers):
                    cell = tbl.cell(0, ci)
                    cell.fill.solid()
                    cell.fill.fore_color.rgb = DARK_BLUE
                    tf = cell.text_frame; tf.word_wrap = True
                    p = tf.paragraphs[0]; p.text = str(hdr)
                    p.font.bold = True; p.font.color.rgb = WHITE
                    p.font.size = PptxPt(hdr_pt); p.alignment = PP_ALIGN.CENTER
                # Data rows
                for ri, row in enumerate(rows):
                    for ci, val in enumerate(row[:n_cols]):
                        cell = tbl.cell(ri + 1, ci)
                        if ri % 2 == 1:
                            cell.fill.solid()
                            cell.fill.fore_color.rgb = LIGHT_BLUE
                        tf = cell.text_frame; tf.word_wrap = True
                        p = tf.paragraphs[0]; p.text = str(val)
                        p.font.size = PptxPt(data_pt)
                        p.font.color.rgb = DARK_TEXT; p.alignment = PP_ALIGN.CENTER

        # ── TWO COLUMN ───────────────────────────────────────────
        elif layout_type == "two_column":
            def _render_col(col_data, x, w):
                col_bullets = col_data.get("bullet_points", [])
                col_heading = col_data.get("heading", "")
                if col_heading:
                    _add_rect(slide, x, 1.3, w, 0.45, MID_BLUE)
                    _add_textbox(slide, x + 0.1, 1.33, w - 0.2, 0.4,
                                 col_heading, 14, bold=True, color=WHITE)
                txb = slide.shapes.add_textbox(
                    PptxInches(x + 0.1), PptxInches(1.9),
                    PptxInches(w - 0.2), PptxInches(4.8)
                )
                txb.text_frame.word_wrap = True
                for i, b in enumerate(col_bullets[:6]):
                    p = txb.text_frame.paragraphs[0] if i == 0 else txb.text_frame.add_paragraph()
                    p.text = f"▸  {b}"
                    p.font.size = PptxPt(15)
                    p.font.color.rgb = DARK_TEXT
                    p.space_after = PptxPt(6)

            _add_rect(slide, 6.7, 1.2, 0.04, 5.8, LIGHT_BLUE)  # divider
            _render_col(left_col,  0.4, 6.1)
            _render_col(right_col, 6.9, 6.1)

        # ── CONTENT (bullets) ────────────────────────────────────
        else:
            y_start = 1.4
            if content:
                _add_textbox(slide, 0.7, y_start, 11.8, 0.65,
                             content, 17, color=DARK_TEXT)
                y_start += 0.7

            txb = slide.shapes.add_textbox(
                PptxInches(0.7), PptxInches(y_start),
                PptxInches(11.8), PptxInches(7.5 - y_start - 0.3)
            )
            txb.text_frame.word_wrap = True
            for i, bullet in enumerate(bullets[:5]):
                p = txb.text_frame.paragraphs[0] if i == 0 else txb.text_frame.add_paragraph()
                p.text = f"▸  {bullet}"
                p.font.size  = PptxPt(19)
                p.font.color.rgb = DARK_TEXT
                p.space_after = PptxPt(10)

        _apply_notes(slide, notes)

    if not filename:
        safe_title = "".join(c if c.isalnum() or c in " -_" else "" for c in title)
        filename = f"{safe_title}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    filepath = OUTPUT_DIR / f"{filename}.pptx"
    prs.save(str(filepath))

    return json.dumps({
        "status": "created",
        "file_path": str(filepath.resolve()),
        "filename": f"{filename}.pptx",
    })


def create_excel(
    title: str,
    sheets: str,
    filename: str = "",
) -> str:
    """
    Create an Excel workbook (.xlsx) with structured data, formatting, and optional formulas.

    Args:
        title: Workbook title (used for the default sheet name if only one sheet).
        sheets: JSON string of sheets. Each sheet is an object with:
            - "name" (str): Sheet/tab name
            - "headers" (list[str]): Column header names
            - "rows" (list[list]): Data rows (each row is a list of cell values)
            - "column_widths" (list[int], optional): Width for each column
            - "formulas" (list[dict], optional): Each formula: {"cell": "C10", "formula": "=SUM(C2:C9)"}
            - "freeze_panes" (str, optional): Cell to freeze at (e.g., "A2" freezes header row)
        filename: Output filename (without extension). Auto-generated if empty.

    Returns:
        JSON string with the file path of the created workbook.
    """
    try:
        sheets_data = json.loads(sheets)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid sheets JSON: {e}"})

    wb = Workbook()

    # Style constants — palette matches Word (navy headers) and PPTX (light-blue alt rows)
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="1F3864", end_color="1F3864", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )
    alt_fill = PatternFill(start_color="D6E4F7", end_color="D6E4F7", fill_type="solid")

    for sheet_idx, sheet_data in enumerate(sheets_data):
        sheet_name = sheet_data.get("name", title if sheet_idx == 0 else f"Sheet{sheet_idx + 1}")
        headers = sheet_data.get("headers", [])
        rows = sheet_data.get("rows", [])
        column_widths = sheet_data.get("column_widths", [])
        formulas = sheet_data.get("formulas", [])
        freeze_panes = sheet_data.get("freeze_panes", "A2")

        if sheet_idx == 0:
            ws = wb.active
            ws.title = sheet_name
        else:
            ws = wb.create_sheet(title=sheet_name)

        # Write headers
        for col_idx, header in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment
            cell.border = thin_border

        # Write data rows
        for row_idx, row_data in enumerate(rows, start=2):
            for col_idx, cell_value in enumerate(row_data, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=cell_value)
                cell.border = thin_border
                cell.alignment = Alignment(vertical="center")

                # Alternate row shading
                if row_idx % 2 == 0:
                    cell.fill = alt_fill

                # Auto-detect numeric values for right alignment
                if isinstance(cell_value, (int, float)):
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                    # Format currency-like values
                    if isinstance(cell_value, float):
                        cell.number_format = '#,##0.00'

        # Apply formulas
        for formula_data in formulas:
            cell_ref = formula_data.get("cell", "")
            formula = formula_data.get("formula", "")
            if cell_ref and formula:
                cell = ws[cell_ref]
                cell.value = formula
                cell.font = Font(bold=True)
                cell.border = thin_border

        # Set column widths
        if column_widths:
            for col_idx, width in enumerate(column_widths, start=1):
                ws.column_dimensions[get_column_letter(col_idx)].width = width
        else:
            # Auto-size based on content
            for col_idx in range(1, len(headers) + 1):
                max_len = len(str(headers[col_idx - 1])) if col_idx <= len(headers) else 8
                for row_idx in range(2, len(rows) + 2):
                    cell_val = ws.cell(row=row_idx, column=col_idx).value
                    if cell_val:
                        max_len = max(max_len, len(str(cell_val)))
                ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 4, 50)

        # Freeze panes
        if freeze_panes:
            ws.freeze_panes = freeze_panes

    if not filename:
        safe_title = "".join(c if c.isalnum() or c in " -_" else "" for c in title)
        filename = f"{safe_title}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    filepath = OUTPUT_DIR / f"{filename}.xlsx"
    wb.save(str(filepath))

    return json.dumps({
        "status": "created",
        "file_path": str(filepath.resolve()),
        "filename": f"{filename}.xlsx",
    })
