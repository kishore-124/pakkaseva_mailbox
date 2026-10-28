"""Gmail-style search operators: from: to: subject: is: has: in: after: before: and free text."""
import re
from datetime import datetime

from django.db.models import Q
from django.utils import timezone

TOKEN_RE = re.compile(r'(-?)(\w+):("[^"]*"|\S+)|"([^"]*)"|(\S+)')


def _date(value):
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return timezone.make_aware(datetime.strptime(value, fmt))
        except ValueError:
            continue
    return None


def apply_search(qs, account, query):
    """Filter a Message queryset. Returns (queryset, folder_was_specified)."""
    in_folder = False
    for neg, op, val, phrase, word in TOKEN_RE.findall(query or ""):
        if op:
            op, val = op.lower(), val.strip('"')
            cond = None
            if op == "from":
                cond = Q(from_email__icontains=val) | Q(from_name__icontains=val)
            elif op == "to":
                cond = Q(to__icontains=val) | Q(cc__icontains=val) | Q(bcc__icontains=val)
            elif op == "subject":
                cond = Q(subject__icontains=val)
            elif op == "is":
                cond = {
                    "unread": Q(is_read=False),
                    "read": Q(is_read=True),
                    "starred": Q(is_starred=True),
                    "answered": Q(is_answered=True),
                }.get(val.lower())
            elif op == "has" and val.lower() in ("attachment", "attachments"):
                cond = Q(has_attachments=True)
            elif op in ("in", "label"):
                in_folder = True
                v = val.lower()
                if v == "anywhere":
                    continue
                cond = Q(folder__role=v) | Q(folder__display_name__iexact=val)
                if v == "starred":
                    cond = Q(is_starred=True)
            elif op in ("after", "newer"):
                d = _date(val)
                cond = Q(date__gte=d) if d else None
            elif op in ("before", "older"):
                d = _date(val)
                cond = Q(date__lt=d) if d else None
            else:  # unknown operator: treat as text
                word = f"{op}:{val}"
            if cond is not None:
                qs = qs.exclude(cond) if neg else qs.filter(cond)
                continue
        text = phrase or word
        if text:
            cond = (
                Q(subject__icontains=text)
                | Q(from_name__icontains=text)
                | Q(from_email__icontains=text)
                | Q(to__icontains=text)
                | Q(body_text__icontains=text)
            )
            qs = qs.filter(cond)
    return qs, in_folder
