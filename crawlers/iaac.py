"""iaac.mn — Independent Authority Against Corruption news items (/<id>/nitem): title, date, text."""
import re

from .core import Http, NotFound, html_text, log, strip_tags

BASE = "https://iaac.mn"
KEYS = {"news": ["id"]}
http = Http(rate=1.5, timeout=90)


def crawl(ctx):
    if "max_id" not in ctx.state:
        ids = [int(x) for x in re.findall(r"/(\d+)/nitem", http.get(BASE + "/").text)]
        ctx.state["max_id"] = max(ids) + 50 if ids else 4000
        ctx.save()
    ids = list(range(1, ctx.state["max_id"] + 1))
    if ctx.sample:
        ids = ids[-60:]
    log(f"news ids 1..{ctx.state['max_id']}")

    def item(i):
        try:
            html = http.get(f"{BASE}/{i}/nitem").text
        except NotFound:
            return
        title = re.search(r'property="og:title"\s+content="([^"]*)"', html)
        date = re.search(r"<time[^>]*>(.*?)</time>", html, re.S)
        text = html_text(html)
        if not title and len(text) < 100:
            return
        ctx.write("news", {"id": i, "title": title.group(1) if title else None,
                           "date": strip_tags(date.group(1)) if date else None, "text": text})

    return ctx.run_items("news", ids, item, workers=2)
