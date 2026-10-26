"""A small HTML parser for tests: which tags, links and sources does a page use?"""

from html.parser import HTMLParser


class PageInspector(HTMLParser):
    """Collects the tags, attributes and links of a page."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: set[str] = set()
        self.attribute_names: set[str] = set()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        for name, value in attrs:
            self.attribute_names.add(name)
            if name == "href":
                self.hrefs.append(value)


def inspect_page(page: str) -> PageInspector:
    inspector = PageInspector()
    inspector.feed(page)
    return inspector
