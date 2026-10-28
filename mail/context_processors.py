from django.conf import settings

from .models import Folder

NAV = [
    ("inbox", "Inbox", "inbox"),
    ("starred", "Starred", "star"),
    ("sent", "Sent", "send"),
    ("drafts", "Drafts", "file-earmark"),
    ("all", "All Mail", "envelope"),
    ("spam", "Spam", "exclamation-octagon"),
    ("trash", "Trash", "trash"),
]


def brand(request):
    return {"BRAND": settings.BRAND}


def mailbox(request):
    if not request.user.is_authenticated:
        return {"allow_signup": settings.MAILBOX["ALLOW_SIGNUP"]}
    accounts = list(request.user.mail_accounts.all())
    if not accounts:
        return {"accounts": []}
    acc_id = request.session.get("account_id")
    account = next((a for a in accounts if a.pk == acc_id), accounts[0])
    folders = list(account.folders.all())
    by_role = {}
    for f in folders:
        by_role.setdefault(f.role, f)
    nav = []
    for key, label, icon in NAV:
        f = by_role.get(key)
        if key in ("sent", "drafts", "spam", "trash") and f is None:
            continue
        count = 0
        if f:
            count = f.total_count if key == "drafts" else (f.unread_count if key in ("inbox", "spam") else 0)
        nav.append({"key": key, "label": label, "icon": icon, "count": count, "folder_id": f.pk if f else ""})
    if "archive" in by_role:
        f = by_role["archive"]
        nav.insert(5, {"key": "archive", "label": "Archive", "icon": "archive", "count": 0, "folder_id": f.pk})
    labels = [f for f in folders if f.role == "other" and f.selectable]
    return {
        "accounts": accounts,
        "nav_account": account,
        "nav_items": nav,
        "nav_labels": labels,
        "ROLE_ICONS": Folder.ROLE_ICONS,
    }
