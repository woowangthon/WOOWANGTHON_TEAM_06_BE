"""Best-effort conversion of hwp / hwpx / docx to PDF so the overlay can show the original layout.

hwp  -> pyhwp (hwp5odt) -> LibreOffice -> pdf
hwpx -> own XML-to-HTML (paragraphs, tables) -> LibreOffice -> pdf
Needs LibreOffice (`soffice`). Without it every call returns None and the UI falls back to the extracted text.
"""
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
MAC_SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"


def soffice() -> str | None:
    return os.environ.get("SOFFICE") or shutil.which("soffice") or (MAC_SOFFICE if Path(MAC_SOFFICE).exists() else None)


def _text(p: ET.Element) -> str:
    out = []
    for run in p.iter(HP + "t"):
        if run.text:
            out.append(run.text)
    return "".join(out)


def _paragraph(p: ET.Element) -> str:
    """One paragraph: its own text, then any tables that sit inside its runs."""
    own = []
    tables = []
    for run in p.findall(HP + "run"):
        for child in run:
            if child.tag == HP + "t" and child.text:
                own.append(child.text)
            elif child.tag == HP + "tbl":
                tables.append(_table(child))
    text = html.escape("".join(own)).strip()
    return (f"<p>{text}</p>" if text else "") + "".join(tables)


def _table(tbl: ET.Element) -> str:
    rows = []
    for tr in tbl.findall(HP + "tr"):
        cells = []
        for tc in tr.findall(HP + "tc"):
            span = tc.find(HP + "cellSpan")
            attrs = ""
            if span is not None:
                if span.get("colSpan", "1") != "1":
                    attrs += f' colspan="{span.get("colSpan")}"'
                if span.get("rowSpan", "1") != "1":
                    attrs += f' rowspan="{span.get("rowSpan")}"'
            inner = "".join(_paragraph(p) for p in tc.iter(HP + "p") if _is_direct_cell_p(tc, p))
            cells.append(f"<td{attrs}>{inner}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return '<table border="1" cellspacing="0" cellpadding="4" style="border-collapse:collapse;width:100%">' + "".join(rows) + "</table>"


def _is_direct_cell_p(tc: ET.Element, p: ET.Element) -> bool:
    """Paragraphs of this cell, not of tables nested inside it (those are rendered by _paragraph)."""
    for sub in tc.iter(HP + "subList"):
        if p in list(sub):
            return True
    return False


def hwpx_to_html(path: Path) -> str:
    body = []
    with zipfile.ZipFile(path) as z:
        for name in sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml", n)):
            root = ET.fromstring(z.read(name))
            body.extend(_paragraph(p) for p in root.findall(HP + "p"))
    return '<html><head><meta charset="utf-8"><style>body{font-family:"Apple SD Gothic Neo","NanumGothic",sans-serif;font-size:10pt}p{margin:2pt 0}</style></head><body>' + "".join(body) + "</body></html>"


def _soffice_pdf(src: Path, out_dir: Path, html_input: bool = False) -> Path | None:
    exe = soffice()
    if not exe:
        return None
    fmt = "pdf:writer_web_pdf_Export" if html_input else "pdf"
    with tempfile.TemporaryDirectory() as profile:  # private profile: no clash with a running LibreOffice
        r = subprocess.run([exe, f"-env:UserInstallation=file://{profile}", "--headless", "--convert-to", fmt, "--outdir", str(out_dir), str(src)],
                           capture_output=True, text=True, timeout=120)
    pdf = out_dir / (src.stem + ".pdf")
    return pdf if r.returncode == 0 and pdf.exists() else None


def to_pdf(src: Path, dest: Path) -> bool:
    """Convert src (.hwp, .hwpx, .docx) to dest (.pdf). False when it cannot be done."""
    ext = src.suffix.lower()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            if ext == ".hwp":
                odt = tmp / "doc.odt"
                r = subprocess.run([sys.executable, "-c", "import sys; from hwp5.hwp5odt import main; sys.argv=['hwp5odt','--output',sys.argv[1],sys.argv[2]]; main()", str(odt), str(src)],
                                   capture_output=True, text=True, timeout=120)
                pdf = _soffice_pdf(odt, tmp) if r.returncode == 0 and odt.exists() else None
            elif ext == ".hwpx":
                page = tmp / "doc.html"
                page.write_text(hwpx_to_html(src), encoding="utf-8")
                pdf = _soffice_pdf(page, tmp, html_input=True)
            elif ext == ".docx":
                work = tmp / "doc.docx"
                shutil.copyfile(src, work)
                pdf = _soffice_pdf(work, tmp)
            else:
                return False
            if not pdf:
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(pdf, dest)
            return True
    except (subprocess.SubprocessError, ET.ParseError, zipfile.BadZipFile, OSError):
        return False
