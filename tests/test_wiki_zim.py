from __future__ import annotations

from pathlib import Path

import pytest

from secretary.wiki import WikiIndex, build_index, iter_articles
from secretary.wiki_zim import html_to_text, iter_zim_articles

CAGE_HTML = """<!DOCTYPE html><html><head><title>Nicolas Cage</title>
<style>.x{color:red}</style></head><body>
<section data-mw-section-id="0">
<div class="hatnote">"Cage" redirects here. For other uses, see Cage (disambiguation).</div>
<table class="infobox vcard"><tbody>
<tr><th colspan="2">Nicolas Cage</th></tr>
<tr><th scope="row">Born</th><td>Nicolas Kim Coppola<br>January 7, 1964 (age 59)<br>
Long Beach, California</td></tr>
<tr><th scope="row">Occupation</th><td>Actor</td></tr>
<tr><td colspan="2">No label here</td></tr>
</tbody></table>
<p><b>Nicolas Kim Coppola</b> (born January 7, 1964), known professionally as
<b>Nicolas Cage</b>, is an American actor.<sup class="mw-ref reference">[1]</sup></p>
<p>He has appeared in dozens of films since the early 1980s.</p>
</section>
<section data-mw-section-id="1"><h2>Early life</h2>
<p>Cage was born in Long Beach, California, and raised in Los Angeles.[2]</p>
<table class="wikitable"><tr><td><p>Table text that should not appear.</p></td></tr></table>
<figure><figcaption><p>A caption.</p></figcaption></figure>
</section>
<section><h2>References</h2><p>Citation text that should not appear.</p></section>
<div class="navbox"><p>Navigation text.</p></div>
</body></html>"""


def test_html_to_text_orders_lead_facts_then_body():
    text = html_to_text(CAGE_HTML)
    lines = text.split("\n")
    assert lines[0].startswith("Nicolas Kim Coppola (born January 7, 1964), known professionally")
    assert lines[1] == "He has appeared in dozens of films since the early 1980s."
    assert lines[2].startswith("Facts: Born: Nicolas Kim Coppola January 7, 1964 (age 59)")
    assert "Occupation: Actor" in lines[2]
    assert lines[3].startswith("Cage was born in Long Beach")


def test_html_to_text_leaves_out_the_junk():
    text = html_to_text(CAGE_HTML)
    for junk in ("redirects here", "Table text", "A caption", "Citation text", "Navigation", "[1]"):
        assert junk not in text
    assert "[2]" not in text and "No label here" not in text


def test_html_to_text_handles_garbage():
    assert html_to_text("") == ""
    assert html_to_text("<html><body></body></html>") == ""


@pytest.fixture
def zim_file(tmp_path: Path) -> Path:
    from libzim.writer import Creator, Hint, Item, StringProvider

    class Page(Item):
        def __init__(self, path: str, title: str, content: str, mime: str = "text/html"):
            super().__init__()
            self._path, self._title, self._content, self._mime = path, title, content, mime

        def get_path(self):
            return self._path

        def get_title(self):
            return self._title

        def get_mimetype(self):
            return self._mime

        def get_contentprovider(self):
            return StringProvider(self._content)

        def get_hints(self):
            return {Hint.FRONT_ARTICLE: self._mime == "text/html"}

    filler = "<p>Paris is the capital and most populous city of France. " * 1 + "</p>"
    path = tmp_path / "wiki.zim"
    with Creator(path).config_indexing(False, "eng").set_mainpath("Nicolas_Cage") as creator:
        creator.add_item(Page("Nicolas_Cage", "Nicolas Cage", CAGE_HTML))
        creator.add_item(
            Page(
                "Paris",
                "Paris",
                f"<html><body><section>{filler * 3}</section></body></html>",
            )
        )
        creator.add_item(Page("style.css", "style", "p {}", "text/css"))
        creator.add_redirection("Nicholas_Cage", "Nicholas Cage", "Nicolas_Cage", {})
        for key, value in {
            "Title": "Test",
            "Language": "eng",
            "Creator": "t",
            "Publisher": "t",
            "Date": "2026-01-01",
            "Description": "test",
            "Name": "test",
        }.items():
            creator.add_metadata(key, value)
    return path


def test_zim_articles_skip_redirects_and_non_html(zim_file: Path):
    titles = sorted(title for title, _ in iter_zim_articles(zim_file))
    assert titles == ["Nicolas Cage", "Paris"]


def test_zim_with_worker_processes_gives_the_same_articles(zim_file: Path):
    single = sorted(iter_zim_articles(zim_file, workers=1))
    parallel = sorted(iter_zim_articles(zim_file, workers=2))
    assert parallel == single


def test_iter_articles_picks_the_zim_reader_by_suffix(zim_file: Path):
    assert {title for title, _ in iter_articles(zim_file)} == {"Nicolas Cage", "Paris"}


def test_missing_zim_is_an_error(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        next(iter_zim_articles(tmp_path / "nope.zim"))


def test_birthday_question_end_to_end_from_a_zim(zim_file: Path, tmp_path: Path):
    index_file = tmp_path / "index.sqlite"
    build_index(iter_articles(zim_file), index_file)
    with WikiIndex(index_file) as index:
        top = index.search("What is Nicholas Cage's birthday?")[0]
    assert top.title == "Nicolas Cage"
    assert "January 7, 1964" in top.text
