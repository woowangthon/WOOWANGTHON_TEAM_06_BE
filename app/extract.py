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


def _is_red(color: str) -> bool:
    m = re.fullmatch(r"#?([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})", color.strip())
    if not m:
        return False
    r, g, b = (int(x, 16) for x in m.groups())
    return r >= 190 and g <= 90 and b <= 90


def red(t: str) -> str:
    """Text that was red in the original (e.g. a time that changed) is kept as {{r:...}} so the page can show it in red."""
    return "{{r:" + t + "}}" if t.strip() else t


def _hwpx(p: Path) -> str:
    import xml.etree.ElementTree as ET
    hp = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
    red_ids: set[str] = set()
    with zipfile.ZipFile(p) as z:
        if "Contents/header.xml" in z.namelist():
            for el in ET.fromstring(z.read("Contents/header.xml")).iter():
                if el.tag.endswith("}charPr") and _is_red(el.get("textColor", "")):
                    red_ids.add(el.get("id", ""))

    def run_text(run) -> str:
        t = "".join(x.text or "" for x in run.findall(hp + "t"))
        return red(t) if run.get("charPrIDRef") in red_ids else t

    def cell_text(tc) -> str:
        return " ".join("".join(run_text(r) for r in para.findall(hp + "run")).strip() for para in tc.iter(hp + "p")).strip()

    def table(tbl) -> str:
        return md_table([[cell_text(tc) for tc in tr.findall(hp + "tc")] for tr in tbl.findall(hp + "tr")])

    out: list[str] = []
    with zipfile.ZipFile(p) as z:
        for n in sorted(z.namelist()):
            if n.startswith("Contents/section") and n.endswith(".xml"):
                for para in ET.fromstring(z.read(n)).findall(hp + "p"):
                    own, tables = [], []
                    for run in para.findall(hp + "run"):
                        own.append(run_text(run))
                        tables.extend(table(t) for t in run.findall(hp + "tbl"))
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


def _hwp_html(p: Path) -> str:
    """hwp -> xhtml + css (pyhwp keeps text colours) -> text with markdown tables and {{r:...}} for red text."""
    import tempfile
    import xml.etree.ElementTree as ET
    X = "{http://www.w3.org/1999/xhtml}"
    with tempfile.TemporaryDirectory() as tmp:
        code = "import sys; from hwp5.hwp5html import main; sys.argv=['hwp5html','--output',sys.argv[1],sys.argv[2]]; main()"
        r = subprocess.run([sys.executable, "-c", code, tmp, str(p)], capture_output=True, text=True, timeout=120)
        page, css = Path(tmp) / "index.xhtml", Path(tmp) / "styles.css"
        if r.returncode or not page.exists():
            raise ExtractError("hwp 파일을 읽지 못했습니다")
        red_ids = {m[1] for m in re.finditer(r"span\.charshape-(\d+)\s*\{[^}]*?color:\s*(#[0-9a-fA-F]{6})", css.read_text(encoding="utf-8", errors="ignore"))
                   if _is_red(m[2])} if css.exists() else set()
        root = ET.fromstring(page.read_bytes())

    def inline(el) -> str:
        out = [el.text or ""]
        for ch in el:
            t = inline(ch)
            if ch.tag == X + "span" and any(c.startswith("charshape-") and c.split("-")[1] in red_ids for c in (ch.get("class") or "").split()):
                t = red(t)
            out.append(t + (ch.tail or ""))
        return "".join(out).replace("\r", "")

    def table(tbl) -> str:
        rows = []
        for tr in tbl.iter(X + "tr"):
            cells = []
            for td in tr:
                if td.tag in (X + "td", X + "th"):
                    cells.append(" ".join(x for x in (inline(pp).strip() for pp in td.iter(X + "p")) if x))
                    cells.extend([""] * (int(td.get("colspan", "1") or 1) - 1))
            rows.append(cells)
        return md_table(rows)

    out: list[str] = []

    def walk(el):
        for ch in el:
            if ch.tag == X + "table":
                t = table(ch)
                if t:
                    out.append(t)
            elif ch.tag == X + "p":
                t = re.sub(r"[ \t]+", " ", inline(ch)).strip()
                if t:
                    out.append(t)
            elif ch.tag in (X + "div", X + "body", X + "section", X + "ul", X + "li"):
                walk(ch)

    body = root.find(X + "body")
    walk(body if body is not None else root)
    return "\n".join(out)


def _hwp(p: Path) -> str:
    """hwp text with tables and colours; falls back to the odt route, then to plain hwp5txt."""
    try:
        text = _hwp_html(p)
        if text.strip():
            return text
    except (ExtractError, ET_ParseError, subprocess.SubprocessError, OSError):
        pass
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
