import json
import logging
import threading
from email.utils import getaddresses

from django.conf import settings
from django.contrib import messages as flash
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import close_old_connections
from django.db.models import Count, OuterRef, Prefetch, Q, Subquery
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.defaultfilters import date as date_filter
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from . import imap, smtp
from .forms import MailAccountForm, SignupForm
from .models import Attachment, Folder, MailAccount, Message
from .parsing import normalize_subject
from .sanitize import clean_html, render_body
from .search import apply_search

log = logging.getLogger("mail.views")
SYSTEM_VIEWS = {"inbox", "starred", "sent", "drafts", "all", "spam", "trash", "archive"}


# ------------------------------------------------------------------ helpers


def current_account(request):
    accounts = request.user.mail_accounts.all()
    acc_id = request.session.get("account_id")
    account = accounts.filter(pk=acc_id).first() if acc_id else None
    return account or accounts.first()


def run_in_background(fn, *args):
    def target():
        try:
            fn(*args)
        except Exception:
            log.exception("Background mail task failed")
        finally:
            close_old_connections()

    threading.Thread(target=target, daemon=True).start()


def user_message(request, pk):
    return get_object_or_404(Message.objects.select_related("folder", "account"), pk=pk, account__user=request.user)


def clean_path(request, path, default="/mail/inbox/"):
    """Local URL without the internal 'partial' flag (and never an off-site redirect)."""
    from urllib.parse import parse_qsl, urlencode, urlsplit

    from django.utils.http import url_has_allowed_host_and_scheme

    if not path or not url_has_allowed_host_and_scheme(path, allowed_hosts={request.get_host()}) \
            or not path.startswith("/") or path.startswith("//"):
        return default
    parts = urlsplit(path)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k != "partial"])
    return parts.path + ("?" + query if query else "")


def is_fetch(request):
    return request.headers.get("X-Requested-With") == "fetch"


def json_error(msg, status=400):
    return JsonResponse({"ok": False, "error": msg}, status=status)


def view_title(view, folder):
    if folder:
        return folder.label
    return {"starred": "Starred", "all": "All Mail", "search": "Search results"}.get(view, view.title())


def base_queryset(account, view, folder=None):
    qs = Message.objects.filter(account=account)
    if folder is not None:
        return qs.filter(folder=folder)
    excluded = Q(folder__role__in=["trash", "spam"])
    if view == "starred":
        return qs.filter(is_starred=True).exclude(excluded)
    if view in ("all", "search"):
        return qs.exclude(excluded)
    return qs.none()


# ------------------------------------------------------------------ pages


@login_required
def home(request):
    if not request.user.mail_accounts.exists():
        return redirect("account_add")
    return redirect("folder_view", view="inbox")


@login_required
def mailbox(request, view="inbox", label_id=None):
    account = current_account(request)
    if not account:
        return redirect("account_add")

    folder = None
    if label_id is not None:
        folder = get_object_or_404(Folder, pk=label_id, account=account)
        view = "label"
    elif view in ("inbox", "sent", "drafts", "spam", "trash", "archive"):
        folder = account.folder_by_role(view)
        if folder is None and account.folders.exists():
            raise Http404("This account has no such folder")
    elif view not in SYSTEM_VIEWS and view != "search":
        raise Http404

    query = request.GET.get("q", "").strip()
    if query:
        qs, has_in = apply_search(Message.objects.filter(account=account), account, query)
        if not has_in:
            qs = qs.exclude(folder__role__in=["trash", "spam"])
        view, folder = "search", None
    else:
        qs = base_queryset(account, view, folder)

    group_threads = view != "drafts"
    if group_threads:
        latest = qs.order_by("thread_key", "-date").distinct("thread_key").values("pk")
        counts = (
            qs.filter(thread_key=OuterRef("thread_key"))
            .order_by()
            .values("thread_key")
            .annotate(c=Count("pk"))
            .values("c")
        )
        unread = (
            qs.filter(thread_key=OuterRef("thread_key"), is_read=False)
            .order_by()
            .values("thread_key")
            .annotate(c=Count("pk"))
            .values("c")
        )
        rows = (
            Message.objects.filter(pk__in=Subquery(latest))
            .annotate(thread_count=Subquery(counts), thread_unread=Subquery(unread))
            .select_related("folder")
            .order_by("-date")
        )
    else:
        rows = qs.select_related("folder").order_by("-date")

    rows = rows.prefetch_related(
        Prefetch("attachments", queryset=Attachment.objects.filter(is_inline=False).order_by("pk"), to_attr="file_atts")
    )
    paginator = Paginator(rows, settings.MAILBOX["PAGE_SIZE"])
    page = paginator.get_page(request.GET.get("page"))
    for m in page.object_list:
        if not group_threads:
            m.thread_count, m.thread_unread = 1, 0 if m.is_read else 1
        m.row_unread = bool(m.thread_unread)
        m.show_recipient = folder is not None and folder.role in ("sent", "drafts")

    ctx = {
        "account": account,
        "view": view,
        "folder": folder,
        "title": view_title(view, folder),
        "page": page,
        "query": query,
        "back_url": clean_path(request, request.get_full_path()),
    }
    if request.GET.get("partial"):
        # ?partial=1 returns only the list HTML for the JS auto-refresh. Anyone landing on it
        # directly (old link, browser history) gets the full page instead.
        if not is_fetch(request):
            return redirect(ctx["back_url"])
        resp = render(request, "mail/_message_list.html", ctx)
        resp["Cache-Control"] = "no-store"
        resp["Vary"] = "X-Requested-With"
        return resp
    return render(request, "mail/mailbox.html", ctx)


@login_required
def thread(request, pk):
    msg = user_message(request, pk)
    account = msg.account
    if account != current_account(request):
        request.session["account_id"] = account.pk

    if msg.folder.role == "drafts":
        return redirect(f"/mail/drafts/?draft={msg.pk}")

    qs = Message.objects.filter(account=account, thread_key=msg.thread_key).select_related("folder")
    if msg.folder.role not in ("trash", "spam"):
        qs = qs.exclude(folder__role__in=["trash", "spam"])
    # The same message can exist in several folders (e.g. sent to yourself) - show it once.
    chosen = {}
    for m in qs.prefetch_related("attachments").order_by("date", "pk"):
        key = m.message_id or f"pk{m.pk}"
        if key not in chosen or m.folder_id == msg.folder_id:
            chosen[key] = m
    thread_msgs = sorted(chosen.values(), key=lambda m: m.date)
    if not thread_msgs:
        thread_msgs = [msg]

    unread = [m for m in thread_msgs if not m.is_read]
    if unread:
        Message.objects.filter(pk__in=[m.pk for m in unread]).update(is_read=True)
        for f in {m.folder for m in unread}:
            imap.update_counts(f)
        run_in_background(imap.set_flag, account, unread, "\\Seen", True)

    labels = sorted({m.folder for m in thread_msgs if m.folder.role in ("inbox", "other", "spam", "trash")},
                    key=lambda f: f.role != "inbox")
    back = clean_path(request, request.GET.get("back"))
    return render(
        request,
        "mail/thread.html",
        {
            "account": account,
            "msg": msg,
            "thread_msgs": thread_msgs,
            "last": thread_msgs[-1],
            "subject": normalize_subject(msg.subject) or msg.subject or "(no subject)",
            "labels": labels,
            "back_url": back,
            "view": msg.folder.role if msg.folder.role in SYSTEM_VIEWS else "label",
            "folder": msg.folder,
            "move_targets": account.folders.filter(selectable=True).exclude(role__in=["all", "flagged", "important", "drafts"]),
        },
    )


@login_required
@xframe_options_sameorigin
def message_body(request, pk):
    msg = user_message(request, pk)
    resp = HttpResponse(render_body(msg))
    resp["Content-Security-Policy"] = (
        "default-src 'none'; img-src * data: blob:; style-src 'unsafe-inline' *; font-src * data:; "
        "media-src * data:; frame-ancestors 'self'"
    )
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@login_required
def attachment(request, pk):
    att = get_object_or_404(Attachment, pk=pk, message__account__user=request.user)
    inline = request.GET.get("inline") and att.content_type in (
        "image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"
    )
    try:
        resp = FileResponse(att.file.open("rb"), as_attachment=not inline, filename=att.filename,
                            content_type=att.content_type if inline else "application/octet-stream")
    except FileNotFoundError:
        raise Http404
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


# ------------------------------------------------------------------ JSON API


def _expand_threads(account, ids, scope_folder=True):
    """Rows in the list represent conversations; expand to their messages."""
    picked = list(Message.objects.filter(account=account, pk__in=ids).select_related("folder"))
    keys = {m.thread_key for m in picked}
    qs = Message.objects.filter(account=account, thread_key__in=keys).select_related("folder")
    if scope_folder:
        qs = qs.filter(folder_id__in={m.folder_id for m in picked})
    return list(qs)


@login_required
@require_POST
def actions(request):
    try:
        data = json.loads(request.body)
    except ValueError:
        return json_error("Invalid request")
    account = current_account(request)
    ids = [int(i) for i in data.get("ids", []) if str(i).isdigit()]
    action = data.get("action")
    whole_thread = data.get("thread", True)
    if not ids or not account:
        return json_error("Nothing selected")

    if whole_thread:
        msgs = _expand_threads(account, ids, scope_folder=action not in ("read", "unread"))
    else:
        msgs = list(Message.objects.filter(account=account, pk__in=ids).select_related("folder"))
    if not msgs:
        return json_error("Messages not found", 404)

    try:
        if action in ("read", "unread"):
            val = action == "read"
            Message.objects.filter(pk__in=[m.pk for m in msgs]).update(is_read=val)
            for f in {m.folder for m in msgs}:
                imap.update_counts(f)
            run_in_background(imap.set_flag, account, msgs, "\\Seen", val)
            note = "Marked as read" if val else "Marked as unread"
        elif action in ("star", "unstar"):
            val = action == "star"
            if val and whole_thread:  # Gmail stars the latest message of a conversation
                msgs = [max(msgs, key=lambda m: m.date)]
            Message.objects.filter(pk__in=[m.pk for m in msgs]).update(is_starred=val)
            run_in_background(imap.set_flag, account, msgs, "\\Flagged", val)
            note = "Starred" if val else "Unstarred"
        elif action in ("archive", "trash", "spam", "inbox", "move"):
            with imap.connect(account) as conn:
                if action == "archive":
                    dest = account.folder_by_role("archive") or account.folder_by_role("all")
                    if dest is None:
                        dest = imap.ensure_folder(account, conn, "archive", "Archive")
                elif action == "trash":
                    dest = account.folder_by_role("trash") or imap.ensure_folder(account, conn, "trash", "Trash")
                elif action == "spam":
                    dest = account.folder_by_role("spam") or imap.ensure_folder(account, conn, "spam", "Junk")
                elif action == "inbox":
                    dest = account.folder_by_role("inbox")
                else:
                    dest = get_object_or_404(Folder, pk=data.get("folder_id"), account=account)
            if action == "trash" and all(m.folder_id == dest.pk for m in msgs):
                imap.delete_forever(account, msgs)
                note = "Deleted forever"
            else:
                imap.move_messages(account, [m for m in msgs if m.folder_id != dest.pk], dest)
                note = {
                    "archive": "Conversation archived",
                    "trash": "Conversation moved to Trash",
                    "spam": "Conversation marked as spam",
                    "inbox": "Conversation moved to Inbox",
                }.get(action, f"Moved to {dest.label}")
        elif action == "delete_forever":
            imap.delete_forever(account, msgs)
            note = "Deleted forever"
        else:
            return json_error("Unknown action")
    except imap.MailError as exc:
        return json_error(str(exc), 502)
    return JsonResponse({"ok": True, "message": note, "count": len(ids)})


@login_required
@require_POST
def sync_now(request):
    account = current_account(request)
    if not account:
        return json_error("No account")
    folder = None
    if request.POST.get("folder_id", "").isdigit():
        folder = account.folders.filter(pk=request.POST["folder_id"]).first()
    targets = [f for f in [account.folder_by_role("inbox"), folder] if f and f.sync_enabled]
    try:
        new = imap.sync_account(account, folders=targets or None, refresh_folders=not account.folders.exists())
    except imap.MailError as exc:
        return json_error(str(exc), 502)
    return JsonResponse({"ok": True, "new": new, "busy": new is None})


@login_required
def counts(request):
    account = current_account(request)
    if not account:
        return JsonResponse({"folders": {}})
    folders = {f.pk: {"unread": f.unread_count, "total": f.total_count, "role": f.role} for f in account.folders.all()}
    latest = Message.objects.filter(account=account, folder__role="inbox").order_by("-pk").values_list("pk", flat=True).first()
    return JsonResponse({
        "folders": folders,
        "latest_inbox_id": latest or 0,
        "last_synced": account.last_synced_at.isoformat() if account.last_synced_at else None,
        "error": account.last_error,
    })


@login_required
def contacts(request):
    q = request.GET.get("q", "").strip()
    account = current_account(request)
    if len(q) < 1 or not account:
        return JsonResponse({"results": []})
    seen, out = set(), []
    qs = Message.objects.filter(account=account).filter(
        Q(from_email__icontains=q) | Q(from_name__icontains=q) | Q(to__icontains=q)
    ).order_by("-date").values_list("from_name", "from_email", "to")[:300]
    for name, email, to in qs:
        cands = [(name, email)] + getaddresses([to])
        for n, e in cands:
            if not e or e.lower() in seen:
                continue
            if q.lower() in e.lower() or q.lower() in (n or "").lower():
                seen.add(e.lower())
                out.append({"name": n, "email": e})
        if len(out) >= 8:
            break
    return JsonResponse({"results": out[:8]})


def _quote_block(m):
    when = date_filter(timezone.localtime(m.date), "D, M j, Y \\a\\t g:i A")
    sender = escape(f"{m.from_name} <{m.from_email}>" if m.from_name else m.from_email)
    original = clean_html(m.body_html, for_display=False) if m.body_html else escape(m.body_text).replace("\n", "<br>")
    return when, sender, original


@login_required
def compose_data(request):
    """Prefill data for reply / reply-all / forward / editing a draft."""
    account = current_account(request)
    mode = request.GET.get("mode", "new")
    data = {"to": "", "cc": "", "bcc": "", "subject": "", "body": "", "in_reply_to": "",
            "attachments": [], "draft_id": "", "account_id": account.pk if account else ""}
    if mode == "draft":
        m = user_message(request, request.GET.get("id"))
        data.update(
            to=m.to, cc=m.cc, bcc=m.bcc, subject=m.subject, draft_id=m.pk, account_id=m.account_id,
            body=clean_html(m.body_html, for_display=False) if m.body_html else escape(m.body_text).replace("\n", "<br>"),
            attachments=[{"id": a.pk, "name": a.filename, "size": a.size} for a in m.attachments.all() if not a.is_inline],
        )
        parent = Message.objects.filter(account=m.account, message_id=m.in_reply_to).first() if m.in_reply_to else None
        data["in_reply_to"] = parent.pk if parent else ""
        return JsonResponse(data)
    if mode in ("reply", "replyall", "forward"):
        m = user_message(request, request.GET.get("id"))
        account = m.account
        data["account_id"] = account.pk
        base = normalize_subject(m.subject)
        when, sender, original = _quote_block(m)
        if mode == "forward":
            data["subject"] = f"Fwd: {base}"
            data["body"] = (
                "<br><br><div>---------- Forwarded message ---------<br>"
                f"From: {sender}<br>Date: {when}<br>Subject: {escape(m.subject)}<br>"
                f"To: {escape(m.to)}<br>{('Cc: ' + escape(m.cc) + '<br>') if m.cc else ''}<br>{original}</div>"
            )
            data["attachments"] = [{"id": a.pk, "name": a.filename, "size": a.size} for a in m.attachments.all() if not a.is_inline]
        else:
            mine = m.from_email.lower() == account.email.lower()
            if mine:  # replying to our own sent mail goes to the original recipients
                to_list = getaddresses([m.to])
            else:
                to_list = getaddresses([m.reply_to or f"{m.from_name} <{m.from_email}>"])
            to = ", ".join(f"{n} <{e}>" if n else e for n, e in to_list if e)
            data["to"] = to
            if mode == "replyall":
                exclude = {account.email.lower()} | {e.lower() for _, e in to_list}
                cc_pairs = [(n, e) for n, e in getaddresses([m.to, m.cc]) if e and e.lower() not in exclude]
                uniq = {}
                for n, e in cc_pairs:
                    uniq.setdefault(e.lower(), f"{n} <{e}>" if n else e)
                data["cc"] = ", ".join(uniq.values())
            data["subject"] = f"Re: {base}"
            data["body"] = (
                f'<br><br><div class="mb-quote"><div>On {when}, {sender} wrote:</div>'
                f'<blockquote style="margin:0 0 0 .8ex;border-left:1px solid #ccc;padding-left:1ex">{original}</blockquote></div>'
            )
            data["in_reply_to"] = m.pk
    if account and account.signature and mode in ("new", "reply", "replyall", "forward"):
        data["body"] = f'<br><br><div class="mb-signature">-- <br>{clean_html(account.signature, for_display=False)}</div>' + data["body"]
    return JsonResponse(data)


def _compose_parts(request):
    account = get_object_or_404(MailAccount, pk=request.POST.get("account_id") or 0, user=request.user)
    p = request.POST
    max_bytes = settings.MAILBOX["MAX_ATTACHMENT_MB"] * 1024 * 1024
    files, total = [], 0
    for f in request.FILES.getlist("attachments"):
        total += f.size
        files.append((f.name, f.content_type, f.read()))
    for att_id in p.getlist("keep_attachments"):
        if att_id.isdigit():
            att = Attachment.objects.filter(pk=att_id, message__account__user=request.user).first()
            if att:
                with att.file.open("rb") as fh:
                    content = fh.read()
                total += len(content)
                files.append((att.filename, att.content_type, content))
    if total > max_bytes:
        raise imap.MailError(f"Attachments exceed {settings.MAILBOX['MAX_ATTACHMENT_MB']} MB")
    parent = None
    if p.get("in_reply_to", "").isdigit():
        parent = Message.objects.filter(pk=p["in_reply_to"], account__user=request.user).first()
    msg = smtp.build_message(
        account,
        to=p.get("to", "").strip(),
        cc=p.get("cc", "").strip(),
        bcc=p.get("bcc", "").strip(),
        subject=p.get("subject", "").strip(),
        html_body=clean_html(p.get("body", ""), for_display=False),
        attachments=files,
        in_reply_to=parent.message_id if parent else "",
        references=parent.references if parent else "",
    )
    draft = None
    if p.get("draft_id", "").isdigit():
        draft = Message.objects.filter(pk=p["draft_id"], account=account).select_related("folder").first()
    return account, msg, parent, draft


def _store_copy(account, folder, raw, flags, upload=True):
    uid = imap.append_message(account, folder, raw, "(" + " ".join(flags) + ")") if upload else None
    imap.store_message(account, folder, uid, {f.lower() for f in flags}, raw)
    imap.update_counts(folder)


@login_required
@require_POST
def send(request):
    try:
        account, msg, parent, draft = _compose_parts(request)
        rcpts = smtp.recipients(msg.get("To", ""), msg.get("Cc", ""), msg.get("Bcc", ""))
        smtp.send(account, msg, rcpts)
    except imap.MailError as exc:
        return json_error(str(exc), 502)

    raw = msg.as_bytes()
    # Post-send bookkeeping; failures here must not report the send as failed.
    try:
        with imap.connect(account) as conn:
            sent = account.folder_by_role("sent") or imap.ensure_folder(account, conn, "sent", "Sent")
        _store_copy(account, sent, raw, ["\\Seen"], upload=account.save_sent_copy and not account.is_gmail)
        if parent:
            Message.objects.filter(pk=parent.pk).update(is_answered=True)
            run_in_background(imap.set_flag, account, [parent], "\\Answered", True)
        if draft:
            imap.delete_forever(account, [draft])
    except Exception:
        log.exception("Post-send bookkeeping failed")
    return JsonResponse({"ok": True, "message": "Message sent"})


@login_required
@require_POST
def save_draft(request):
    try:
        account, msg, parent, draft = _compose_parts(request)
        with imap.connect(account) as conn:
            drafts = account.folder_by_role("drafts") or imap.ensure_folder(account, conn, "drafts", "Drafts")
        raw = msg.as_bytes()
        uid = imap.append_message(account, drafts, raw, "(\\Draft \\Seen)")
        new = imap.store_message(account, drafts, uid, {"\\draft", "\\seen"}, raw)
        if draft:
            imap.delete_forever(account, [draft])
        imap.update_counts(drafts)
    except imap.MailError as exc:
        return json_error(str(exc), 502)
    atts = [{"id": a.pk, "name": a.filename, "size": a.size} for a in new.attachments.all() if not a.is_inline]
    return JsonResponse({"ok": True, "draft_id": new.pk, "attachments": atts, "message": "Draft saved"})


@login_required
@require_POST
def discard_draft(request, pk):
    m = user_message(request, pk)
    if m.folder.role != "drafts":
        return json_error("Not a draft")
    try:
        imap.delete_forever(m.account, [m])
    except imap.MailError as exc:
        return json_error(str(exc), 502)
    return JsonResponse({"ok": True, "message": "Draft discarded"})


# ------------------------------------------------------------------ accounts & auth


def signup(request):
    if not settings.MAILBOX["ALLOW_SIGNUP"]:
        raise Http404
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect("account_add")
    return render(request, "registration/signup.html", {"form": form})


@login_required
def account_list(request):
    return render(request, "mail/accounts.html", {"accounts": request.user.mail_accounts.all(), "account": current_account(request)})


@login_required
def account_edit(request, pk=None):
    instance = get_object_or_404(MailAccount, pk=pk, user=request.user) if pk else None
    initial = None
    if instance is None and request.user.email and not request.user.mail_accounts.exists():
        initial = {"email": request.user.email, "imap_username": request.user.email,
                   "display_name": request.user.get_full_name()}
    form = MailAccountForm(request.POST or None, instance=instance, initial=initial)
    if request.method == "POST" and form.is_valid():
        acc = form.save(commit=False)
        acc.user = request.user
        try:
            imap.test_connection(acc)
            smtp.test_smtp(acc)
        except imap.MailError as exc:
            form.add_error(None, str(exc))
        else:
            acc.save()
            request.session["account_id"] = acc.pk
            if instance is None:
                try:  # discover folders + first inbox batch right away
                    with imap.connect(acc) as conn:
                        imap.sync_folders(acc, conn)
                    run_in_background(imap.sync_account, acc)
                except imap.MailError:
                    pass
                flash.success(request, "Account added. Your mail is being downloaded…")
                return redirect("home")
            flash.success(request, "Account updated.")
            return redirect("account_list")
    return render(request, "mail/account_form.html", {"form": form, "instance": instance, "account": current_account(request)})


@login_required
@require_POST
def account_delete(request, pk):
    acc = get_object_or_404(MailAccount, pk=pk, user=request.user)
    acc.delete()
    request.session.pop("account_id", None)
    flash.success(request, f"Removed {acc.email}.")
    return redirect("account_list")


@login_required
def account_switch(request, pk):
    acc = get_object_or_404(MailAccount, pk=pk, user=request.user)
    request.session["account_id"] = acc.pk
    return redirect("folder_view", view="inbox")


@login_required
@require_POST
def folder_toggle_sync(request, pk):
    f = get_object_or_404(Folder, pk=pk, account__user=request.user)
    f.sync_enabled = not f.sync_enabled
    f.save(update_fields=["sync_enabled"])
    return redirect("account_list")
