"""Excel / PDF / print layouts for the Customer List and the License Expiry Report.

Both views describe a table the same way (`Table`): a title, a subtitle, column headings and rows of text. The
file builders below turn that into an .xlsx or .pdf; the print view receives it as JSON and draws the page itself.
Rows come from the customer records the caller was already allowed to see, never from the client's own text.
"""
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from django.utils import timezone

from .models import PROJECTS
from .serializers import customer_data

IST = ZoneInfo("Asia/Kolkata")
NAVY = "#012641"
ROW_TINT = "#F4FBFD"


@dataclass
class Table:
    title: str
    subtitle: str
    headings: list
    rows: list  # lists of str
    weights: list  # relative column widths (PDF / Excel)

    def as_json(self):
        return {"title": self.title, "subtitle": self.subtitle, "columns": self.headings, "rows": self.rows}


def _date(value):
    return value.astimezone(IST).strftime("%d/%m/%Y") if value else "—"


def _stamp(value):
    return value.astimezone(IST).strftime("%d/%m/%Y %H:%M") if value else "—"


def _subtitle(count, filters):
    parts = [f"Generated {_stamp(timezone.now())}", f"{count} record{'' if count == 1 else 's'}"]
    if filters:
        parts.append(f"Filters: {filters}")
    return " · ".join(parts)


def customer_table(project_label, customers, filters=""):
    headings = [
        "S.No", "Name", "Company Name", "Contact No.", "Email", "Address", "GSTIN", "License Type", "Status",
        "User ID", "Serial No.", "Trial Days", "Activated On", "Expiry Date", "Created By", "Created On",
    ]
    weights = [3, 9, 10, 8, 12, 14, 9, 7, 8, 8, 8, 4, 7, 7, 7, 8]
    rows = []
    for index, c in enumerate(customers, 1):
        d = customer_data(c)
        rows.append(
            [
                str(index), d["name"], d["company_name"], d["contact_number"], d["email"] or "—", d["address"],
                d["gstin"] or "—", d["license_type_label"], d["status_label"], d["user_id"], d["serial_number"] or "—",
                "—" if d["trial_days"] is None else str(d["trial_days"]), _date(c.activated_at), _date(c.expires_at),
                d["created_by"] or "—", _stamp(c.created_at),
            ]
        )
    return Table(f"{project_label} – Customer List", _subtitle(len(rows), filters), headings, rows, weights)


def expiry_status(customer, now=None):
    return "Expired" if customer.expires_at and customer.expires_at <= (now or timezone.now()) else "Upcoming"


def expiry_table(customers, filters=""):
    headings = [
        "S.No", "Name", "Company Name", "Product", "Product Serial No.", "License Type", "Customer Serial No.",
        "Expiry Date", "Status",
    ]
    weights = [3, 10, 12, 9, 9, 7, 9, 7, 7]
    now = timezone.now()
    rows = [
        [
            str(index), c.name, c.company_name, PROJECTS.get(c.project, c.project), c.serial_number or "—",
            c.get_license_type_display(), c.user_id, _date(c.expires_at), expiry_status(c, now),
        ]
        for index, c in enumerate(customers, 1)
    ]
    return Table("License Expiry Report", _subtitle(len(rows), filters), headings, rows, weights)


def build_xlsx(table):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Report"[:31]
    width = len(table.headings)
    ws.append([table.title])
    ws.append([table.subtitle])
    ws.append([])
    ws.append(table.headings)
    for row in table.rows:
        ws.append(row)

    ws["A1"].font = Font(bold=True, size=14, color=NAVY[1:])
    ws["A2"].font = Font(italic=True, size=9, color="555555")
    thin = Side(style="thin", color="C9DDE6")
    for cell in ws[4]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=NAVY[1:])
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for row in ws.iter_rows(min_row=5, max_row=4 + len(table.rows), max_col=width):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
    total = sum(table.weights)
    for i, weight in enumerate(table.weights, 1):
        ws.column_dimensions[get_column_letter(i)].width = max(6, round(weight / total * width * 16))
    ws.freeze_panes = "A5"
    if table.rows:
        ws.auto_filter.ref = f"A4:{get_column_letter(width)}{4 + len(table.rows)}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def build_pdf(table):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from reportlab.platypus import Table as PdfTable
    from reportlab.platypus import TableStyle

    wide = len(table.headings) > 10
    font = 6.3 if wide else 8.2
    margin = 10 * mm
    page = landscape(A4)
    usable = page[0] - 2 * margin
    head = ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=font, leading=font + 2, textColor=colors.white, alignment=1)
    cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=font, leading=font + 2, textColor=colors.HexColor(NAVY))
    title = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=14, leading=18, textColor=colors.HexColor(NAVY))
    sub = ParagraphStyle("sub", fontName="Helvetica", fontSize=8, leading=11, textColor=colors.HexColor("#4F6B7D"))

    total = sum(table.weights)
    widths = [usable * w / total for w in table.weights]
    data = [[Paragraph(escape(h), head) for h in table.headings]]
    data += [[Paragraph(escape(value), cell) for value in row] for row in table.rows]
    if not table.rows:
        data.append([Paragraph("No records.", cell)] + [""] * (len(table.headings) - 1))

    grid = PdfTable(data, colWidths=widths, repeatRows=1)
    grid.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(NAVY)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C9DDE6")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(ROW_TINT)]),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#4F6B7D"))
        canvas.drawString(margin, 6 * mm, "Sai Infosys Nexus")
        canvas.drawRightString(page[0] - margin, 6 * mm, f"Page {doc.page}")
        canvas.restoreState()

    out = BytesIO()
    doc = SimpleDocTemplate(
        out, pagesize=page, leftMargin=margin, rightMargin=margin, topMargin=margin, bottomMargin=12 * mm, title=table.title, author="Sai Infosys Nexus"
    )
    doc.build([Paragraph(escape(table.title), title), Paragraph(escape(table.subtitle), sub), Spacer(1, 4 * mm), grid], onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()


def file_name(table, extension):
    base = "".join(ch if ch.isalnum() else "-" for ch in table.title).strip("-").lower()
    while "--" in base:
        base = base.replace("--", "-")
    return f"{base}-{datetime.now(IST):%Y%m%d}.{extension}"


def usage_table(rows, filters=""):
    headings = ["S.No", "Customer / Company Name", "Product", "Device / Serial Number", "Last Used Date", "Usage Status"]
    weights = [3, 16, 10, 16, 9, 8]
    body = [
        [str(i), r["customer"] or "—", r["product"], r["device"] or "—", _date(datetime.fromisoformat(r["last_used"])) if r["last_used"] else "—", r["status"]]
        for i, r in enumerate(rows, 1)
    ]
    return Table("Usage Report", _subtitle(len(body), filters), headings, body, weights)
