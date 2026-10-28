from django.contrib import admin

from .models import Attachment, Folder, MailAccount, Message


@admin.register(MailAccount)
class MailAccountAdmin(admin.ModelAdmin):
    list_display = ("email", "user", "imap_host", "smtp_host", "is_active", "last_synced_at")
    exclude = ("imap_password_enc", "smtp_password_enc")


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ("display_name", "account", "role", "sync_enabled", "unread_count", "total_count", "last_uid")
    list_filter = ("role", "sync_enabled")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("subject", "from_email", "folder", "date", "is_read", "is_starred")
    list_filter = ("is_read", "is_starred", "folder__role")
    search_fields = ("subject", "from_email", "to")
    raw_id_fields = ("account", "folder")


admin.site.register(Attachment)
