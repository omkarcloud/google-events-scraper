"""Low-level extraction for Google Events responses (verified 2026-09-26).

  * RESULTS PAGE   www.google.com/search?q=… — the "Events" carousel. The
    first ~4 cards are inline HTML; the rest arrive as deferred fragments
    `[{id:'<placeholder id>'},function(){jsl.dh(this.id,"<escaped html>");}]`
    inside a script, injected into the placeholder element (fragments nest:
    a fragment can hold the placeholder of the next one). Lazily loaded card
    images: `google.ldi={"dimg_…": "https://encrypted-tbn…"}` (URLs) plus the
    `var s='data:image…';var ii=[…]` pairs (inline data URIs) the Google
    Search parsers already read.
  * CHUNK          /async/callback:8243 (the event viewer) — `)]}'` then typed
    frames: `c;[2,null,"0"]<hex>;` HTML, `c;[5,…]` JS data, `c;[9,…]` end.
    google_jobs.markup reads the frame header and the JS data.
"""
import json
import re

from lxml import html as lxml_html

from google_jobs.markup import JsData, is_refused  # noqa: F401 (is_refused re-exported for fetch)
from google_search.serp_parsers import js_unescape

_DH_RE = re.compile(r"\[\{id:'([^']+)'\},function\(\)\{jsl\.dh\(this\.id,\"((?:[^\"\\]|\\.)*)\"\);\}\]")
_LDI_RE = re.compile(r"google\.ldi=(\{.*?\});")
_HTML_FRAME_RE = re.compile(r'c;\[2,null,"0"\][0-9a-f]+;')
_UTF8 = lxml_html.HTMLParser(encoding="utf-8")


def deferred_fragments(text):
    """{placeholder id: html} of every jsl.dh fragment in a page."""
    out = {}
    for m in _DH_RE.finditer(text or ""):
        out.setdefault(m.group(1), js_unescape(m.group(2)))
    return out


def lazy_image_links(text):
    """{img id: image URL} from the page's google.ldi map."""
    out = {}
    for m in _LDI_RE.finditer(text or ""):
        try:
            value = json.loads(m.group(1))
        except ValueError:
            continue
        out.update({k: v for k, v in value.items() if isinstance(v, str) and v.startswith("http")})
    return out


def inflated_document(text):
    """The page parsed with every deferred fragment placed into its
    placeholder, so the carousel reads in page order."""
    doc = lxml_html.fromstring((text or "<html></html>").encode("utf-8", "replace"), parser=_UTF8)
    pending = deferred_fragments(text)
    placed = True
    while pending and placed:
        placed = False
        for fid in list(pending):
            try:
                el = doc.get_element_by_id(fid)
            except KeyError:
                continue
            try:
                parts = lxml_html.fragments_fromstring(pending.pop(fid), parser=_UTF8)
            except Exception:
                continue
            for part in parts:
                if isinstance(part, str):
                    el.text = (el.text or "") + part
                else:
                    el.append(part)
            placed = True
    return doc


def chunk_html(text):
    """The HTML frames of a callback chunk, joined."""
    text = text or ""
    out = []
    for m in _HTML_FRAME_RE.finditer(text):
        end = text.find("c;[", m.end())
        out.append(text[m.end(): end if end >= 0 else len(text)])
    return "".join(out)


def chunk_document(text):
    """A callback chunk's HTML as one lxml tree (an empty <div> when none)."""
    body = chunk_html(text)
    return lxml_html.fromstring(("<div>" + body + "</div>").encode("utf-8", "replace"), parser=_UTF8)


def walk(value):
    """Every node of a nested JSON value, depth first."""
    stack = [value]
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, list):
            stack.extend(reversed(node))
        elif isinstance(node, dict):
            stack.extend(reversed(list(node.values())))


def js_values(text):
    """Every decoded JS data value of a chunk (the `c;[5,…]` frames)."""
    js = JsData(text)
    js._load_pairs()
    return [js.get(key) for key in js._pairs]
