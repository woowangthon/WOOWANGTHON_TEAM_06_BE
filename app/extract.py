"""Text extraction for uploaded / imported documents: pdf, hwp, hwpx, docx, txt, md."""
import html
import re
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

SUPPORTED = {".pdf", ".hwp", ".hwpx", ".docx", ".txt", ".md"}
# "2025년 4월 18일", "2025 5월 19일", "2025 7.24", "2025.3.24"
DATE = re.compile(r"(\d{4})\s*[년.\-]?\s*(\d{1,2})\s*[월.\-]\s*(\d{1,2})")
MD = re.compile(r"(\d{1,2})월\s*(\d{1,2})일")


class ExtractError(Exception):
    pass


def _hwpx(p: Path) -> str:
    out = []
    with zipfile.ZipFile(p) as z:
        for n in sorted(z.namelist()):
            if n.startswith("Contents/section") and n.endswith(".xml"):
                for para in re.findall(r"<hp:p[ >].*?</hp:p>", z.read(n).decode("utf-8", "ignore"), flags=re.S):
                    t = html.unescape(re.sub(r"<[^>]+>", "", "".join(re.findall(r"<hp:t>(.*?)</hp:t>", para, flags=re.S)))).strip()
                    if t:
                        out.append(t)
    return "\n".join(out)


def _hwp(p: Path) -> str:
    code = "import sys; from hwp5.hwp5txt import main; sys.argv = ['hwp5txt', sys.argv[1]]; main()"
    r = subprocess.run([sys.executable, "-c", code, str(p)], capture_output=True, text=True)
    if r.returncode:
        raise ExtractError("hwp 파일을 읽지 못했다")
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
        raise ExtractError(f"{ext} 파일을 읽지 못했다") from e
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
