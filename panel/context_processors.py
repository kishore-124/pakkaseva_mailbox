def panel(request):
    user = getattr(request, "user", None)
    is_admin = bool(user and user.is_authenticated and user.is_active and user.is_staff)
    ctx = {"is_panel_admin": is_admin}
    if is_admin and request.path.startswith("/manage/"):
        from . import provision

        ctx["linux_enabled"] = provision.is_enabled()
    return ctx
