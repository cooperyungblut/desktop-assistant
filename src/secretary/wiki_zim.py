"""Read articles from a Kiwix ZIM file (the offline Wikipedia from kiwix.org).

A ZIM stores each article as the HTML Wikipedia shows, so this turns it back into
plain text: the opening paragraphs, the infobox facts ("Born: January 7, 1964"),
then the body paragraphs, without references, navigation boxes, images or tables.
Needs `libzim` and `lxml` (`uv sync --extra wiki`).
"""

from __future__ import annotations

import multiprocessing
import re
from collections.abc import Iterator
from pathlib import Path

# Only the start of an article matters (we keep a few thousand characters), and
# the infobox and opening sit at the top, so a huge page is cut before parsing.
MAX_HTML_CHARS = 150_000
INFOBOX_ROWS = 14
INFOBOX_VALUE_CHARS = 160
CHUNK = 2000  # entries handed to a worker at a time

# Sections after which there is nothing worth reading.
_END_SECTIONS = frozenset(
    {
        "references",
        "notes",
        "footnotes",
        "citations",
        "external links",
        "see also",
        "further reading",
        "bibliography",
        "sources",
        "works cited",
        "gallery",
    }
)
_JUNK = (
    '//script|//style|//figure|//sup[contains(@class,"reference") or contains(@class,"mw-ref")]'
    '|//*[contains(@class,"navbox")]|//*[contains(@class,"reflist")]'
    '|//*[contains(@class,"mw-references-wrap")]|//*[contains(@class,"hatnote")]'
    '|//*[contains(@class,"thumb")]|//*[contains(@class,"metadata")]'
    '|//*[contains(@class,"noprint")]|//*[contains(@class,"mw-editsection")]'
    '|//*[contains(@class,"sidebar")]|//*[contains(@class,"gallery")]'
    '|//table[not(contains(@class,"infobox"))]'
)
_SPACE = re.compile(r"\s+")
_CITATION = re.compile(r"\[(?:\d+|[a-z]|citation needed|note \d+)\]")


def _clean(text: str) -> str:
    return _SPACE.sub(" ", _CITATION.sub("", text)).strip()


def html_to_text(html: str) -> str:
    """The readable text of one Wikipedia article page, or '' if there is none."""
    import lxml.html

    try:
        doc = lxml.html.document_fromstring(html[:MAX_HTML_CHARS])
    except (ValueError, lxml.etree.ParserError):
        return ""
    body = doc.find("body")
    if body is None:
        return ""

    # Line breaks and list items would otherwise glue words together.
    for element in body.iter("br", "li", "div"):
        element.tail = " " + (element.tail or "")

    facts: list[str] = []
    for box in body.xpath('//table[contains(@class,"infobox")]')[:1]:
        for row in box.xpath(".//tr"):
            label = row.xpath("./th")
            value = row.xpath("./td")
            if len(label) != 1 or len(value) != 1:
                continue
            name, text = _clean(label[0].text_content()), _clean(value[0].text_content())
            if name and text and len(facts) < INFOBOX_ROWS:
                facts.append(f"{name}: {text[:INFOBOX_VALUE_CHARS]}")
    for element in body.xpath(_JUNK + '|//table[contains(@class,"infobox")]'):
        element.drop_tree()

    lead: list[str] = []
    rest: list[str] = []
    in_lead = True
    for element in body.iter("p", "h2"):
        if element.tag == "h2":
            if _clean(element.text_content()).lower() in _END_SECTIONS:
                break
            in_lead = False
            continue
        text = _clean(element.text_content())
        if len(text) > 1:
            (lead if in_lead else rest).append(text)

    parts = lead
    if facts:
        parts = [*parts, "Facts: " + "; ".join(facts) + "."]
    return "\n".join([*parts, *rest])


_archive = None


def _open(path: str) -> None:
    global _archive
    from libzim.reader import Archive

    _archive = Archive(path)


def _read_chunk(bounds: tuple[int, int]) -> list[tuple[str, str]]:
    """Articles for entry ids in [start, stop). Runs inside a worker."""
    assert _archive is not None
    start, stop = bounds
    found: list[tuple[str, str]] = []
    for index in range(start, min(stop, _archive.all_entry_count)):
        entry = _archive.get_entry_by_id(index)
        if entry.is_redirect:
            continue
        item = entry.get_item()
        if not item.mimetype.startswith("text/html"):
            continue
        text = html_to_text(bytes(item.content).decode("utf-8", errors="replace"))
        if text:
            found.append((entry.title, text))
    return found


def iter_zim_articles(path: Path, workers: int = 1) -> Iterator[tuple[str, str]]:
    """Yield (title, text) for every article in a ZIM file, in file order."""
    from libzim.reader import Archive

    if not path.is_file():
        raise FileNotFoundError(f"No ZIM file at {path}")
    total = Archive(str(path)).all_entry_count
    ranges = [(start, start + CHUNK) for start in range(0, total, CHUNK)]
    if workers <= 1:
        _open(str(path))
        for bounds in ranges:
            yield from _read_chunk(bounds)
        return
    context = multiprocessing.get_context("spawn")
    with context.Pool(workers, initializer=_open, initargs=(str(path),)) as pool:
        for chunk in pool.imap(_read_chunk, ranges):
            yield from chunk
