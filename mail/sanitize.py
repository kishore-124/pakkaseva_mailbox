"""Sanitising e-mail HTML before it is shown (inside a sandboxed iframe) or sent."""
import html
import re

import nh3
from django.urls import reverse

ATTRS_ALL = {
    "style", "class", "id", "align", "valign", "width", "height", "bgcolor", "color", "border",
    "cellpadding", "cellspacing", "dir", "title", "lang", "role",
}
TAGS = set(nh3.ALLOWED_TAGS) | {"style", "font", "center", "span", "div", "u", "s", "strike", "big", "small"}
ATTRIBUTES = {
    "*": ATTRS_ALL,
    "a": {"href", "name"},
    "img": {"src", "alt", "width", "height"},
    "font": {"face", "size", "color"},
    "td": {"colspan", "rowspan", "nowrap", "background"},
    "th": {"colspan", "rowspan", "nowrap", "background"},
    "table": {"background", "summary"},
    "ol": {"start", "type"},
    "li": {"value"},
}


def clean_html(value: str, for_display=True) -> str:
    return nh3.clean(
        value or "",
        tags=TAGS,
        clean_content_tags={"script"},
        attributes=ATTRIBUTES,
        url_schemes={"http", "https", "mailto", "tel", "data"},
        set_tag_attribute_values={"a": {"target": "_blank"}} if for_display else None,
        link_rel="noopener noreferrer nofollow",
        strip_comments=True,
    )


URL_RE = re.compile(r"(https?://[^\s<>\"']+)")


def text_to_html(text: str) -> str:
    escaped = html.escape(text or "")
    linked = URL_RE.sub(r'<a href="\1">\1</a>', escaped)
    return f'<div style="white-space:pre-wrap;word-wrap:break-word">{linked}</div>'


def render_body(message) -> str:
    """Return a full, sanitised HTML document for a message body, with cid: images resolved."""
    if message.body_html:
        body = message.body_html
        cids = {a.content_id: a.pk for a in message.attachments.all() if a.content_id}
        if cids:
            def repl(m):
                pk = cids.get(m.group(2))
                if pk is None:
                    return m.group(0)
                return m.group(1) + reverse("attachment", args=[pk]) + "?inline=1"

            body = re.sub(r'''(src=["']?)cid:([^"'\s>]+)''', repl, body, flags=re.I)
        body = clean_html(body)
    else:
        body = clean_html(text_to_html(message.body_text))
    return (
        '<!doctype html><html><head><meta charset="utf-8"><base target="_blank">'
        "<style>html,body{margin:0;padding:0}body{font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1d2a2c;"
        "overflow-wrap:anywhere}img{max-width:100%;height:auto}blockquote{margin:0 0 0 .8ex;"
        "border-left:1px solid #ccc;padding-left:1ex}pre{white-space:pre-wrap}</style></head>"
        f"<body>{body}</body></html>"
    )
