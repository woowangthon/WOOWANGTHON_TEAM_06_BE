"""Split a document into ~500 character passages so retrieval and answers can point at the relevant part."""


def chunk_text(body: str, size: int = 500) -> list[str]:
    out, cur = [], ""
    for line in body.splitlines():
        while len(line) > size:  # very long line: hard split
            if cur:
                out.append(cur); cur = ""
            out.append(line[:size]); line = line[size:]
        in_table = line.startswith("|") and cur.rsplit("\n", 1)[-1].startswith("|")
        if cur and not (in_table and len(cur) < 3000) and len(cur) + len(line) + 1 > size:  # tables stay whole
            out.append(cur); cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur.strip():
        out.append(cur)
    return [c.strip() for c in out if c.strip()]
