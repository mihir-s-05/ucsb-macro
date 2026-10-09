"""Display NetNutrition's full label without running upstream scripts."""
from html import escape
from functools import lru_cache
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

SOURCE = "https://nutrition.info.dining.ucsb.edu/NetNutrition/1"
DOCUMENT_VERSION = 2
STYLE_HOSTS = {"nutrition.info.dining.ucsb.edu", "fonts.googleapis.com", "arit-ucsb.github.io"}
LABEL_TAGS = {"div", "table", "thead", "tbody", "tfoot", "tr", "td", "th", "span", "br", "b", "strong", "i", "em", "p", "small"}
LABEL_ATTRIBUTES = {"id", "class", "style", "width", "height", "align", "valign", "colspan", "rowspan", "cellpadding", "cellspacing"}


@lru_cache(maxsize=16)
def stylesheet_links(landing):
    page = BeautifulSoup(landing, "html.parser")
    base = page.find("base", href=True)
    base_url = urljoin(SOURCE, base["href"]) if base else SOURCE
    styles = []
    for link in page.select("link[rel=stylesheet][href]"):
        url = urljoin(base_url, link["href"])
        parsed = urlparse(url)
        if parsed.scheme == "https" and parsed.hostname in STYLE_HOSTS:
            styles.append(f'<link rel="stylesheet" href="{escape(url, quote=True)}">')
    return ''.join(styles)


def label_document(raw_label, landing):
    soup = BeautifulSoup(raw_label, "html.parser")
    label = soup.select_one("#nutritionLabel")
    header = label.select_one(".cbo_nn_LabelHeader") if label else None
    if not header:
        raise ValueError("Nutrition label unavailable for this item.")
    name = header.get_text(" ", strip=True)
    for element in [label, *label.find_all(True)]:
        if element.parent is None:
            continue
        if element.name not in LABEL_TAGS:
            element.decompose()
            continue
        for attribute in list(element.attrs):
            if attribute not in LABEL_ATTRIBUTES:
                del element[attribute]
    policy = "default-src 'none'; style-src 'unsafe-inline' https://nutrition.info.dining.ucsb.edu https://fonts.googleapis.com https://arit-ucsb.github.io; font-src https://fonts.gstatic.com https://nutrition.info.dining.ucsb.edu; script-src 'none'; base-uri 'none'"
    document = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{escape(policy, quote=True)}">
<title>{escape(name)} — Nutrition label</title>
{stylesheet_links(landing)}
<style>html,body{{margin:0;padding:0;background:#fff;color:#000;overflow:hidden}}body{{padding:12px;min-height:0;height:auto}}#nutritionLabel{{width:100%;max-width:100%;margin:0 auto}}#nutritionLabel table{{max-width:100%}}.horizontal-flex-container{{max-width:100%}}</style>
</head><body>{label}</body></html>'''
    return {"name": name, "document": document}
