"""Text extraction for uploaded / imported documents: pdf, hwp, hwpx, docx, txt, md."""
import html
import re
import subprocess
import sys
import zipfile
from datetime import date
from xml.etree.ElementTree import ParseError as ET_ParseError
from pathlib import Path

SUPPORTED = {".pdf", ".hwp", ".hwpx", ".docx", ".txt", ".md"}
# "2025년 4월 18일", "2025 5월 19일", "2025 7.24", "2025.3.24"
DATE = re.compile(r"(\d{4})\s*[년.\-]?\s*(\d{1,2})\s*[월.\-]\s*(\d{1,2})")
MD = re.compile(r"(\d{1,2})월\s*(\d{1,2})일")


class ExtractError(Exception):
    pass


def md_table(rows: list[list[str]]) -> str:
    """Rows of cell text -> a markdown table (kept as text so it is indexed, searchable and rendered later)."""
    rows = [[re.sub(r"\s+", " ", c.replace("|", "/")).strip() for c in r] for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return ""
    w = max(len(r) for r in rows)
    rows = [r + [""] * (w - len(r)) for r in rows]
    if w == 1:  # a one-cell "table" is just a text box
        return "\n".join(r[0] for r in rows)
    out = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join("---" for _ in range(w)) + " |"]
    return "\n".join(out + ["| " + " | ".join(r) + " |" for r in rows[1:]])


def _hwpx(p: Path) -> str:
    import xml.etree.ElementTree as ET
    hp = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"

    def cell_text(tc) -> str:
        return " ".join(t.text for t in tc.iter(hp + "t") if t.text)

    def table(tbl) -> str:
        return md_table([[cell_text(tc) for tc in tr.findall(hp + "tc")] for tr in tbl.findall(hp + "tr")])

    out: list[str] = []
    with zipfile.ZipFile(p) as z:
        for n in sorted(z.namelist()):
            if n.startswith("Contents/section") and n.endswith(".xml"):
                for para in ET.fromstring(z.read(n)).findall(hp + "p"):
                    own = []
                    tables = []
                    for run in para.findall(hp + "run"):
                        for child in run:
                            if child.tag == hp + "t" and child.text:
                                own.append(child.text)
                            elif child.tag == hp + "tbl":
                                tables.append(table(child))
                    line = html.unescape("".join(own)).strip()
                    if line:
                        out.append(line)
                    out.extend(t for t in tables if t)
    return "\n".join(out)


def _odt_blocks(odt: Path) -> str:
    import xml.etree.ElementTree as ET
    ns = {"text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0", "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
          "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0"}
    T, TB = "{%s}" % ns["text"], "{%s}" % ns["table"]
    with zipfile.ZipFile(odt) as z:
        root = ET.fromstring(z.read("content.xml"))
    body = root.find("office:body/office:text", ns)

    def txt(el) -> str:
        return "".join(el.itertext()).strip()

    def table(tbl) -> str:
        rows = []
        for tr in tbl.iter(TB + "table-row"):
            cells = []
            for c in tr:
                if c.tag in (TB + "table-cell", TB + "covered-table-cell"):
                    t = " ".join(txt(p) for p in c.iter(T + "p") if txt(p))
                    reps = int(c.get(TB + "number-columns-repeated", "1"))
                    cells.extend([t] * (reps if t and reps < 20 else 1))
            while cells and not cells[-1]:
                cells.pop()
            rows.append(cells)
        return md_table(rows)

    out: list[str] = []

    def walk(el):
        for ch in el:
            if ch.tag in (T + "p", T + "h"):
                t = txt(ch)
                if t:
                    out.append(t)
            elif ch.tag == TB + "table":
                t = table(ch)
                if t:
                    out.append(t)
            elif ch.tag in (T + "section", T + "list", T + "list-item"):
                walk(ch)

    walk(body)
    return "\n".join(out)


def _hwp(p: Path) -> str:
    """hwp -> odt (keeps tables) -> markdown-ish text; falls back to plain hwp5txt text."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        odt = Path(tmp) / "doc.odt"
        code = "import sys; from hwp5.hwp5odt import main; sys.argv=['hwp5odt','--output',sys.argv[1],sys.argv[2]]; main()"
        r = subprocess.run([sys.executable, "-c", code, str(odt), str(p)], capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and odt.exists():
            try:
                text = _odt_blocks(odt)
                if text.strip():
                    return text
            except (zipfile.BadZipFile, KeyError, ValueError, ET_ParseError):
                pass
    code = "import sys; from hwp5.hwp5txt import main; sys.argv = ['hwp5txt', sys.argv[1]]; main()"
    r = subprocess.run([sys.executable, "-c", code, str(p)], capture_output=True, text=True)
    if r.returncode:
        raise ExtractError("hwp 파일을 읽지 못했습니다")
    return r.stdout


def _markitdown(p: Path) -> str:
    from markitdown import MarkItDown
    return MarkItDown().convert(str(p)).text_content


def read_text(p: Path) -> str:
    ext = p.suffix.lower()
    try:
        if ext == ".hwpx":
            t = _hwpx(p)
        elif ext == ".hwp":
            t = _hwp(p)
        elif ext in (".txt", ".md"):
            t = p.read_text(encoding="utf-8", errors="ignore")
        else:
            t = _markitdown(p)
    except ExtractError:
        raise
    except Exception as e:  # noqa: BLE001 - any parser failure is a user-facing "can't read"
        raise ExtractError(f"{ext} 파일을 읽지 못했습니다") from e
    return re.sub(r"<표>|[ \t]+\n", "", t).strip()


def find_date(text: str, name: str = "") -> date | None:
    for src in (text[:600], name, text):
        for m in DATE.finditer(src):
            try:
                return date(int(m[1]), int(m[2]), int(m[3]))
            except ValueError:
                continue
    year = re.search(r"(20\d{2})", name)
    m = MD.search(text) or MD.search(name)
    if year and m:
        try:
            return date(int(year[1]), int(m[1]), int(m[2]))
        except ValueError:
            pass
    return None
