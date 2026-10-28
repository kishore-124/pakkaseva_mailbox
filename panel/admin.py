from django.contrib import admin

from .models import AuditLog, LinuxAccount


@admin.register(LinuxAccount)
class LinuxAccountAdmin(admin.ModelAdmin):
    list_display = ("username", "user", "uid", "shell", "status", "updated_at")
    list_filter = ("status",)
    search_fields = ("username", "user__username")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "target", "ok")
    list_filter = ("ok", "action")
    search_fields = ("target", "detail")
    readonly_fields = [f.name for f in AuditLog._meta.fields]
