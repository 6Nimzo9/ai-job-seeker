"""Professional A4 cover-letter PDF renderer for the local AI Job Seeker project."""
from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape


def _plain(value: object, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def build_cover_letter_pdf(
    pdf_path: str | Path,
    letter: str,
    *,
    job_title: str = "",
    company_name: str = "",
    candidate_name: str | None = None,
) -> Path:
    """Render a supplied letter as a clean A4 PDF without rewriting its claims."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

    body = str(letter or "").strip()
    if not body:
        raise ValueError("Cover letter is empty; PDF was not created.")

    destination = Path(pdf_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    name = _plain(candidate_name or os.getenv("JOB_SEEKER_CANDIDATE_NAME", "Claudia"), 120) or "Claudia"
    role = _plain(job_title, 300)
    company = _plain(company_name, 300)
    navy = colors.HexColor("#172B4D")
    blue = colors.HexColor("#2367A8")
    slate = colors.HexColor("#4B5563")
    light = colors.HexColor("#D9E2F0")

    styles = getSampleStyleSheet()
    name_style = ParagraphStyle("ApplicantName", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=navy, spaceAfter=2)
    kicker_style = ParagraphStyle("Kicker", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8, leading=11, textColor=blue, spaceAfter=8)
    meta_style = ParagraphStyle("Meta", parent=styles["Normal"], fontName="Helvetica", fontSize=9, leading=12, textColor=slate, spaceAfter=3)
    section_style = ParagraphStyle("Section", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=blue, spaceAfter=6)
    body_style = ParagraphStyle("LetterBody", parent=styles["Normal"], fontName="Helvetica", fontSize=10.4, leading=15.2, textColor=colors.HexColor("#202939"), alignment=TA_LEFT, spaceAfter=9, allowWidows=0, allowOrphans=0)
    greeting_style = ParagraphStyle("Greeting", parent=body_style, fontName="Helvetica-Bold", spaceAfter=11)
    date_style = ParagraphStyle("Date", parent=styles["Normal"], fontName="Helvetica", fontSize=8.5, leading=11, textColor=slate, alignment=TA_RIGHT)
    close_style = ParagraphStyle("Closing", parent=body_style, spaceBefore=2, spaceAfter=3)
    signature_style = ParagraphStyle("Signature", parent=body_style, fontName="Helvetica-Bold", textColor=navy, spaceBefore=0, spaceAfter=0)

    lines = body.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    signoff = re.compile(r"^(sincerely|best regards|kind regards|regards|yours sincerely|respectfully)[,\.]?$", re.I)
    if lines and signoff.match(lines[-1].strip()):
        lines.pop()
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].strip().casefold() == name.casefold():
        lines.pop()
    body = "\n".join(lines).strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    if not paragraphs:
        raise ValueError("Cover letter contains no readable paragraphs; PDF was not created.")

    story = [
        Paragraph(escape(name), name_style),
        Paragraph("AI AUTOMATION &amp; WORKFLOW AUTOMATION", kicker_style),
        HRFlowable(width="100%", thickness=1.2, color=blue, spaceBefore=0, spaceAfter=13),
        Paragraph("COVER LETTER", section_style),
    ]
    if role:
        story.append(Paragraph(f"<b>Application for:</b> {escape(role)}", meta_style))
    if company:
        story.append(Paragraph(f"<b>Company:</b> {escape(company)}", meta_style))
    story.append(Paragraph(date.today().strftime("%d %B %Y"), date_style))
    story.append(Spacer(1, 13))

    for index, paragraph in enumerate(paragraphs):
        safe = escape(paragraph).replace("\n", "<br/>")
        style = greeting_style if index == 0 and re.match(r"^(dear|hello)\b", paragraph, re.I) else body_style
        story.append(Paragraph(safe, style))

    story.extend([Spacer(1, 3), Paragraph("Kind regards,", close_style), Paragraph(escape(name), signature_style)])

    def draw_page(canvas, document):
        canvas.saveState()
        width, _height = A4
        canvas.setStrokeColor(light)
        canvas.setLineWidth(0.5)
        canvas.line(document.leftMargin, 1.25 * cm, width - document.rightMargin, 1.25 * cm)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(slate)
        canvas.drawString(document.leftMargin, 0.88 * cm, f"{name}  |  Cover Letter")
        canvas.drawRightString(width - document.rightMargin, 0.88 * cm, f"Page {document.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(
        str(destination), pagesize=A4, leftMargin=2.25 * cm, rightMargin=2.25 * cm,
        topMargin=1.65 * cm, bottomMargin=1.75 * cm,
        title=f"Cover Letter - {role or 'Application'}", author=name,
        subject=f"Application to {company}" if company else "Job application cover letter",
        creator="AI Job Seeker - Professional Cover Letter Layout",
    )
    document.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    if not destination.is_file() or destination.stat().st_size < 100:
        raise RuntimeError(f"PDF verification failed: {destination}")
    return destination
