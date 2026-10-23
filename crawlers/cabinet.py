"""cabinet.gov.mn (Cabinet Secretariat) — every page listed in the WordPress sitemaps: news, cabinet-meeting summaries
(decisions, appointments), government resolutions, leadership. Title, date, type and text. Reachable only from
Mongolian networks."""
import re

from .core import Http, html_text, log, strip_tags

BASE = "https://cabinet.gov.mn"
KEYS = {"pages": ["url"]}
http = Http(rate=1.0, timeout=120, retries=8)


def crawl(ctx):
    st = ctx.state
    if "urls" not in st:
        index = http.get(f"{BASE}/wp-sitemap.xml").text
        maps = [u for u in re.findall(r"<loc>([^<]+)</loc>", index) if "-posts-" in u]
        urls = {}
        for m in maps:
            kind = re.search(r"-posts-([a-z_-]+)-\d+\.xml", m)
            for u in re.findall(r"<loc>([^<]+)</loc>", http.get(m).text):
                urls[u] = kind.group(1) if kind else None
        st["urls"] = urls
        ctx.expect("pages", len(urls))
        ctx.save()
        log(f"{len(urls)} pages in {len(maps)} sitemaps")

    def page(url):
        html = http.get(url).text
        title = re.search(r'property="og:title"\s+content="([^"]*)"', html) or re.search(r"<title>(.*?)</title>", html, re.S)
        date = re.search(r'property="article:published_time"\s+content="([^"]+)"', html) or re.search(r'datetime="([^"]+)"', html)
        text = html_text(html, root_class="entry-content") or html_text(html)
        ctx.write("pages", {"url": url, "type": st["urls"][url], "title": strip_tags(title.group(1)) if title else None,
                            "date": date.group(1) if date else None, "chars": len(text), "text": text})

    urls = sorted(st["urls"])
    return ctx.run_items("pages", urls if not ctx.sample else urls[:3], page, workers=2)
