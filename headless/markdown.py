"""Web pages as clean Markdown, ready for an LLM.

``fetch_markdown(url)`` fetches a page (over the browser-impersonating HTTP
transport, or rendered in Playwright when the page needs JavaScript), finds
the main content, drops navigation, headers, footers, cookie banners, ads and
share widgets, and returns Markdown with absolute links — token-counted and,
on request, split into overlapping chunks for retrieval pipelines::

    doc = fetch_markdown("https://docs.python.org/3/library/asyncio.html",
                         chunk_tokens=500)
    doc.title, doc.tokens, doc.markdown
    for chunk in doc.chunks:
        embed(chunk.text, metadata={"url": doc.url, "heading": chunk.heading})

No new dependency: parsing uses BeautifulSoup, which the ``impersonate``,
``http`` and ``playwright`` extras already install. Token counts use
``tiktoken`` when it is installed and a close estimate otherwise.
"""

import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from .logs import get_logger

log = get_logger("markdown")

#: Elements that are never content.
_DROP_TAGS = ("script", "style", "noscript", "template", "svg", "canvas", "iframe",
              "button", "input", "select", "textarea", "dialog", "object", "embed",
              "link", "meta")

#: Class/id fragments that mark boilerplate.
_BOILERPLATE = re.compile(
    r"(^|[\s_-])(nav|navbar|menu|breadcrumbs?|footer|header|masthead|sidebar|"
    r"cookie|consent|gdpr|banner|advert|ads?|sponsor|promo|social|share|sharing|"
    r"related|recommended|newsletter|subscribe|signup|popup|modal|overlay|"
    r"comments?|disqus|skip-link|toc-mobile|pagination)($|[\s_-])", re.I)

_ROLES_TO_DROP = {"navigation", "banner", "contentinfo", "complementary", "search",
                  "dialog", "alert"}

_BLOCK = {"p", "div", "section", "article", "main", "header", "footer", "aside", "nav",
          "form", "address", "ul", "ol", "li", "table",
          "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "figure",
          "figcaption", "dl", "dt", "dd", "details", "summary"}


# ------------------------------------------------------------------ tokens
_encoder = None


def count_tokens(text: str) -> int:
    """Tokens in `text`: exact with ``tiktoken`` (cl100k_base), else estimated.

    The estimate (about four characters, or 0.75 words, per token) is within
    roughly ten percent for English prose, which is what chunk sizing needs.
    """
    global _encoder
    if not text:
        return 0
    if _encoder is None:
        try:
            import tiktoken
            _encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _encoder = False
    if _encoder:
        return len(_encoder.encode(text, disallowed_special=()))
    words = len(text.split())
    return max(1, round(max(len(text) / 4.0, words / 0.75)))


# -------------------------------------------------------------- data model
@dataclass
class Chunk:
    """One retrieval-sized piece of a document."""

    index: int
    text: str
    tokens: int
    #: The heading path the chunk sits under, e.g. "Install > From source".
    heading: str = ""


@dataclass
class MarkdownDocument:
    """A page as Markdown."""

    url: str
    markdown: str
    title: str = ""
    status: int = 200
    final_url: str = ""
    tokens: int = 0
    links: List[Tuple[str, str]] = field(default_factory=list)
    chunks: List[Chunk] = field(default_factory=list)
    #: Which transport fetched it: ``impersonate``, ``http`` or ``playwright``.
    transport: str = ""

    def as_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["links"] = [{"text": t, "url": u} for t, u in self.links]
        return out

    def __str__(self) -> str:
        return self.markdown


# ---------------------------------------------------------- HTML -> Markdown
#: Placeholders that survive whitespace collapsing, restored at the very end:
#: spaces inside inline code, and hard line breaks.
_CODE_SPACE = ""
_HARD_BREAK = ""


def _is_boilerplate(tag) -> bool:
    attrs = getattr(tag, "attrs", None) or {}
    if (attrs.get("role") or "").lower() in _ROLES_TO_DROP:
        return True
    if attrs.get("aria-hidden") == "true" or "hidden" in attrs:
        return True
    style = (attrs.get("style") or "").replace(" ", "").lower()
    if "display:none" in style or "visibility:hidden" in style:
        return True
    marker = " ".join([attrs.get("id") or ""] + list(attrs.get("class") or []))
    return bool(marker.strip()) and bool(_BOILERPLATE.search(marker))


def _text_len(tag) -> int:
    return len(" ".join(tag.get_text(" ", strip=True).split()))


def _link_density(tag) -> float:
    total = _text_len(tag) or 1
    links = sum(_text_len(a) for a in tag.find_all("a"))
    return links / total


def _score(tag) -> float:
    return _text_len(tag) * (1 - _link_density(tag)) + 50 * len(tag.find_all("p"))


_CONTENT_SELECTORS = ("main", "article", "[role=main]", "#content", "#main", ".content",
                      ".post", ".post-content", ".entry-content", ".article-body",
                      ".markdown-body")


def _main_content(soup):
    """The element holding the page's main content.

    Every conventional content container is a candidate, and the one with the
    most prose (and the fewest links) wins — so a post in ``.entry-content`` is
    not lost to a short ``<article>`` comment beside it.
    """
    candidates = []
    for selector in _CONTENT_SELECTORS:
        candidates += [t for t in soup.select(selector) if _text_len(t) > 200]
    if candidates:
        return max(candidates, key=_score)
    body = soup.body or soup
    best, best_score = body, 0.0
    for tag in body.find_all(["div", "section", "td"]):
        if _text_len(tag) < 200:
            continue
        score = _score(tag)
        if score > best_score:
            best, best_score = tag, score
    # Prefer the whole body when the best block is only a sliver of it.
    if _text_len(best) < 0.4 * _text_len(body):
        return body
    return best


def _inside_content(tag) -> bool:
    return tag.find_parent(["article", "main"]) is not None


def _clean(root) -> None:
    total = _text_len(root) or 1

    def holds_the_content(tag) -> bool:
        # Never throw away an element holding most of the page's text, however
        # its classes read ("container has-sidebar", an ASP.NET <form>).
        return _text_len(tag) > 0.5 * total

    for tag in root.find_all(_DROP_TAGS):
        if getattr(tag, "decomposed", False):
            continue
        tag.decompose()
    for tag in root.find_all(["nav", "header", "footer", "aside", "form"]):
        if getattr(tag, "decomposed", False) or holds_the_content(tag):
            continue
        if tag.name == "header" and _inside_content(tag) and tag.find(["h1", "h2", "h3"]):
            continue  # an article's own header: its title and byline
        if tag.name == "form" and _text_len(tag) > 300 and _link_density(tag) < 0.5:
            continue  # a form wrapping real content
        tag.decompose()
    for tag in root.find_all(True):
        if getattr(tag, "decomposed", False):
            continue
        if tag.name in ("html", "body", "main", "article") or holds_the_content(tag):
            continue
        if _is_boilerplate(tag):
            tag.decompose()


def _wrap(marker: str, inner: str) -> str:
    """``**`` around `inner`, keeping its surrounding spaces outside the markers."""
    core = inner.strip()
    if not core:
        return inner if inner.isspace() else ""
    lead = " " if inner[:1].isspace() else ""
    trail = " " if inner[-1:].isspace() else ""
    return f"{lead}{marker}{core}{marker}{trail}"


class _Converter:
    def __init__(self, base_url: str, include_images: bool, include_links: bool):
        self.base = base_url
        self.images = include_images
        self.linked = include_links
        self.links: List[Tuple[str, str]] = []

    def url(self, href: str) -> str:
        return urljoin(self.base, href) if self.base else href

    def inline(self, node) -> str:
        from bs4 import NavigableString, Tag
        if isinstance(node, NavigableString):
            if type(node).__name__ in ("Comment", "CData", "ProcessingInstruction", "Doctype"):
                return ""
            return re.sub(r"\s+", " ", str(node))
        if not isinstance(node, Tag):
            return ""
        name = node.name
        if name == "code":
            text = node.get_text()
            return f"`{text.replace(' ', _CODE_SPACE)}`" if text else ""
        if name == "br":
            return _HARD_BREAK + "\n"
        inner = "".join(self.inline(c) for c in node.children)
        if name in ("strong", "b"):
            return _wrap("**", inner)
        if name in ("em", "i"):
            return _wrap("*", inner)
        if name == "a":
            href = (node.get("href") or "").strip()
            text = inner.strip()
            if not text:
                return inner if inner.isspace() else ""
            if href.startswith("#") and text in ("¶", "#", "§", "🔗", "link"):
                return ""  # a heading's permalink anchor, not content
            lead = " " if inner[:1].isspace() else ""
            trail = " " if inner[-1:].isspace() else ""
            if not href or href.startswith(("javascript:", "#")) or not self.linked:
                return lead + text + trail
            absolute = self.url(href)
            self.links.append((" ".join(text.split()), absolute))
            return f"{lead}[{text}]({absolute}){trail}"
        if name == "img":
            if not self.images:
                return ""
            src = node.get("src") or node.get("data-src") or ""
            return f"![{node.get('alt', '').strip()}]({self.url(src)})" if src else ""
        if name in _BLOCK:
            return " " + self.block(node).strip() + " "
        return inner

    def block(self, node, depth: int = 0) -> str:
        from bs4 import NavigableString, Tag
        out: List[str] = []
        buffer: List[str] = []

        def flush():
            text = "".join(buffer)
            buffer.clear()
            text = re.sub(r"[ \t]+", " ", text)
            text = "\n".join(line.strip() for line in text.split("\n")).strip()
            if text:
                out.append(text)

        for child in node.children:
            if isinstance(child, NavigableString) or not isinstance(child, Tag) \
                    or child.name not in _BLOCK:
                buffer.append(self.inline(child))
                continue
            flush()
            name = child.name
            if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                text = " ".join(self.inline(child).replace(_HARD_BREAK, " ").split())
                if text:
                    out.append("#" * int(name[1]) + " " + text)
            elif name == "hr":
                out.append("---")
            elif name == "pre":
                code = child.get_text().rstrip("\n")
                lang = ""
                inner = child.find("code")
                for cls in (inner.get("class") if inner else None) or []:
                    if cls.startswith("language-"):
                        lang = cls[len("language-"):]
                fence = "````" if "```" in code else "```"
                out.append(f"{fence}{lang}\n{code}\n{fence}")
            elif name in ("ul", "ol"):
                listing = self.listing(child)
                if listing:
                    out.append(listing)
            elif name == "blockquote":
                inner = self.block(child, depth)
                out.append("\n".join("> " + line if line else ">" for line in inner.split("\n")))
            elif name == "table":
                table = self.table(child)
                if table:
                    out.append(table)
            elif name == "dt":
                text = " ".join(self.inline(child).split())
                if text:
                    out.append(f"**{text}**")
            else:
                inner = self.block(child, depth)
                if inner:
                    out.append(inner)
        flush()
        return "\n\n".join(part for part in out if part.strip())

    def listing(self, node, depth: int = 0) -> str:
        """A list, with each item's continuation lines (nested lists, code,
        further paragraphs) indented under its marker."""
        lines = []
        ordered = node.name == "ol"
        try:
            number = int(node.get("start", 1))
        except (TypeError, ValueError):
            number = 1
        for item in node.find_all("li", recursive=False):
            body = self.block(item).strip("\n")
            marker = f"{number}." if ordered else "-"
            number += 1
            if not body.strip():
                continue
            pad = " " * (len(marker) + 1)
            first, *rest = body.split("\n")
            lines.append(f"{marker} {first}")
            lines += [pad + line if line else "" for line in rest]
        return "\n".join(lines)

    def table(self, node) -> str:
        rows = []
        # This table's own rows and cells only: a nested table is content of
        # its cell, not extra rows of this one.
        for tr in node.find_all("tr"):
            if tr.find_parent("table") is not node:
                continue
            cells = [" ".join(self.inline(c).replace(_HARD_BREAK, " ").split()).replace("|", "\\|")
                     for c in tr.find_all(["th", "td"], recursive=False)]
            if any(cells):
                rows.append(cells)
        if not rows:
            return ""
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        lines = ["| " + " | ".join(rows[0]) + " |",
                 "| " + " | ".join("---" for _ in range(width)) + " |"]
        lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return "\n".join(lines)


def _strip_invisible(text: str) -> str:
    """NFC, minus control and zero-width characters. Entities are already
    decoded by the parser and whitespace is significant, so neither is touched."""
    text = unicodedata.normalize("NFC", text)
    return "".join(ch for ch in text if ch in "\t\n" or ch in (_CODE_SPACE, _HARD_BREAK)
                   or not unicodedata.category(ch).startswith("C"))


def html_to_markdown(html: str, base_url: str = "", main_content: bool = True,
                     include_links: bool = True, include_images: bool = False
                     ) -> Tuple[str, str, List[Tuple[str, str]]]:
    """Convert HTML to Markdown. Returns ``(markdown, title, links)``.

    With `main_content`, boilerplate is removed and only the page's main
    content is converted; without it, the whole ``<body>``.
    """
    from bs4 import BeautifulSoup
    from .transport import html_parser

    soup = BeautifulSoup(html or "", html_parser())
    title = ""
    if soup.title and soup.title.string:
        title = " ".join(soup.title.string.split())
    og = soup.find("meta", attrs={"property": "og:title"})
    if not title and og and og.get("content"):
        title = og["content"].strip()
    base = soup.find("base", href=True)
    if base:
        base_url = urljoin(base_url, base["href"])

    for tag in soup.find_all(["script", "style", "noscript", "template"]):
        tag.decompose()
    root = _main_content(soup) if main_content else (soup.body or soup)
    if main_content:
        _clean(root)
    converter = _Converter(base_url, include_images, include_links)
    try:
        markdown = converter.block(root)
    except RecursionError:
        # Pathologically deep markup: keep the text rather than fail.
        markdown = "\n\n".join(line.strip() for line in root.get_text("\n").split("\n")
                               if line.strip())
    markdown = _strip_invisible(markdown)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
    markdown = markdown.replace(_HARD_BREAK + "\n", "  \n").replace(_HARD_BREAK, "  \n")
    markdown = markdown.replace(_CODE_SPACE, " ")
    if title and not markdown.lstrip().startswith("# "):
        markdown = f"# {title}\n\n{markdown}" if markdown else f"# {title}"
    seen, links = set(), []
    for text, url in converter.links:
        if url not in seen:
            seen.add(url)
            links.append((text, url))
    return markdown, title, links


# ------------------------------------------------------------------ chunks
_FENCE = re.compile(r"^\s*(```+|~~~+)")


def _blocks(markdown: str) -> List[str]:
    """Paragraph-level blocks, keeping a fenced code block whole even when it
    contains blank lines."""
    blocks: List[str] = []
    pending: List[str] = []
    fence: Optional[str] = None
    for part in re.split(r"\n{2,}", markdown):
        if fence is not None:
            pending.append(part)
            if any(_FENCE.match(line) and _FENCE.match(line).group(1).startswith(fence)
                   for line in part.split("\n")):
                blocks.append("\n\n".join(pending))
                pending, fence = [], None
            continue
        opened = None
        for line in part.split("\n"):
            m = _FENCE.match(line)
            if m:
                opened = None if opened else m.group(1)
        if opened:
            fence, pending = opened, [part]
        elif part.strip():
            blocks.append(part)
    if pending:
        blocks.append("\n\n".join(pending))
    return [b for b in blocks if b.strip()]


def _is_heading(block: str):
    if _FENCE.match(block):
        return None
    return re.match(r"^(#{1,6})\s+(.*)", block)


def chunk_markdown(markdown: str, max_tokens: int = 500, overlap: int = 50) -> List[Chunk]:
    """Split Markdown into chunks of at most `max_tokens`, on heading and
    paragraph boundaries, each carrying the heading path it sits under.

    `overlap` tokens of the previous chunk's tail are repeated at the start of
    the next, so a fact split across a boundary is still retrievable. Code
    blocks and lists that must be split keep their lines (and fences).
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    overlap = max(0, min(overlap, max_tokens // 2))
    chunks: List[Chunk] = []
    path: List[str] = []
    current: List[str] = []
    current_heading = ""

    def add(text: str) -> None:
        chunks.append(Chunk(len(chunks), text, count_tokens(text), current_heading))

    def emit(keep_tail: bool = True) -> None:
        nonlocal current
        if not current:
            return
        text = "\n\n".join(current)
        add(text)
        tail = _tail(text, overlap) if (overlap and keep_tail) else ""
        current = [tail] if tail else []

    for block in _blocks(markdown):
        heading = _is_heading(block)
        if heading:
            emit(keep_tail=False)
            current = []
            level = len(heading.group(1))
            path = path[: level - 1] + [heading.group(2).strip()]
            current_heading = " > ".join(path)
        if count_tokens(block) > max_tokens:
            # A heading waiting for its content goes with the first piece.
            prefix = "\n\n".join(c for c in current if _is_heading(c))
            emit_only_body = [c for c in current if not _is_heading(c)]
            if emit_only_body:
                current = emit_only_body
                emit(keep_tail=False)
            current = []
            budget = max_tokens - (count_tokens(prefix) + 2 if prefix else 0)
            pieces = _split_block(block, max(1, budget)) if budget > 0 else _split_block(block, max_tokens)
            for i, piece in enumerate(pieces):
                if i == 0 and prefix and budget > 0:
                    piece = prefix + "\n\n" + piece
                elif i == 0 and prefix:
                    add(prefix)
                add(piece)
            continue
        candidate = "\n\n".join(current + [block])
        if current and count_tokens(candidate) > max_tokens:
            emit()
            if current and count_tokens("\n\n".join(current + [block])) > max_tokens:
                current = []   # the overlap would push this block over the limit
        current.append(block)
    if current and any(c.strip() for c in current):
        text = "\n\n".join(current)
        if not chunks or text != _tail(chunks[-1].text, overlap):
            add(text)
    return chunks


def _tail(text: str, tokens: int) -> str:
    if tokens <= 0 or _FENCE.search(text.split("\n")[-1] if text else ""):
        return ""
    words = text.split()
    keep = max(1, int(tokens * 0.75))
    tail = " ".join(words[-keep:]) if len(words) > keep else ""
    while tail and count_tokens(tail) > tokens:
        tail = tail.split(" ", 1)[1] if " " in tail else ""
    return tail


def _pack(units: List[str], joiner: str, max_tokens: int) -> List[str]:
    """Greedily join `units` into pieces of at most `max_tokens`."""
    pieces: List[str] = []
    current: List[str] = []
    for unit in units:
        candidate = joiner.join(current + [unit])
        if current and count_tokens(candidate) > max_tokens:
            pieces.append(joiner.join(current))
            current = []
        if count_tokens(unit) > max_tokens:
            if current:
                pieces.append(joiner.join(current))
                current = []
            pieces.extend(_split_text(unit, max_tokens))
            continue
        current.append(unit)
    if current:
        pieces.append(joiner.join(current))
    return [p for p in pieces if p.strip()]


def _split_text(text: str, max_tokens: int) -> List[str]:
    """Sentences, then words, then characters — whatever it takes to fit."""
    sentences = [s for s in re.split(r"(?<=[.!?。！？])\s*", text) if s]
    if len(sentences) > 1:
        return _pack(sentences, " " if " " in text else "", max_tokens)
    words = text.split(" ")
    if len(words) > 1:
        return _pack(words, " ", max_tokens)
    # One unbroken run (CJK, a long token): cut by characters.
    pieces, start = [], 0
    while start < len(text):
        lo, hi = start + 1, len(text)
        while lo < hi:  # the longest slice from `start` that fits
            mid = (lo + hi + 1) // 2
            if count_tokens(text[start:mid]) <= max_tokens:
                lo = mid
            else:
                hi = mid - 1
        pieces.append(text[start:lo])
        start = lo
    return pieces


def _split_block(block: str, max_tokens: int) -> List[str]:
    """Split one oversized block, keeping its structure."""
    fence = _FENCE.match(block)
    if fence:
        lines = block.split("\n")
        opener = lines[0]
        closer = lines[-1] if len(lines) > 1 and _FENCE.match(lines[-1]) else fence.group(1)
        body = lines[1:-1] if len(lines) > 1 and _FENCE.match(lines[-1]) else lines[1:]
        overhead = count_tokens(f"{opener}\n\n{closer}")
        inner = _pack(body, "\n", max(1, max_tokens - overhead))
        return [f"{opener}\n{piece}\n{closer}" for piece in inner]
    if "\n" in block:  # lists, tables, quotes: split between lines
        return _pack(block.split("\n"), "\n", max_tokens)
    return _split_text(block, max_tokens)


def _split_long(block: str, max_tokens: int) -> List[str]:
    """Backwards-compatible name for :func:`_split_block`."""
    return _split_block(block, max(1, int(max_tokens)))


# ------------------------------------------------------------------- fetch
def _fetch_html(url: str, render: str, timeout: float, proxy: Optional[str]):
    """``(html, final_url, status, transport)`` via the cheapest adequate path."""
    from .transport import (ImpersonateTransport, HttpTransport, impersonate_available,
                            http_available)
    if render == "always":
        return _render(url, timeout, proxy)
    if impersonate_available()[0]:
        transport = ImpersonateTransport(timeout=timeout, proxy=proxy)
    elif http_available()[0]:
        transport = HttpTransport(timeout=timeout, proxy=proxy)
    elif render == "auto":
        return _render(url, timeout, proxy)
    else:
        raise RuntimeError('fetch_markdown needs an HTTP extra: pip install "headless-driver[impersonate]"')
    try:
        page = transport.fetch(url, key="markdown")
    finally:
        transport.close()
    html = str(page.root._tag) if hasattr(page.root, "_tag") else ""
    return html, page.url, page.status, transport.name


def _render(url: str, timeout: float, proxy: Optional[str]):
    from .playwright_driver import PlaywrightBrowser
    with PlaywrightBrowser(timeout=timeout, proxy=proxy) as browser:
        page, response = browser._open(url, "markdown", wait_until="load")
        try:
            try:
                # Give client-side rendering a moment to settle, but never hang
                # on pages that poll forever.
                page.wait_for_load_state("networkidle", timeout=min(5000, int(timeout * 1000)))
            except Exception:
                pass
            status = response.status if response else 200
            html = page.content()
            final = page.url or url
        finally:
            page.close()
    return html, final, status, "playwright"


def fetch_markdown(url: str, *, render: str = "auto", chunk_tokens: Optional[int] = None,
                   overlap: int = 50, main_content: bool = True, include_links: bool = True,
                   include_images: bool = False, max_tokens: Optional[int] = None,
                   timeout: float = 15.0, proxy: Optional[str] = None) -> MarkdownDocument:
    """Fetch `url` and return it as clean, LLM-ready Markdown.

    `render` is ``"auto"`` (HTTP first; Playwright if the page came back with
    almost no text, as JavaScript-rendered pages do), ``"never"`` or
    ``"always"``. `chunk_tokens` splits the result into
    :class:`Chunk` objects of about that size with `overlap` tokens repeated.
    `max_tokens` truncates the Markdown, at a paragraph boundary, to fit a
    context window.
    """
    if render not in ("auto", "never", "always"):
        raise ValueError("render must be 'auto', 'never' or 'always'")
    if max_tokens is not None and max_tokens < 1:
        raise ValueError("max_tokens must be at least 1")
    if chunk_tokens is not None and chunk_tokens < 1:
        raise ValueError("chunk_tokens must be at least 1")
    html, final_url, status, transport = _fetch_html(url, render, timeout, proxy)
    markdown, title, links = html_to_markdown(html, final_url or url, main_content,
                                              include_links, include_images)
    if render == "auto" and transport != "playwright" and status < 400 \
            and looks_client_rendered(html, markdown) and _playwright_ready():
        # Almost no text over HTTP: very likely rendered in the browser.
        log.debug("%s has little server-rendered text; rendering it", url)
        try:
            html, final_url, status, transport = _render(url, timeout, proxy)
            markdown, title, links = html_to_markdown(html, final_url or url, main_content,
                                                      include_links, include_images)
        except Exception as e:
            log.debug("rendering %s failed (%s); keeping the HTTP version", url, e)
    if max_tokens and count_tokens(markdown) > max_tokens:
        markdown = truncate_tokens(markdown, max_tokens)
    doc = MarkdownDocument(url=url, markdown=markdown, title=title, status=status,
                           final_url=final_url or url, tokens=count_tokens(markdown),
                           links=links, transport=transport)
    if chunk_tokens:
        doc.chunks = chunk_markdown(markdown, chunk_tokens, overlap)
    return doc


_APP_SHELL = re.compile(
    r'<div[^>]+id=["\'](root|app|__next|__nuxt|svelte)["\'][^>]*>\s*</div>'
    r'|ng-app|data-reactroot|enable javascript|requires javascript|javascript is required',
    re.I)


def looks_client_rendered(html: str, markdown: str) -> bool:
    """Whether a page's content probably arrives by JavaScript.

    Short *and* either an empty app shell, a "please enable JavaScript" notice,
    or mostly scripts — a short static page (example.com) is left alone.
    """
    if len(markdown) >= 400:
        return False
    if _APP_SHELL.search(html or ""):
        return True
    return (html or "").lower().count("<script") >= 3


def truncate_tokens(markdown: str, max_tokens: int) -> str:
    """Keep whole blocks up to `max_tokens`; never leaves a code fence open."""
    if max_tokens <= 0:
        return ""
    kept: List[str] = []
    for block in _blocks(markdown):
        if count_tokens("\n\n".join(kept + [block])) > max_tokens:
            if not kept:
                kept = [_split_block(block, max_tokens)[0]]
            break
        kept.append(block)
    return "\n\n".join(kept)


def _playwright_ready() -> bool:
    try:
        from .playwright_driver import playwright_available
        return playwright_available()[0]
    except Exception:
        return False
