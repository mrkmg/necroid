"""Tiny markdown -> tkinter Text renderer. Stdlib only.

Subset supported (sufficient for the per-mod READMEs we author):

    # / ## / ### / ####     ATX headings
    paragraphs               blank-line separated
    - foo / * foo            bullet lists (one or two indent levels)
    1. foo                   ordered lists
    ```...```                fenced code blocks
    `code`                   inline code
    **bold** / *italic*      emphasis
    [text](url)              clickable links (opens in default browser)
    ---                      horizontal rule
    | a | b |                pipe tables (header row + --- separator + body)

Anything else is rendered as plain text. The renderer never raises on malformed
input — worst case the user sees the raw markdown verbatim.

Usage:

    text_widget = tk.Text(parent, ...)
    markdown_render.render(md_source, text_widget, palette)

Note: leaves the widget in state=DISABLED on return; the caller doesn't need to
toggle state to enable selection/copy (DISABLED still allows that, only typing
is blocked).
"""
from __future__ import annotations

import re
import tkinter as tk
import webbrowser

# Inline token regex: code spans, bold, italic, links — in that priority order.
# Code spans win first so backticks suppress inner */_/[ parsing.
_INLINE_RE = re.compile(
    r"(?P<code>`+)(?P<code_body>.+?)(?P=code)"
    r"|\*\*(?P<bold>.+?)\*\*"
    r"|(?<![A-Za-z0-9])_(?P<itl_u>[^_\n]+)_(?![A-Za-z0-9])"
    r"|\*(?P<itl>[^*\n]+)\*"
    r"|\[(?P<link_text>[^\]]+)\]\((?P<link_url>[^)\s]+)\)",
    re.DOTALL,
)

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$")
_HR_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_FENCE_RE = re.compile(r"^\s*```")
_BULLET_RE = re.compile(r"^(\s*)([-*])\s+(.*)$")
_ORDERED_RE = re.compile(r"^(\s*)(\d+)\.\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")


def render(text: str, widget: tk.Text, palette: dict) -> None:
    """Render markdown ``text`` into ``widget`` using palette colors."""
    _configure_tags(widget, palette)

    widget.configure(state=tk.NORMAL)
    widget.delete("1.0", tk.END)

    link_counter = [0]
    blocks = _split_blocks(text.splitlines())

    for i, block in enumerate(blocks):
        if i > 0:
            # Blank line between blocks. Headings already pad themselves below;
            # keep the gap small for tight visual rhythm.
            widget.insert(tk.END, "\n")
        _render_block(widget, block, palette, link_counter)

    widget.configure(state=tk.DISABLED)


# --- block-level tokenizer -------------------------------------------------


def _split_blocks(lines: list[str]) -> list[dict]:
    """Group raw lines into typed blocks. Returns list of dicts with a
    ``kind`` field and kind-specific payload."""
    blocks: list[dict] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # Fenced code block — capture until closing fence (or EOF).
        if _FENCE_RE.match(line):
            i += 1
            buf: list[str] = []
            while i < n and not _FENCE_RE.match(lines[i]):
                buf.append(lines[i])
                i += 1
            if i < n:
                i += 1  # consume closing fence
            blocks.append({"kind": "code", "lines": buf})
            continue

        if _HR_RE.match(line):
            blocks.append({"kind": "hr"})
            i += 1
            continue

        m = _HEADING_RE.match(stripped)
        if m:
            blocks.append({
                "kind": "heading",
                "level": len(m.group(1)),
                "text": m.group(2),
            })
            i += 1
            continue

        # Pipe table: a header line, then a separator like |---|---|, then body.
        if "|" in stripped and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            header = _split_table_row(lines[i])
            i += 2  # header + separator
            rows: list[list[str]] = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(_split_table_row(lines[i]))
                i += 1
            blocks.append({"kind": "table", "header": header, "rows": rows})
            continue

        if _BULLET_RE.match(line) or _ORDERED_RE.match(line):
            items: list[dict] = []
            while i < n:
                bm = _BULLET_RE.match(lines[i])
                om = _ORDERED_RE.match(lines[i])
                if not (bm or om):
                    if not lines[i].strip():
                        # blank line ends the list
                        break
                    # continuation of previous item: indented line under it
                    if items and lines[i].startswith((" ", "\t")):
                        items[-1]["text"] += " " + lines[i].strip()
                        i += 1
                        continue
                    break
                if bm:
                    indent, _bullet, body = bm.group(1), bm.group(2), bm.group(3)
                    ordered = False
                    marker = ""
                else:
                    indent, num, body = om.group(1), om.group(2), om.group(3)
                    ordered = True
                    marker = num
                items.append({
                    "indent": len(indent.expandtabs(4)) // 2,
                    "ordered": ordered,
                    "marker": marker,
                    "text": body,
                })
                i += 1
            blocks.append({"kind": "list", "items": items})
            continue

        # Default: paragraph — gather contiguous non-empty, non-special lines.
        buf = [line]
        i += 1
        while i < n:
            nxt = lines[i]
            if not nxt.strip():
                break
            if (_HEADING_RE.match(nxt.strip()) or _HR_RE.match(nxt)
                    or _FENCE_RE.match(nxt) or _BULLET_RE.match(nxt)
                    or _ORDERED_RE.match(nxt)):
                break
            buf.append(nxt)
            i += 1
        blocks.append({"kind": "paragraph", "text": " ".join(s.strip() for s in buf)})

    return blocks


def _split_table_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


# --- block renderers -------------------------------------------------------


def _render_block(widget: tk.Text, block: dict, palette: dict,
                  link_counter: list[int]) -> None:
    kind = block["kind"]

    if kind == "heading":
        tag = f"h{block['level']}"
        widget.insert(tk.END, block["text"] + "\n", tag)
        return

    if kind == "paragraph":
        _render_inline(widget, block["text"], palette, link_counter)
        widget.insert(tk.END, "\n")
        return

    if kind == "code":
        widget.insert(tk.END, "\n", "codeblock_pad")
        for ln in block["lines"]:
            widget.insert(tk.END, ln + "\n", "codeblock")
        widget.insert(tk.END, "\n", "codeblock_pad")
        return

    if kind == "hr":
        # A run of em-dashes works visually under any monospace/proportional
        # mix without needing a separate Frame widget.
        widget.insert(tk.END, "─" * 60 + "\n", "hr")
        return

    if kind == "list":
        for item in block["items"]:
            indent = "    " * item["indent"]
            bullet = (item["marker"] + ". ") if item["ordered"] else "• "
            widget.insert(tk.END, indent + bullet, "list_marker")
            _render_inline(widget, item["text"], palette, link_counter)
            widget.insert(tk.END, "\n")
        return

    if kind == "table":
        # Render with column padding computed from the widest cell per column.
        header = block["header"]
        rows = block["rows"]
        cols = max(len(header), max((len(r) for r in rows), default=0))
        widths = [0] * cols
        for row in [header, *rows]:
            for j, cell in enumerate(row):
                if j < cols:
                    widths[j] = max(widths[j], len(cell))

        def fmt(row: list[str]) -> str:
            cells = []
            for j in range(cols):
                cell = row[j] if j < len(row) else ""
                cells.append(cell.ljust(widths[j]))
            return "  ".join(cells)

        widget.insert(tk.END, fmt(header) + "\n", "table_header")
        widget.insert(tk.END, "  ".join("-" * w for w in widths) + "\n", "table_sep")
        for row in rows:
            widget.insert(tk.END, fmt(row) + "\n", "table_row")
        return


# --- inline renderer -------------------------------------------------------


def _render_inline(widget: tk.Text, text: str, palette: dict,
                   link_counter: list[int]) -> None:
    """Walk text, splitting at inline tokens and inserting each chunk with
    the appropriate tag."""
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            widget.insert(tk.END, text[pos:m.start()])
        if m.group("code") is not None:
            widget.insert(tk.END, m.group("code_body"), "code")
        elif m.group("bold") is not None:
            widget.insert(tk.END, m.group("bold"), "bold")
        elif m.group("itl") is not None:
            widget.insert(tk.END, m.group("itl"), "italic")
        elif m.group("itl_u") is not None:
            widget.insert(tk.END, m.group("itl_u"), "italic")
        elif m.group("link_text") is not None:
            url = m.group("link_url")
            link_counter[0] += 1
            tag = f"link_{link_counter[0]}"
            widget.insert(tk.END, m.group("link_text"), ("link", tag))
            # Per-link tag carries the URL via a Tk binding closure.
            widget.tag_bind(tag, "<Button-1>",
                            lambda _e, u=url: webbrowser.open(u))
            widget.tag_bind(tag, "<Enter>",
                            lambda _e: widget.configure(cursor="hand2"))
            widget.tag_bind(tag, "<Leave>",
                            lambda _e: widget.configure(cursor=""))
        pos = m.end()
    if pos < len(text):
        widget.insert(tk.END, text[pos:])


# --- tag setup -------------------------------------------------------------


def _configure_tags(widget: tk.Text, palette: dict) -> None:
    bone = palette["bone"]
    bone_dim = palette["bone_dim"]
    accent = palette["accent"]
    code_bg = palette["char_700"]

    base = ("Segoe UI", 10)
    mono = ("Consolas", 10)

    widget.tag_configure("h1", font=("Segoe UI", 18, "bold"),
                         foreground=bone, spacing1=8, spacing3=6)
    widget.tag_configure("h2", font=("Segoe UI", 14, "bold"),
                         foreground=bone, spacing1=6, spacing3=4)
    widget.tag_configure("h3", font=("Segoe UI", 12, "bold"),
                         foreground=bone, spacing1=4, spacing3=2)
    widget.tag_configure("h4", font=("Segoe UI", 11, "bold"),
                         foreground=bone, spacing1=4, spacing3=2)

    widget.tag_configure("bold", font=("Segoe UI", 10, "bold"))
    widget.tag_configure("italic", font=("Segoe UI", 10, "italic"))

    widget.tag_configure("code", font=mono, background=code_bg,
                         foreground=accent)
    widget.tag_configure("codeblock", font=mono, background=code_bg,
                         foreground=bone, lmargin1=14, lmargin2=14,
                         rmargin=14)
    # Pad lines bracket the codeblock so its bg has visible top/bottom inset.
    widget.tag_configure("codeblock_pad", background=code_bg, font=mono)

    widget.tag_configure("hr", foreground=bone_dim, justify="center",
                         spacing1=4, spacing3=4)

    widget.tag_configure("list_marker", foreground=accent, font=base)

    widget.tag_configure("link", foreground=accent, underline=True)

    widget.tag_configure("table_header", font=("Consolas", 10, "bold"),
                         foreground=bone)
    widget.tag_configure("table_sep", font=mono, foreground=bone_dim)
    widget.tag_configure("table_row", font=mono, foreground=bone)
