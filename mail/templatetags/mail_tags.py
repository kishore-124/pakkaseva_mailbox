from email.utils import getaddresses

from django import template
from django.template.defaultfilters import date as date_filter
from django.utils import timezone
from django.utils.timesince import timesince

register = template.Library()


@register.filter
def mail_date(value):
    """Gmail-style list date: time today, 'Oct 21' this year, '21/10/25' otherwise."""
    if not value:
        return ""
    local = timezone.localtime(value)
    now = timezone.localtime()
    if local.date() == now.date():
        return date_filter(local, "g:i A")
    if local.year == now.year:
        return date_filter(local, "M j")
    return date_filter(local, "d/m/y")


@register.filter
def full_date(value):
    if not value:
        return ""
    local = timezone.localtime(value)
    now = timezone.localtime()
    fmt = "D, M j, g:i A" if local.year == now.year else "D, M j, Y, g:i A"
    text = date_filter(local, fmt)
    if (now - local).days < 7:
        ago = timesince(local, now).split(",")[0]
        text += f" ({ago} ago)"
    return text


@register.filter
def short_names(value, me=""):
    """'Alice <a@x>, b@y' -> 'Alice, b' ; your own address becomes 'me'."""
    names = []
    for name, addr in getaddresses([value or ""]):
        if not addr and not name:
            continue
        if me and addr.lower() == str(me).lower():
            names.append("me")
        else:
            names.append(name.split()[0] if name else addr.split("@")[0])
    return ", ".join(names)


@register.filter
def filesize(value):
    value = int(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


@register.filter
def recipients_count(m):
    return len([a for _, a in getaddresses([m.to or "", m.cc or ""]) if a])
