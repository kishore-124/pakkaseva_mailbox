"""Helpers for parsing RFC 822 messages and IMAP mailbox names."""
import base64
import html
import re
from datetime import datetime, timezone as dt_timezone
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

# ---------------------------------------------------------------- IMAP modified UTF-7 (RFC 3501 5.1.3)


def imap_utf7_decode(name: str) -> str:
    def repl(m):
        chunk = m.group(1)
        if chunk == "":
            return "&"
        b64 = chunk.replace(",", "/")
        b64 += "=" * (-len(b64) % 4)
        try:
            return base64.b64decode(b64).decode("utf-16-be")
        except Exception:
            return m.group(0)

    return re.sub(r"&([A-Za-z0-9+,]*)-", repl, name)


def imap_utf7_encode(name: str) -> str:
    out, buf = [], []

    def flush():
        if buf:
            b = base64.b64encode("".join(buf).encode("utf-16-be")).decode().rstrip("=").replace("/", ",")
            out.append("&" + b + "-")
            buf.clear()

    for ch in name:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)


def imap_quote(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------------------------------------------------------- headers


def decode_str(value) -> str:
    if value is None:
        return ""
    value = str(value)
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def clean_msgid(value: str) -> str:
    value = (value or "").strip()
    m = re.search(r"<[^>]+>", value)
    return m.group(0) if m else value


def parse_references(value: str):
    return re.findall(r"<[^>]+>", value or "")


def format_addresses(value) -> str:
    """Normalise an address header into 'Name <email>, other@x' form."""
    if not value:
        return ""
    pairs = getaddresses([decode_str(value)])
    parts = []
    for name, addr in pairs:
        if not addr and not name:
            continue
        parts.append(f"{name} <{addr}>" if name else addr)
    return ", ".join(parts)


def split_addresses(value: str):
    """Return a list of bare e-mail addresses from free-form input."""
    return [addr for _, addr in getaddresses([value or ""]) if addr and "@" in addr]


def parse_date(value):
    if value:
        try:
            dt = parsedate_to_datetime(str(value))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=dt_timezone.utc)
            return dt
        except Exception:
            pass
    return datetime.now(dt_timezone.utc)


# ---------------------------------------------------------------- bodies

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"(?i)<\s*(br|/p|/div|/li|/tr|/h[1-6])\s*/?>")
_STRIP_RE = re.compile(r"(?is)<(script|style|head|title)[^>]*>.*?</\1>")


def html_to_text(value: str) -> str:
    value = _STRIP_RE.sub(" ", value or "")
    value = _BLOCK_RE.sub("\n", value)
    value = _TAG_RE.sub("", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*\n\s*\n+", "\n\n", value)
    return value.strip()


def make_snippet(text: str, length=200) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:length]


def _part_text(part) -> str:
    try:
        return part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")


def parse_message(raw: bytes) -> dict:
    """Parse raw RFC822 bytes into a dict of fields plus attachment payloads."""
    msg = BytesParser(policy=policy.default).parsebytes(raw)

    text_parts, html_parts, attachments = [], [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disp = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        if filename:
            filename = decode_str(filename)
        content_id = (part.get("Content-ID") or "").strip().strip("<>")

        is_body = disp != "attachment" and not filename and ctype in ("text/plain", "text/html")
        if is_body:
            (html_parts if ctype == "text/html" else text_parts).append(_part_text(part))
            continue
        if ctype == "message/rfc822" and not filename:
            filename = "forwarded-message.eml"
        payload = part.get_payload(decode=True)
        if payload is None:
            if ctype == "message/rfc822":
                inner = part.get_payload()
                payload = inner[0].as_bytes() if isinstance(inner, list) and inner else b""
            else:
                continue
        if not filename:
            ext = ctype.split("/")[-1][:8] if "/" in ctype else "bin"
            filename = f"attachment.{ext}"
        attachments.append(
            {
                "filename": filename[:400],
                "content_type": ctype,
                "payload": payload,
                "content_id": content_id,
                "is_inline": disp == "inline" and bool(content_id),
            }
        )

    body_html = "\n".join(html_parts)
    body_text = "\n".join(text_parts)
    if not body_text and body_html:
        body_text = html_to_text(body_html)

    from_name, from_email = parseaddr(decode_str(msg.get("From", "")))
    references = " ".join(parse_references(msg.get("References", "")))
    return {
        "message_id": clean_msgid(msg.get("Message-ID", ""))[:990],
        "in_reply_to": clean_msgid(msg.get("In-Reply-To", ""))[:990],
        "references": references,
        "subject": decode_str(msg.get("Subject", "")),
        "from_name": from_name[:490],
        "from_email": from_email[:490],
        "to": format_addresses(msg.get("To")),
        "cc": format_addresses(msg.get("Cc")),
        "bcc": format_addresses(msg.get("Bcc")),
        "reply_to": format_addresses(msg.get("Reply-To")),
        "date": parse_date(msg.get("Date")),
        "body_text": body_text,
        "body_html": body_html,
        "snippet": make_snippet(html_to_text(body_html) if body_html else body_text),
        "attachments": attachments,
    }


def normalize_subject(subject: str) -> str:
    return re.sub(r"^\s*((re|fw|fwd|aw|sv)\s*(\[\d+\])?\s*:\s*)+", "", subject or "", flags=re.I).strip()
