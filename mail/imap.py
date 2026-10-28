"""IMAP access built on Python's standard imaplib: folder discovery, incremental sync, and
pushing user actions (read/star/move/delete/append) back to the server."""
import imaplib
import logging
import re
import ssl
import time
from contextlib import contextmanager

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from .models import Attachment, Folder, Message
from .parsing import imap_quote, imap_utf7_decode, imap_utf7_encode, parse_message

log = logging.getLogger("mail.imap")
CFG = settings.MAILBOX


class MailError(Exception):
    pass


# ------------------------------------------------------------------ connection


@contextmanager
def connect(account):
    timeout = CFG["IMAP_TIMEOUT"]
    ctx = ssl.create_default_context()
    if account.imap_password_enc and not account.imap_password:
        raise MailError(
            "The saved password could not be decrypted (DJANGO_SECRET_KEY or FIELD_ENCRYPTION_KEY changed). "
            "Edit the account and enter the password again."
        )
    try:
        if account.imap_security == "ssl":
            conn = imaplib.IMAP4_SSL(account.imap_host, account.imap_port, ssl_context=ctx, timeout=timeout)
        else:
            conn = imaplib.IMAP4(account.imap_host, account.imap_port, timeout=timeout)
            if account.imap_security == "starttls":
                conn.starttls(ssl_context=ctx)
        conn._encoding = "utf-8"
        conn.login(account.imap_username, account.imap_password)
    except (imaplib.IMAP4.error, OSError, ssl.SSLError) as exc:
        raise MailError(f"IMAP connection failed: {_err(exc)}") from exc
    try:
        yield conn
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _err(exc):
    msg = exc.args[0] if exc.args else str(exc)
    if isinstance(msg, bytes):
        msg = msg.decode(errors="replace")
    return str(msg)


def _ok(typ, data, what):
    if typ != "OK":
        detail = data[0].decode(errors="replace") if data and isinstance(data[0], bytes) else data
        raise MailError(f"{what} failed: {detail}")


def select(conn, folder, readonly=False):
    typ, data = conn.select(imap_quote(folder.name), readonly=readonly)
    _ok(typ, data, f"SELECT {folder.display_name}")
    return int(data[0] or 0)


def uidset(uids):
    return ",".join(str(int(u)) for u in uids)


# ------------------------------------------------------------------ folders

LIST_RE = re.compile(r'^\((?P<flags>[^)]*)\)\s+(?P<delim>"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.+)$')
NAME_ROLES = {
    "sent": "sent", "sent items": "sent", "sent mail": "sent", "sent messages": "sent",
    "drafts": "drafts", "draft": "drafts",
    "trash": "trash", "deleted items": "trash", "deleted messages": "trash", "bin": "trash",
    "spam": "spam", "junk": "spam", "junk e-mail": "spam", "junk email": "spam", "bulk mail": "spam",
    "archive": "archive", "archives": "archive",
}
FLAG_ROLES = {
    "\\sent": "sent", "\\drafts": "drafts", "\\trash": "trash", "\\junk": "spam",
    "\\archive": "archive", "\\all": "all", "\\flagged": "flagged", "\\important": "important",
}


def _unquote(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return re.sub(r"\\(.)", r"\1", s[1:-1])
    return s


def list_mailboxes(conn):
    typ, data = conn.list()
    _ok(typ, data, "LIST")
    boxes = []
    for item in data:
        if item is None:
            continue
        if isinstance(item, tuple):  # name sent as a literal
            line, literal = item[0].decode(errors="replace"), item[1].decode(errors="replace")
        else:
            line, literal = item.decode(errors="replace"), None
        m = LIST_RE.match(line.strip())
        if not m:
            continue
        name = literal if literal is not None else _unquote(m.group("name"))
        delim = m.group("delim")
        boxes.append(
            {
                "name": name,
                "flags": {f.lower() for f in m.group("flags").split()},
                "delimiter": "" if delim == "NIL" else _unquote(delim),
            }
        )
    return boxes


def sync_folders(account, conn):
    boxes = list_mailboxes(conn)
    roles_taken = set()
    for b in boxes:
        role = "inbox" if b["name"].upper() == "INBOX" else next(
            (FLAG_ROLES[f] for f in b["flags"] if f in FLAG_ROLES), None
        )
        b["role"] = role
        if role:
            roles_taken.add(role)
    for b in boxes:  # name-based fallback for servers without SPECIAL-USE
        if b["role"] is None:
            leaf = imap_utf7_decode(b["name"]).split(b["delimiter"] or "/")[-1].strip().lower()
            guess = NAME_ROLES.get(leaf)
            if guess and guess not in roles_taken:
                b["role"] = guess
                roles_taken.add(guess)
            else:
                b["role"] = "other"

    seen = []
    for b in boxes:
        selectable = not ({"\\noselect", "\\nonexistent"} & b["flags"])
        folder, created = Folder.objects.get_or_create(
            account=account,
            name=b["name"],
            defaults={
                "display_name": imap_utf7_decode(b["name"]),
                "delimiter": b["delimiter"] or "/",
                "role": b["role"],
                "selectable": selectable,
                "sync_enabled": selectable and b["role"] not in Folder.VIRTUAL_ROLES,
            },
        )
        if not created and (folder.role != b["role"] or folder.selectable != selectable):
            folder.role, folder.selectable = b["role"], selectable
            folder.save(update_fields=["role", "selectable"])
        seen.append(folder.pk)
    account.folders.exclude(pk__in=seen).delete()


def ensure_folder(account, conn, role, name):
    """Return the folder for a role, creating it on the server if needed."""
    folder = account.folder_by_role(role)
    if folder:
        return folder
    raw = imap_utf7_encode(name)
    conn.create(imap_quote(raw))
    folder, _ = Folder.objects.get_or_create(
        account=account, name=raw, defaults={"display_name": name, "role": role}
    )
    if folder.role != role:
        folder.role = role
        folder.save(update_fields=["role"])
    return folder


# ------------------------------------------------------------------ fetch parsing

UID_RE = re.compile(rb"UID (\d+)")
FLAGS_RE = re.compile(rb"FLAGS \(([^)]*)\)")
SIZE_RE = re.compile(rb"RFC822\.SIZE (\d+)")


def _iter_fetch(data):
    """Yield (meta_bytes, literal_bytes_or_None) for each message in a FETCH response."""
    i = 0
    while i < len(data):
        item = data[i]
        if isinstance(item, tuple):
            meta, literal = item[0], item[1]
            # Some servers send FLAGS/UID after the literal, as a trailing bytes element.
            if i + 1 < len(data) and isinstance(data[i + 1], bytes):
                meta += b" " + data[i + 1]
                i += 1
            yield meta, literal
        elif isinstance(item, bytes) and UID_RE.search(item):
            yield item, None
        i += 1


def _flags(meta):
    m = FLAGS_RE.search(meta)
    return {f.lower() for f in m.group(1).decode(errors="replace").split()} if m else set()


# ------------------------------------------------------------------ storing messages


def compute_thread_key(account, parsed):
    refs = parsed["references"].split()
    if parsed["in_reply_to"]:
        refs.append(parsed["in_reply_to"])
    if refs:
        key = (
            Message.objects.filter(account=account, message_id__in=refs)
            .exclude(thread_key="")
            .values_list("thread_key", flat=True)
            .first()
        )
        if key:
            return key
    if parsed["message_id"]:
        key = (
            Message.objects.filter(account=account, message_id=parsed["message_id"])
            .exclude(thread_key="")
            .values_list("thread_key", flat=True)
            .first()
        )
        return key or parsed["message_id"]
    return refs[0] if refs else ""


@transaction.atomic
def store_message(account, folder, uid, flags, raw, size=0):
    parsed = parse_message(raw)
    attachments = parsed.pop("attachments")
    fields = dict(
        parsed,
        uid=uid,
        is_read="\\seen" in flags,
        is_starred="\\flagged" in flags,
        is_answered="\\answered" in flags,
        is_draft="\\draft" in flags or folder.role == "drafts",
        has_attachments=any(not a["is_inline"] for a in attachments),
        size=size or len(raw),
    )
    # A copy we created locally (sent mail / draft) waiting for its server UID.
    placeholder = None
    if parsed["message_id"]:
        placeholder = Message.objects.filter(folder=folder, uid__isnull=True, message_id=parsed["message_id"]).first()
    if placeholder:
        for k, v in fields.items():
            if k not in ("thread_key",):
                setattr(placeholder, k, v)
        placeholder.save()
        placeholder.attachments.all().delete()
        msg = placeholder
    else:
        fields["thread_key"] = compute_thread_key(account, parsed) or f"local:{folder.pk}:{uid}"
        msg = Message.objects.create(account=account, folder=folder, **fields)
        # Re-attach replies that arrived before this message.
        if parsed["message_id"]:
            orphans = (
                Message.objects.filter(account=account)
                .filter(Q(in_reply_to=parsed["message_id"]) | Q(references__contains=parsed["message_id"]))
                .exclude(thread_key=msg.thread_key)
                .values_list("thread_key", flat=True)
                .distinct()
            )
            old_keys = list(orphans)
            if old_keys:
                Message.objects.filter(account=account, thread_key__in=old_keys).update(thread_key=msg.thread_key)

    for a in attachments:
        att = Attachment(
            message=msg,
            filename=a["filename"],
            content_type=a["content_type"][:190],
            size=len(a["payload"]),
            content_id=a["content_id"][:490],
            is_inline=a["is_inline"],
        )
        safe = re.sub(r"[^\w.\- ]", "_", a["filename"])[:150] or "file"
        att.file.save(safe, ContentFile(a["payload"]), save=True)
    return msg


# ------------------------------------------------------------------ sync


def sync_folder(account, conn, folder, initial_limit=None):
    if not folder.selectable:
        return 0
    initial_limit = initial_limit or CFG["INITIAL_SYNC_LIMIT"]
    exists = select(conn, folder, readonly=True)
    _, uv = conn.response("UIDVALIDITY")
    uidvalidity = int(uv[0]) if uv and uv[0] else 0

    if folder.uidvalidity != uidvalidity:
        if folder.uidvalidity is not None:
            log.info("UIDVALIDITY changed for %s, resetting", folder)
        folder.messages.filter(uid__isnull=False).delete()
        folder.uidvalidity, folder.last_uid = uidvalidity, 0

    new_count = 0
    if exists:
        # 1. Download new messages.
        if folder.last_uid == 0:
            typ, data = conn.uid("SEARCH", None, "ALL")
        else:
            typ, data = conn.uid("SEARCH", None, f"UID {folder.last_uid + 1}:*")
        _ok(typ, data, "SEARCH")
        uids = sorted(int(x) for x in (data[0] or b"").split() if int(x) > folder.last_uid)
        if folder.last_uid == 0:
            uids = uids[-initial_limit:]
        known = set(folder.messages.filter(uid__in=uids).values_list("uid", flat=True))
        todo = [u for u in uids if u not in known]
        # newest first so the inbox fills from the top
        for i in range(0, len(todo), 20):
            chunk = todo[::-1][i : i + 20]
            typ, data = conn.uid("FETCH", uidset(chunk), "(UID FLAGS RFC822.SIZE BODY.PEEK[])")
            _ok(typ, data, "FETCH")
            for meta, raw in _iter_fetch(data):
                if raw is None:
                    continue
                m = UID_RE.search(meta)
                if not m:
                    continue
                size = SIZE_RE.search(meta)
                try:
                    store_message(account, folder, int(m.group(1)), _flags(meta), raw, int(size.group(1)) if size else 0)
                    new_count += 1
                except Exception:
                    log.exception("Could not store UID %s in %s", m.group(1), folder)
        if uids:
            folder.last_uid = max(folder.last_uid, max(uids))

        # 2. Refresh flags and detect removed messages.
        local = {
            row[0]: row
            for row in folder.messages.filter(uid__isnull=False).values_list(
                "uid", "id", "is_read", "is_starred", "is_answered"
            )
        }
        if local:
            typ, data = conn.uid("FETCH", f"{min(local)}:*", "(UID FLAGS)")
            _ok(typ, data, "FETCH FLAGS")
            remote = {}
            for meta, _ in _iter_fetch(data):
                m = UID_RE.search(meta)
                if m:
                    remote[int(m.group(1))] = _flags(meta)
            changed = []
            for uid, (_, pk, r, s, a) in local.items():
                if uid not in remote:
                    continue
                f = remote[uid]
                nr, ns, na = "\\seen" in f, "\\flagged" in f, "\\answered" in f
                if (nr, ns, na) != (r, s, a):
                    changed.append(Message(pk=pk, is_read=nr, is_starred=ns, is_answered=na))
            if changed:
                Message.objects.bulk_update(changed, ["is_read", "is_starred", "is_answered"], batch_size=500)
            gone = [local[u][1] for u in local if u not in remote]
            if gone:
                Message.objects.filter(pk__in=gone).delete()
    else:
        folder.messages.filter(uid__isnull=False).delete()

    update_counts(folder, save=False)
    folder.save()
    return new_count


def update_counts(folder, save=True):
    folder.total_count = folder.messages.count()
    folder.unread_count = folder.messages.filter(is_read=False).count()
    if save:
        folder.save(update_fields=["total_count", "unread_count"])


@contextmanager
def account_lock(account, wait=False):
    """Postgres advisory lock so the worker and web requests never sync one account concurrently."""
    key = 7_300_000 + account.pk
    with connection.cursor() as cur:
        if wait:
            cur.execute("SELECT pg_advisory_lock(%s)", [key])
            got = True
        else:
            cur.execute("SELECT pg_try_advisory_lock(%s)", [key])
            got = cur.fetchone()[0]
    try:
        yield got
    finally:
        if got:
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", [key])


def sync_account(account, folders=None, wait=False, refresh_folders=True):
    """Sync an account. Returns number of new messages, or None if a sync is already running."""
    with account_lock(account, wait=wait) as got:
        if not got:
            return None
        new = 0
        try:
            with connect(account) as conn:
                if refresh_folders or not account.folders.exists():
                    sync_folders(account, conn)
                targets = folders or account.folders.filter(sync_enabled=True, selectable=True)
                # Inbox first so new mail shows up quickly.
                targets = sorted(targets, key=lambda f: (f.role != "inbox", f.role == "other", f.display_name))
                for folder in targets:
                    folder.refresh_from_db()
                    try:
                        new += sync_folder(account, conn, folder)
                    except (MailError, imaplib.IMAP4.error) as exc:
                        log.warning("Sync of %s/%s failed: %s", account, folder, _err(exc))
            account.last_error = ""
        except (MailError, imaplib.IMAP4.error, OSError) as exc:
            account.last_error = _err(exc)[:1000]
            account.last_synced_at = timezone.now()
            account.save(update_fields=["last_error", "last_synced_at"])
            raise MailError(account.last_error) from exc
        account.last_synced_at = timezone.now()
        account.save(update_fields=["last_error", "last_synced_at"])
        return new


# ------------------------------------------------------------------ actions pushed to the server


def _group_by_folder(messages):
    groups = {}
    for m in messages:
        groups.setdefault(m.folder, []).append(m)
    return groups


def set_flag(account, messages, flag, value):
    """flag is an IMAP system flag such as '\\Seen' or '\\Flagged'."""
    with connect(account) as conn:
        for folder, msgs in _group_by_folder(messages).items():
            uids = [m.uid for m in msgs if m.uid]
            if not uids:
                continue
            select(conn, folder)
            typ, data = conn.uid("STORE", uidset(uids), "+FLAGS.SILENT" if value else "-FLAGS.SILENT", f"({flag})")
            _ok(typ, data, "STORE")


def _copyuid_map(conn):
    """Parse a COPYUID response code (UIDPLUS) into {old_uid: new_uid}."""
    _, data = conn.response("COPYUID")
    mapping = {}
    for item in data or []:
        if not item:
            continue
        parts = item.decode().split() if isinstance(item, bytes) else str(item).split()
        if len(parts) != 3:
            continue
        olds, news = _expand(parts[1]), _expand(parts[2])
        mapping.update(zip(olds, news))
    return mapping


def _expand(s):
    out = []
    for piece in s.split(","):
        if ":" in piece:
            a, b = (int(x) for x in piece.split(":"))
            out.extend(range(min(a, b), max(a, b) + 1))
        else:
            out.append(int(piece))
    return out


def move_messages(account, messages, dest):
    """Move messages to dest folder on the server and mirror the change locally."""
    with connect(account) as conn:
        caps = {c.upper() for c in conn.capabilities}
        for folder, msgs in _group_by_folder(messages).items():
            if folder.pk == dest.pk:
                continue
            uids = [m.uid for m in msgs if m.uid]
            mapping = {}
            if uids:
                select(conn, folder)
                conn.response("COPYUID")  # clear stale codes
                if "MOVE" in caps:
                    typ, data = conn.uid("MOVE", uidset(uids), imap_quote(dest.name))
                    _ok(typ, data, "MOVE")
                else:
                    typ, data = conn.uid("COPY", uidset(uids), imap_quote(dest.name))
                    _ok(typ, data, "COPY")
                    conn.uid("STORE", uidset(uids), "+FLAGS.SILENT", "(\\Deleted)")
                    if "UIDPLUS" in caps:
                        conn.uid("EXPUNGE", uidset(uids))
                    else:
                        conn.expunge()
                mapping = _copyuid_map(conn)
            for m in msgs:
                new_uid = mapping.get(m.uid) if m.uid else None
                # Keep the local row (it becomes a placeholder matched by Message-ID on the next
                # sync if the server did not report the new UID).
                if dest.sync_enabled and (new_uid or m.message_id):
                    Message.objects.filter(pk=m.pk).update(folder=dest, uid=new_uid)
                else:
                    m.delete()
            update_counts(folder)
    update_counts(dest)


def delete_forever(account, messages):
    with connect(account) as conn:
        caps = {c.upper() for c in conn.capabilities}
        for folder, msgs in _group_by_folder(messages).items():
            uids = [m.uid for m in msgs if m.uid]
            if uids:
                select(conn, folder)
                conn.uid("STORE", uidset(uids), "+FLAGS.SILENT", "(\\Deleted)")
                if "UIDPLUS" in caps:
                    conn.uid("EXPUNGE", uidset(uids))
                else:
                    conn.expunge()
            Message.objects.filter(pk__in=[m.pk for m in msgs]).delete()
            update_counts(folder)


def append_message(account, folder, raw_bytes, flags="(\\Seen)"):
    """APPEND a message to a folder. Returns the new UID when the server reports it."""
    with connect(account) as conn:
        conn.response("APPENDUID")
        typ, data = conn.append(imap_quote(folder.name), flags, imaplib.Time2Internaldate(time.time()), raw_bytes)
        _ok(typ, data, "APPEND")
        _, resp = conn.response("APPENDUID")
        for item in resp or []:
            parts = (item.decode() if isinstance(item, bytes) else str(item)).split()
            if len(parts) == 2 and parts[1].isdigit():
                return int(parts[1])
    return None


def test_connection(account):
    with connect(account) as conn:
        return len(list_mailboxes(conn))
