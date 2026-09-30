"""Generate the end-to-end test site from data.json.

Each engine's page reproduces that engine's real markup — the same containers,
classes, ads and click-tracking redirects the live engine serves — filled with
the ground-truth results, so scraping it exercises the real selectors, ad
filters and redirect unwrapping. Run after editing data.json:

    python tests/e2e/build_site.py
"""

import json
import base64
import pathlib
from urllib.parse import quote

HERE = pathlib.Path(__file__).parent
SITE = HERE / "site"
DATA = json.loads((HERE / "data.json").read_text(encoding="utf-8"))
RESULTS = DATA["results"]
AD = DATA["ad"]


def href(r):
    return r.get("href") or r["url"]


def page(title, body, head=""):
    return (f"<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            f"<title>{title}</title>\n{head}</head>\n<body>\n{body}\n</body>\n</html>\n")


def brave():
    items = []
    items.append(f'<div data-type="ad"><a href="https://search.brave.com/a/redirect?x=1">'
                 f'<div class="title">{AD["title"]}</div></a></div>')
    for r in RESULTS:
        items.append(
            f'<div class="snippet" data-type="web">\n'
            f'  <a href="{href(r)}"><div class="site-name-content">{r["url"].split("/")[2]}</div>'
            f'<div class="title search-snippet-title">{r["title_html"]}</div></a>\n'
            f'  <div class="generic-snippet"><div class="content">{r["snippet_html"]}</div></div>\n'
            f'</div>')
    body = ('<main id="main"><section id="mixed-main">\n' + "\n".join(items) +
            '\n</section><div id="search-elsewhere">Search elsewhere</div></main>')
    return page("{query} - Brave Search", body)


def brave_empty():
    return page("{query} - Brave Search", '<main id="main"><p>Not many great matches came '
                'back for your search.</p><div id="search-elsewhere">Search elsewhere</div></main>')


def duckduckgo():
    items = [f'<div class="result results_links result--ad"><a class="result__a" '
             f'href="https://duckduckgo.com/y.js?ad_domain=ads.example">{AD["title"]}</a>'
             f'<a class="result__snippet">{AD["snippet"]}</a></div>']
    for r in RESULTS:
        items.append(
            f'<div class="result results_links results_links_deep web-result">\n'
            f'  <h2 class="result__title"><a class="result__a" href="{href(r)}">{r["title_html"]}</a></h2>\n'
            f'  <a class="result__snippet" href="{href(r)}">{r["snippet_html"]}</a>\n'
            f'</div>')
    return page("{query} at DuckDuckGo", '<div id="links" class="results">\n' +
                "\n".join(items) + "\n</div>")


def duckduckgo_empty():
    return page("{query} at DuckDuckGo", '<div id="links" class="results"><div class="result '
                'result--no-result"><div class="no-results">No results.</div></div></div>')


def duckduckgo_lite():
    rows = []
    for i, r in enumerate(RESULTS, 1):
        wrapped = "https://duckduckgo.com/l/?uddg=" + quote(href(r), safe="")
        rows.append(f'<tr><td>{i}.&nbsp;</td><td><a rel="nofollow" href="{wrapped}" '
                    f'class="result-link">{r["title_html"]}</a></td></tr>\n'
                    f'<tr><td>&nbsp;</td><td class="result-snippet"></td></tr>')
    return page("{query} at DuckDuckGo", "<table>\n" + "\n".join(rows) + "\n</table>")


def yahoo():
    items = [f'<li><div class="dd algo relsrch"><div class="compTitle"><h3><a href="'
             f'https://www.bing.com/aclick?ld=ad">{AD["title"]}</a></h3></div></div></li>']
    for i, r in enumerate(RESULTS, 1):
        wrapped = ("https://r.search.yahoo.com/_ylt=Awr;_ylu=Y29s/RV=2/RE=1791872156/RO=10/"
                   f"RU={quote(href(r), safe='')}/RK=2/RS=e2e{i}-")
        items.append(
            f'<li><div class="dd algo algo-sr relsrch">\n'
            f'  <div class="compTitle options-toggle"><h3 class="title"><a href="{wrapped}">'
            f'{r["title_html"]}</a></h3></div>\n'
            f'  <div class="compText aAbs"><p class="fz-ms">{r["snippet_html"]}</p></div>\n'
            f'</div></li>')
    return page("{query} - Yahoo Search Results", '<div id="web"><ol class="searchCenterMiddle">\n'
                + "\n".join(items) + "\n</ol></div>")


def yahoo_empty():
    return page("{query} - Yahoo Search Results", '<div id="web"><ol><li><div class="dd zrp">'
                '<p>We did not find results for: {query}</p></div></li></ol></div>')


def mojeek():
    items = []
    for r in RESULTS:
        items.append(f'<li><a class="ob" href="{href(r)}">{r["url"]}</a>\n'
                     f'  <h2><a class="title" href="{href(r)}">{r["title_html"]}</a></h2>\n'
                     f'  <p class="s">{r["snippet_html"]}</p></li>')
    return page("{query} - Mojeek Search", '<ul class="results-standard">\n' +
                "\n".join(items) + "\n</ul>")


def mojeek_empty():
    return page("{query} - Mojeek Search", '<div class="results"><p>No pages found matching:'
                ' {query}</p></div>')


def google_basic():
    items = []
    for r in RESULTS:
        wrapped = "/url?q=" + quote(href(r), safe="") + "&sa=U&ved=2ahUKE"
        items.append(f'<div data-hveid="CA{len(items)}QAA"><div><a href="{wrapped}"><h3>'
                     f'{r["title_html"]}</h3></a></div><div data-sncf="1">{r["snippet_html"]}'
                     f'</div></div>')
    return page("{query} - Google Search", '<div id="main">\n' + "\n".join(items) + "\n</div>")


def google_enablejs():
    return page("Google Search", '<noscript><meta content="0;url=/httpservice/retry/enablejs?sei=e2e"'
                ' http-equiv="refresh"><div>Please click <a href="/httpservice/retry/enablejs?sei=e2e">'
                'here</a> if you are not redirected within a few seconds.</div></noscript>')


def bing():
    items = [f'<li class="b_ad"><h2><a href="https://www.bing.com/aclick?ld=e2e">{AD["title"]}'
             f'</a></h2></li>']
    for r in RESULTS:
        token = "a1" + base64.urlsafe_b64encode(href(r).encode()).decode().rstrip("=")
        items.append(f'<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?!&amp;&amp;p=e2e'
                     f'&amp;u={token}&amp;ntb=1">{r["title_html"]}</a></h2>'
                     f'<div class="b_caption"><p>{r["snippet_html"]}</p></div></li>')
    return page("{query} - Search", '<ol id="b_results">\n' + "\n".join(items) + "\n</ol>")


def startpage_home():
    return page("Startpage", f'<form id="search" action="/sp/search" method="post">'
                f'<input name="query"><input type="hidden" name="sc" value="{DATA["startpage_token"]}">'
                f'</form>')


def startpage():
    items = []
    for r in RESULTS:
        items.append(f'<div class="w-gl__result"><a class="w-gl__result-title result-link" '
                     f'href="{href(r)}"><h3>{r["title_html"]}</h3></a>'
                     f'<p class="w-gl__description">{r["snippet_html"]}</p></div>')
    return page("Startpage Search Results", '<section class="w-gl">\n' + "\n".join(items) +
                "\n</section>")


def captcha():
    return page("Captcha", '<p>JavaScript is required to complete this challenge.</p>')


def new_layout():
    return page("{query} - Search", "<main><article>Entirely new markup the scraper has "
                "never seen.</article></main>")


def article():
    rows = "\n".join(f'<li><a href="{href(r)}">{r["title_html"]}</a> &mdash; {r["snippet_html"]}</li>'
                     for r in RESULTS)
    return page("Credo Capital people directory",
                '<header class="site-header"><nav><a href="/">Home</a> <a href="/about">About</a>'
                '</nav></header>\n<div class="cookie-banner">We use cookies. <button>OK</button></div>\n'
                '<main><article><h1>Credo Capital people directory</h1>\n'
                '<p>Everyone below appears in the <strong>ground truth</strong> used by the '
                'end-to-end suite. See <a href="/brave.html">the Brave page</a>.</p>\n'
                f'<h2>People</h2>\n<ul>\n{rows}\n</ul>\n'
                '<h2>Code</h2>\n<pre><code class="language-python">from headless import fetch_markdown\n'
                'doc = fetch_markdown(url)</code></pre>\n'
                '<table><tr><th>Engine</th><th>Index</th></tr><tr><td>yahoo</td><td>bing</td></tr></table>\n'
                '</article></main>\n<aside class="sidebar">Related: nothing</aside>\n'
                '<footer>&copy; 2026 e2e</footer>')


def app():
    # Content that only exists once JavaScript has run.
    return page("App shell", '<div id="root"></div>\n<script>\n'
                'document.getElementById("root").innerHTML = "<main><h1>Rendered by JavaScript</h1>'
                '<p>This paragraph was inserted by a script, so only a browser can read it. "'
                ' + "It is long enough to count as real content for the extractor. ".repeat(8)'
                ' + "</p></main>";\n</script>')


PAGES = {
    "brave.html": brave, "brave_empty.html": brave_empty,
    "duckduckgo.html": duckduckgo, "duckduckgo_empty.html": duckduckgo_empty,
    "duckduckgo_lite.html": duckduckgo_lite,
    "yahoo.html": yahoo, "yahoo_empty.html": yahoo_empty,
    "mojeek.html": mojeek, "mojeek_empty.html": mojeek_empty,
    "google_basic.html": google_basic, "google_enablejs.html": google_enablejs,
    "bing.html": bing,
    "startpage_home.html": startpage_home, "startpage.html": startpage,
    "captcha.html": captcha, "new_layout.html": new_layout,
    "article.html": article, "app.html": app,
}


def build() -> None:
    SITE.mkdir(exist_ok=True)
    for name, render in PAGES.items():
        (SITE / name).write_text(render(), encoding="utf-8")
    # A human-readable index of everything the site serves.
    rows = "\n".join(f'<tr><td><a href="{href(r)}">{r["title_html"]}</a></td>'
                     f'<td>{r["snippet_html"]}</td></tr>' for r in RESULTS)
    links = "\n".join(f'<li><a href="{n}">{n}</a></li>' for n in PAGES)
    (SITE / "index.html").write_text(page(
        "headless-driver end-to-end test site",
        f"<h1>headless-driver end-to-end test site</h1>\n<p>Every engine page below is "
        f"generated from <code>data.json</code>.</p>\n<table>\n{rows}\n</table>\n"
        f"<ul>\n{links}\n</ul>"), encoding="utf-8")


if __name__ == "__main__":
    build()
    print(f"wrote {len(PAGES) + 1} pages to {SITE}")
