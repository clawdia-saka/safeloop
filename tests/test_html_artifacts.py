from html.parser import HTMLParser
from pathlib import Path


ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "docs" / "artifacts"


class ArtifactParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.titles: list[str] = []
        self.h1_count = 0
        self.langs: list[str] = []
        self.canonical_mentions = 0
        self.independent_mentions = 0
        self.li_count = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "html" and attrs.get("lang"):
            self.langs.append(attrs["lang"])
        if tag == "title":
            self._in_title = True
        if tag == "h1":
            self.h1_count += 1
        if tag == "li":
            self.li_count += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        text = data.strip()
        if self._in_title and text:
            self.titles.append(text)
        lower = text.lower()
        if "canonical source" in lower or "正本" in text:
            self.canonical_mentions += 1
        if "independent" in lower or "verification" in lower:
            self.independent_mentions += 1


def parse_artifact(name: str) -> ArtifactParser:
    parser = ArtifactParser()
    parser.feed((ARTIFACT_DIR / name).read_text(encoding="utf-8"))
    return parser


def test_pr_review_explainer_html_has_required_review_sections():
    parser = parse_artifact("pr-review-explainer.html")
    assert parser.langs == ["en"]
    assert parser.h1_count == 1
    assert any("PR Review Explainer" in title for title in parser.titles)
    assert parser.canonical_mentions >= 1
    assert parser.independent_mentions >= 1


def test_dd_research_report_html_has_20_item_checklist_and_canonical_boundary():
    parser = parse_artifact("dd-research-20-items-report.html")
    assert parser.langs == ["ja"]
    assert parser.h1_count == 1
    assert any("20項目" in title for title in parser.titles)
    assert parser.canonical_mentions >= 1
    assert parser.independent_mentions >= 1
    assert parser.li_count == 23  # 20 report items + 3 independent-verification items
