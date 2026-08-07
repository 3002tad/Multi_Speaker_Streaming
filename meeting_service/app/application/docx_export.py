from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape


CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _paragraph(text: str, *, style: str | None = None) -> str:
    style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{style_xml}<w:r><w:t xml:space="preserve">{escape(str(text))}</w:t></w:r></w:p>'


def _item_text(item: dict, field: str = "content") -> str:
    speaker = item.get("speaker")
    value = item.get(field) or ""
    return f"{speaker}: {value}" if speaker else str(value)


def render_minutes_docx(document: dict, *, official: bool) -> bytes:
    """Render a dependency-free DOCX suitable for the demo/export contract."""
    meeting = document.get("meeting") or {}
    watermark = "BẢN CHÍNH THỨC" if official else "DỰ THẢO"
    paragraphs = [
        _paragraph("BIÊN BẢN CUỘC HỌP", style="Title"),
        _paragraph(watermark, style="Subtitle"),
        _paragraph(meeting.get("title") or document.get("title") or "Cuộc họp", style="Heading1"),
    ]
    if meeting.get("started_at"):
        paragraphs.append(_paragraph(f"Thời gian bắt đầu: {meeting['started_at']}"))
    paragraphs.append(_paragraph("TÓM TẮT", style="Heading1"))
    for item in document.get("summary") or []:
        paragraphs.append(_paragraph(f"• {_item_text(item)}"))
    paragraphs.append(_paragraph("NỘI DUNG CHÍNH", style="Heading1"))
    for topic in document.get("topics") or []:
        paragraphs.append(_paragraph(topic.get("title") or "Chủ đề", style="Heading2"))
        for group_name, field in (("Chi tiết", "details"), ("Đề xuất / phát biểu", "proposals"), ("Quyết định / thống nhất", "decisions")):
            items = topic.get(field) or []
            if items:
                paragraphs.append(_paragraph(group_name, style="Heading3"))
                paragraphs.extend(_paragraph(f"• {_item_text(item)}") for item in items)
        actions = topic.get("actions") or []
        if actions:
            paragraphs.append(_paragraph("Việc cần làm", style="Heading3"))
            for action in actions:
                owner = f" | Phụ trách: {action.get('owner')}" if action.get("owner") else ""
                deadline = f" | Hạn: {action.get('deadline')}" if action.get("deadline") else ""
                paragraphs.append(_paragraph(f"• {action.get('task') or ''}{owner}{deadline}"))
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>""".format(body="".join(paragraphs))
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>"""
    relationships = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>"""
    document_relationships = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>"""
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/_rels/document.xml.rels", document_relationships)
    return output.getvalue()
