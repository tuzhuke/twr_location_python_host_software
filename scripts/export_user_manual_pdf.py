# -*- coding: utf-8 -*-
"""Export USER_MANUAL.md to a PDF with table-safe page layout."""
import os
import re
import sys
from pathlib import Path

from markdown import markdown
from PyQt5 import QtCore, QtGui, QtWidgets, QtPrintSupport


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_MD = PROJECT_ROOT / "USER_MANUAL.md"
OUTPUT_PDF = PROJECT_ROOT / "USER_MANUAL.pdf"


def build_html(markdown_text):
    """Convert Markdown to styled HTML suitable for Qt PDF printing."""
    html_body = markdown(
        markdown_text,
        extensions=["extra", "tables", "fenced_code", "sane_lists"],
        output_format="html5",
    )

    # Python-Markdown emits inline text-align styles for columns declared with
    # ---:. Qt's PDF renderer can place right-aligned first-column table text
    # too close to the page edge, so normalize table cell alignment.
    html_body = re.sub(r'\sstyle="text-align:\s*(left|right|center);?"', "", html_body)

    css = r"""
<style>
body {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "SimSun", sans-serif;
    color: #1f2937;
    font-size: 4pt;
    line-height: 1.22;
    margin: 0;
    padding: 0;
}
h1 {
    color: #0f2742;
    font-size: 7.2pt;
    margin: 0 0 5px 0;
    padding-bottom: 3px;
    border-bottom: 2px solid #1f5f8b;
}
h2 {
    color: #12395a;
    font-size: 5.5pt;
    margin: 7px 0 3px 0;
    padding-bottom: 2px;
    border-bottom: 1px solid #c7d3df;
}
h3 {
    color: #1d4f73;
    font-size: 4.6pt;
    margin: 5px 0 3px 0;
}
p { margin: 2px 0; }
ul, ol { margin-top: 2px; margin-bottom: 3px; }
li { margin: 1px 0; }
table {
    border-collapse: collapse;
    width: 96%;
    margin: 4px auto 5px auto;
}
th {
    background: #e8eef5;
    color: #0f2742;
    font-weight: 700;
}
th, td {
    border: 1px solid #b8c7d6;
    padding: 2px 3px;
    vertical-align: middle;
    text-align: center;
}
th:first-child, td:first-child {
    padding-left: 3px;
}
code {
    font-family: Consolas, "Microsoft YaHei UI", monospace;
    color: #7a2f16;
    background: #f4f6f8;
}
pre {
    font-family: Consolas, "Microsoft YaHei UI", monospace;
    background: #f4f6f8;
    border: 1px solid #cbd5df;
    padding: 3px;
    white-space: pre-wrap;
}
blockquote {
    border-left: 4px solid #9bb5cb;
    margin-left: 0;
    padding-left: 10px;
    color: #44546a;
}
</style>
"""
    return '<html><head><meta charset="utf-8">%s</head><body>%s</body></html>' % (css, html_body)


def export_pdf(source_md=SOURCE_MD, output_pdf=OUTPUT_PDF):
    """Render the Markdown user manual as an A4 PDF."""
    markdown_text = Path(source_md).read_text(encoding="utf-8")
    html = build_html(markdown_text)

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    document = QtGui.QTextDocument()
    document.setDefaultFont(QtGui.QFont("Microsoft YaHei UI", 4))
    document.setDocumentMargin(2)
    document.setHtml(html)

    printer = QtPrintSupport.QPrinter(QtPrintSupport.QPrinter.HighResolution)
    printer.setOutputFormat(QtPrintSupport.QPrinter.PdfFormat)
    printer.setOutputFileName(str(output_pdf))
    printer.setPageSize(QtPrintSupport.QPrinter.A4)
    printer.setOrientation(QtPrintSupport.QPrinter.Portrait)
    printer.setPageMargins(12, 12, 12, 14, QtPrintSupport.QPrinter.Millimeter)

    page_rect = printer.pageRect(QtPrintSupport.QPrinter.Point)
    document.setPageSize(QtCore.QSizeF(page_rect.width(), page_rect.height()))
    document.print_(printer)

    output_pdf = Path(output_pdf)
    if not output_pdf.exists() or output_pdf.stat().st_size < 1024:
        raise RuntimeError("PDF generation failed: %s" % output_pdf)
    return output_pdf


if __name__ == "__main__":
    pdf = export_pdf()
    sys.stdout.write("%s\n%d\n" % (pdf, pdf.stat().st_size))
