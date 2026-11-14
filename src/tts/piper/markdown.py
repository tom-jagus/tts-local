"""Shared Markdown preparation for files and Pi messages."""
import re
import textwrap

from markdown_it import MarkdownIt

SECTION_LIMIT = 1000
MAX_DOCUMENT_LENGTH = 2_000_000
parser = MarkdownIt("commonmark", {"html": False}).enable("table")


def inline_text(tokens) -> str:
    parts = []
    for token in tokens or []:
        if token.type in ("text", "code_inline"):
            parts.append(token.content)
        elif token.type in ("softbreak", "hardbreak"):
            parts.append(" ")
        elif token.type == "image":
            parts.append(inline_text(token.children) or token.content)
    return "".join(parts)


def text_sections(text: str) -> list[str]:
    """Split plain text at whitespace, omitting whitespace-only sections."""
    sections = []
    while len(text) > SECTION_LIMIT:
        boundary = next(
            (index + 1 for index in range(SECTION_LIMIT - 1, -1, -1)
             if text[index].isspace()),
            SECTION_LIMIT,
        )
        section = text[:boundary]
        if section.strip():
            sections.append(section)
        text = text[boundary:]
    if text.strip():
        sections.append(text)
    return sections


def markdown_sections(source: str, split_headings: bool = True) -> list[str]:
    """Keep heading sections together, splitting oversized sections at words."""
    sections = []
    blocks = []

    def flush():
        text = re.sub(r"\n{3,}", "\n\n", "".join(blocks)).strip()
        blocks.clear()
        if text:
            # Prefer paragraph boundaries, then sentence and word boundaries.
            current = ""
            for paragraph in text.split("\n\n"):
                pieces = re.split(r"(?<=[.!?])\s+", paragraph)
                for piece in pieces:
                    for chunk in textwrap.wrap(piece, SECTION_LIMIT,
                                               break_long_words=True,
                                               break_on_hyphens=False):
                        if current and len(current) + len(chunk) + 2 > SECTION_LIMIT:
                            sections.append(current)
                            current = ""
                        current += ("\n\n" if current else "") + chunk
            if current:
                sections.append(current)

    for token in parser.parse(source):
        if token.type == "heading_open":
            if split_headings:
                flush()
        elif token.type == "inline":
            blocks.append(inline_text(token.children))
        elif token.type in ("fence", "code_block"):
            language = token.info.strip().split()
            label = f"{language[0]} code block." if language else "Code block."
            blocks.append(f"\n\n{label}\n\n")
        elif token.type in ("paragraph_close", "heading_close", "list_item_close",
                             "blockquote_close", "tr_close", "hr"):
            blocks.append("\n\n")
        elif token.type in ("td_close", "th_close"):
            blocks.append(". ")
    flush()
    return sections
