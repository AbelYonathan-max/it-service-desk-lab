"""Knowledge articles: Markdown files in docs/kb with a short header, published as HTML.

---
title: Reset your password
category: Accounts and passwords
keywords: password, reset, forgotten, self-service
---
The article in Markdown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import markdown

HEADER = re.compile(r"\A---\s*\n(?P<header>.*?)\n---\s*\n(?P<body>.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class Article:
    slug: str
    title: str
    category: str
    keywords: str
    body_markdown: str

    @property
    def html(self) -> str:
        return markdown.markdown(
            self.body_markdown, extensions=["tables", "sane_lists"], output_format="html"
        )


def parse_article(path: Path) -> Article:
    text = path.read_text(encoding="utf-8")
    match = HEADER.match(text)
    if not match:
        raise ValueError(f"{path.name}: needs a header between --- lines (title, category, keywords).")
    fields: dict[str, str] = {}
    for line in match.group("header").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip().lower()] = value.strip()
    missing = [name for name in ("title", "category") if not fields.get(name)]
    if missing:
        raise ValueError(f"{path.name}: header is missing {', '.join(missing)}.")
    return Article(
        slug=path.stem,
        title=fields["title"],
        category=fields["category"],
        keywords=fields.get("keywords", ""),
        body_markdown=match.group("body").strip() + "\n",
    )


def load_articles(directory: Path) -> list[Article]:
    return [
        parse_article(path) for path in sorted(directory.glob("*.md")) if path.name.lower() != "readme.md"
    ]
