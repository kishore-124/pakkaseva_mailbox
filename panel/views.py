from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import provision
from .forms import DeleteUserForm, LinuxLinkForm, PasswordResetForm, UserCreateForm, UserEditForm
from .models import AuditLog, LinuxAccount


def staff_required(view):
    return login_required(user_passes_test(lambda u: u.is_active and u.is_staff, login_url="home")(view))


def audit(request, action, target, ok=True, detail=""):
    AuditLog.objects.create(actor=request.user, action=action, target=str(target), ok=ok, detail=str(detail)[:4000])


# ------------------------------------------------------------------ dashboard / list


@staff_required
def users(request):
    q = request.GET.get("q", "").strip()
    flt = request.GET.get("filter", "")
    qs = User.objects.select_related("linux_account").annotate(n_accounts=Count("mail_accounts")).order_by("email")
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
                       | Q(email__icontains=q) | Q(linux_account__username__icontains=q))
    if flt == "admins":
        qs = qs.filter(is_staff=True)
    elif flt == "inactive":
        qs = qs.filter(is_active=False)
    elif flt == "linux":
        qs = qs.filter(linux_account__isnull=False)
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    stats = {
        "total": User.objects.count(),
        "inactive": User.objects.filter(is_active=False).count(),
        "admins": User.objects.filter(is_staff=True).count(),
        "linux": LinuxAccount.objects.count(),
    }
    return render(request, "panel/users.html", {
        "page": page, "q": q, "filter": flt, "stats": stats, "section": "users",
        "linux_enabled": provision.is_enabled(),
    })


# ------------------------------------------------------------------ create


@staff_required
def user_create(request):
    form = UserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        created_on_server = None
        try:
            # All or nothing: if the server refuses, the Mailbox login is not created either.
            with transaction.atomic():
                user = form.save()
                if d.get("linux_create"):
                    with provision.Server() as srv:
                        if srv.exists(d["linux_username"]) is not None:
                            raise provision.ProvisionError(
                                f"A Linux user named '{d['linux_username']}' already exists on the server. "
                                "Choose another name, or create the login without the checkbox and link it "
                                "from the user's page."
                            )
                        uid = srv.create(
                            d["linux_username"], d["password1"], shell=d["linux_shell"], groups=d["linux_groups"],
                            comment=user.get_full_name() or user.email,
                        )
                        created_on_server = d["linux_username"]
                        if not user.is_active:
                            srv.lock(d["linux_username"])
                    LinuxAccount.objects.create(
                        user=user, username=d["linux_username"], shell=d["linux_shell"], groups=d["linux_groups"],
                        uid=uid, status=LinuxAccount.STATUS_ACTIVE if user.is_active else LinuxAccount.STATUS_LOCKED,
                    )
        except Exception as exc:
            if created_on_server:  # undo the half-finished server side
                try:
                    with provision.Server() as srv:
                        srv.delete(created_on_server, remove_home=True)
                except provision.ProvisionError:
                    pass
            if not isinstance(exc, provision.ProvisionError):
                raise
            audit(request, "create user", d.get("email", ""), ok=False, detail=exc)
            form.add_error(None, f"Nothing was created. Ubuntu server said: {exc}")
        else:
            detail = f"Linux user {d['linux_username']} (uid {uid})" if d.get("linux_create") else ""
            audit(request, "create user", user.email, detail=detail)
            msg = f"User '{user.email}' created"
            if d.get("linux_create"):
                msg += f", and Linux user '{d['linux_username']}' created on the server"
            messages.success(request, msg + ".")
            return redirect("panel_user", pk=user.pk)
    return render(request, "panel/user_form.html", {
        "form": form, "section": "users", "creating": True, "linux_enabled": provision.is_enabled(),
    })


# ------------------------------------------------------------------ detail / edit


@staff_required
def user_detail(request, pk):
    user = get_object_or_404(User.objects.select_related("linux_account"), pk=pk)
    linux = getattr(user, "linux_account", None)
    form = UserEditForm(request.POST or None, instance=user, current_user=request.user, prefix="edit")
    if request.method == "POST" and "save" in request.POST and form.is_valid():
        was_active = User.objects.get(pk=pk).is_active
        user = form.save()
        audit(request, "edit user", user.email)
        if linux and was_active != user.is_active:
            # Keep the Linux account in step with the Mailbox login.
            _set_linux_lock(request, linux, lock=not user.is_active)
        messages.success(request, "Changes saved.")
        return redirect("panel_user", pk=pk)
    return render(request, "panel/user_detail.html", {
        "u": user, "linux": linux, "form": form, "section": "users",
        "pw_form": PasswordResetForm(user=user, prefix="pw"),
        "link_form": LinuxLinkForm(user=user, prefix="link") if not linux else None,
        "delete_form": DeleteUserForm(prefix="del"),
        "logs": AuditLog.objects.filter(Q(target__in=[user.username, user.email]) | Q(target=getattr(linux, "username", "-")))[:15],
        "accounts": user.mail_accounts.all(),
        "linux_enabled": provision.is_enabled(),
    })


def _set_linux_lock(request, linux, lock):
    action = "lock linux user" if lock else "unlock linux user"
    try:
        with provision.Server() as srv:
            out = srv.lock(linux.username) if lock else srv.unlock(linux.username)
        linux.status = LinuxAccount.STATUS_LOCKED if lock else LinuxAccount.STATUS_ACTIVE
        linux.last_error = ""
        linux.save()
        audit(request, action, linux.username, detail=out)
        messages.info(request, f"Linux user '{linux.username}' {'locked' if lock else 'unlocked'} on the server.")
        return True
    except provision.ProvisionError as exc:
        linux.last_error = str(exc)
        linux.save(update_fields=["last_error", "updated_at"])
        audit(request, action, linux.username, ok=False, detail=exc)
        messages.error(request, f"Could not {action}: {exc}")
        return False


@staff_required
@require_POST
def user_password(request, pk):
    user = get_object_or_404(User, pk=pk)
    form = PasswordResetForm(request.POST, user=user, prefix="pw")
    if not form.is_valid():
        for field, errs in form.errors.items():
            for e in errs:
                messages.error(request, f"Password not changed: {e}")
        return redirect("panel_user", pk=pk)
    pw = form.cleaned_data["password1"]
    linux = getattr(user, "linux_account", None)
    if linux and form.cleaned_data.get("also_linux"):
        try:
            with provision.Server() as srv:
                srv.set_password(linux.username, pw)
        except provision.ProvisionError as exc:
            audit(request, "change password", user.email, ok=False, detail=exc)
            messages.error(request, f"Password NOT changed - the server refused: {exc}")
            return redirect("panel_user", pk=pk)
    user.set_password(pw)
    user.save(update_fields=["password"])
    both = linux and form.cleaned_data.get("also_linux")
    audit(request, "change password", user.email, detail="Mailbox + Linux" if both else "Mailbox only")
    messages.success(request, "Password changed for Mailbox" + (" and on the Ubuntu server." if both else "."))
    return redirect("panel_user", pk=pk)


@staff_required
@require_POST
def linux_link(request, pk):
    user = get_object_or_404(User, pk=pk)
    if hasattr(user, "linux_account"):
        messages.info(request, "This user already has a Linux account.")
        return redirect("panel_user", pk=pk)
    form = LinuxLinkForm(request.POST, user=user, prefix="link")
    if not form.is_valid():
        for errs in form.errors.values():
            for e in errs:
                messages.error(request, e)
        return redirect("panel_user", pk=pk)
    d = form.cleaned_data
    name, pw = d["linux_username"], d.get("password1")
    try:
        with provision.Server() as srv:
            uid = srv.exists(name)
            if uid is not None:
                if not d.get("link_existing"):
                    raise provision.ProvisionError(
                        f"Linux user '{name}' already exists. Tick 'link it instead' to connect it to this login."
                    )
                if pw:
                    srv.set_password(name, pw)
                action, detail = "link linux user", f"linked existing uid {uid}"
            else:
                if not pw:
                    raise provision.ProvisionError("Enter a password to create the new Linux user.")
                uid = srv.create(name, pw, shell=d["linux_shell"], groups=d["linux_groups"],
                                 comment=user.get_full_name() or user.email)
                action, detail = "create linux user", f"uid {uid}"
            if not user.is_active:
                srv.lock(name)
    except provision.ProvisionError as exc:
        audit(request, "create linux user", name, ok=False, detail=exc)
        messages.error(request, f"Ubuntu server said: {exc}")
        return redirect("panel_user", pk=pk)
    LinuxAccount.objects.create(user=user, username=name, shell=d["linux_shell"], groups=d["linux_groups"], uid=uid,
                                status=LinuxAccount.STATUS_ACTIVE if user.is_active else LinuxAccount.STATUS_LOCKED)
    audit(request, action, name, detail=detail)
    messages.success(request, f"Linux user '{name}' {'linked' if action.startswith('link') else 'created'} on the server.")
    return redirect("panel_user", pk=pk)


@staff_required
@require_POST
def linux_action(request, pk, action):
    user = get_object_or_404(User, pk=pk)
    linux = getattr(user, "linux_account", None)
    if not linux:
        return redirect("panel_user", pk=pk)
    if action in ("lock", "unlock"):
        _set_linux_lock(request, linux, lock=action == "lock")
    elif action == "check":
        try:
            with provision.Server() as srv:
                uid = srv.exists(linux.username)
            if uid is None:
                messages.warning(request, f"Linux user '{linux.username}' no longer exists on the server.")
                linux.status, linux.last_error = LinuxAccount.STATUS_FAILED, "missing on server"
            else:
                messages.success(request, f"Linux user '{linux.username}' exists on the server (uid {uid}).")
                linux.uid, linux.last_error = uid, ""
                if linux.status == LinuxAccount.STATUS_FAILED:
                    linux.status = LinuxAccount.STATUS_ACTIVE if user.is_active else LinuxAccount.STATUS_LOCKED
            linux.save()
        except provision.ProvisionError as exc:
            messages.error(request, str(exc))
    elif action == "unlink":
        audit(request, "unlink linux user", linux.username, detail="the Linux account was left on the server")
        linux.delete()
        messages.info(request, f"Unlinked. Linux user '{linux.username}' was left untouched on the server.")
    elif action == "delete":
        remove_home = bool(request.POST.get("remove_home"))
        try:
            with provision.Server() as srv:
                out = srv.delete(linux.username, remove_home=remove_home)
        except provision.ProvisionError as exc:
            audit(request, "delete linux user", linux.username, ok=False, detail=exc)
            messages.error(request, f"Ubuntu server said: {exc}")
            return redirect("panel_user", pk=pk)
        audit(request, "delete linux user", linux.username, detail=out + (" (home removed)" if remove_home else ""))
        linux.delete()
        messages.success(request, f"Linux user '{linux.username}' deleted from the server.")
    return redirect("panel_user", pk=pk)


def _back(request, default):
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}) and nxt.startswith("/"):
        return redirect(nxt)
    return redirect(default)


@staff_required
@require_POST
def user_delete(request, pk):
    user = get_object_or_404(User.objects.select_related("linux_account"), pk=pk)
    if user.pk == request.user.pk:
        messages.error(request, "You cannot delete your own account.")
        return _back(request, "panel_users")
    form = DeleteUserForm(request.POST, prefix="del")
    form.is_valid()
    also_linux = form.cleaned_data.get("also_linux", False)
    remove_home = form.cleaned_data.get("remove_home", False)
    linux = getattr(user, "linux_account", None)
    server_note = ""
    if linux and also_linux:
        try:
            with provision.Server() as srv:
                if srv.exists(linux.username) is None:
                    server_note = f" Linux user '{linux.username}' was already gone from the server."
                else:
                    srv.delete(linux.username, remove_home=remove_home)
                    server_note = f" Linux user '{linux.username}' deleted from the server" + \
                        (" together with its home folder and mail." if remove_home else " (home folder kept).")
        except provision.ProvisionError as exc:
            audit(request, "delete user", user.username, ok=False, detail=exc)
            messages.error(
                request,
                f"'{user.email}' was NOT deleted, because the server could not delete Linux user "
                f"'{linux.username}': {exc} - To delete the user only in this app, untick "
                "'Also delete the Linux user on the server' and try again.",
            )
            return _back(request, "panel_users")
        audit(request, "delete linux user", linux.username, detail=server_note.strip())
    elif linux:
        server_note = f" Linux user '{linux.username}' was left on the server."
    name = user.email or user.username
    user.delete()
    audit(request, "delete user", name, detail=server_note.strip())
    messages.success(request, f"User '{name}' deleted.{server_note}")
    return redirect("panel_users")


@staff_required
@require_POST
def user_toggle(request, pk):
    """Quick activate / deactivate from the list."""
    user = get_object_or_404(User.objects.select_related("linux_account"), pk=pk)
    if user.pk == request.user.pk:
        messages.error(request, "You cannot deactivate your own account.")
        return redirect("panel_users")
    user.is_active = not user.is_active
    user.save(update_fields=["is_active"])
    audit(request, "activate user" if user.is_active else "deactivate user", user.email)
    linux = getattr(user, "linux_account", None)
    if linux:
        _set_linux_lock(request, linux, lock=not user.is_active)
    messages.success(request, f"'{user.email}' {'activated' if user.is_active else 'deactivated'}.")
    nxt = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        nxt = "panel_users"
    return redirect(nxt or "panel_users")


# ------------------------------------------------------------------ server & log


@staff_required
def server(request):
    result = None
    if request.method == "POST":
        try:
            with provision.Server() as srv:
                out = srv.ping()
                fp = srv.host_fingerprint
            result = {"ok": True, "text": out, "fingerprint": fp}
            audit(request, "test server connection", settings.LINUX_USERS["SSH_HOST"], detail=out)
        except provision.ProvisionError as exc:
            result = {"ok": False, "text": str(exc)}
            audit(request, "test server connection", settings.LINUX_USERS["SSH_HOST"], ok=False, detail=exc)
    c = settings.LINUX_USERS
    return render(request, "panel/server.html", {
        "section": "server", "result": result, "cfg": c, "enabled": provision.is_enabled(),
        "host_key_checked": provision.host_key_checked(),
        "auth_kind": "private key" if c["SSH_KEY_FILE"] else ("password" if c["SSH_PASSWORD"] else "not set"),
    })


@staff_required
def logs(request):
    qs = AuditLog.objects.select_related("actor")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(target__icontains=q) | Q(action__icontains=q) | Q(detail__icontains=q))
    if request.GET.get("failed"):
        qs = qs.filter(ok=False)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "panel/logs.html", {"page": page, "q": q, "section": "logs"})
