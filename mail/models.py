import os

from django.conf import settings
from django.db import models
from django.urls import reverse

from . import crypto

SECURITY_CHOICES = [("ssl", "SSL/TLS"), ("starttls", "STARTTLS"), ("none", "None (insecure)")]


class MailAccount(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mail_accounts")
    email = models.EmailField()
    display_name = models.CharField(max_length=200, blank=True)

    imap_host = models.CharField(max_length=255)
    imap_port = models.PositiveIntegerField(default=993)
    imap_security = models.CharField(max_length=10, choices=SECURITY_CHOICES, default="ssl")
    imap_username = models.CharField(max_length=255)
    imap_password_enc = models.TextField(blank=True)

    smtp_host = models.CharField(max_length=255)
    smtp_port = models.PositiveIntegerField(default=587)
    smtp_security = models.CharField(max_length=10, choices=SECURITY_CHOICES, default="starttls")
    smtp_username = models.CharField(max_length=255, blank=True, help_text="Leave blank to use the IMAP username")
    smtp_password_enc = models.TextField(blank=True)

    signature = models.TextField(blank=True, help_text="HTML signature appended to new messages")
    save_sent_copy = models.BooleanField(
        default=True,
        help_text="Upload a copy of sent mail to the Sent folder (not needed for Gmail, which does this itself)",
    )
    is_active = models.BooleanField(default=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["user", "email"], name="uniq_user_email")]

    def __str__(self):
        return self.email

    # Passwords are stored encrypted.
    @property
    def imap_password(self):
        return crypto.decrypt(self.imap_password_enc)

    @imap_password.setter
    def imap_password(self, value):
        self.imap_password_enc = crypto.encrypt(value)

    @property
    def smtp_password(self):
        return crypto.decrypt(self.smtp_password_enc) or self.imap_password

    @smtp_password.setter
    def smtp_password(self, value):
        self.smtp_password_enc = crypto.encrypt(value)

    @property
    def smtp_login(self):
        return self.smtp_username or self.imap_username

    @property
    def is_gmail(self):
        return "gmail" in self.imap_host.lower() or "googlemail" in self.imap_host.lower()

    @property
    def from_header(self):
        from email.utils import formataddr

        return formataddr((self.display_name, self.email)) if self.display_name else self.email

    def folder_by_role(self, role):
        return self.folders.filter(role=role).first()


class Folder(models.Model):
    ROLE_INBOX = "inbox"
    ROLES = [
        ("inbox", "Inbox"),
        ("sent", "Sent"),
        ("drafts", "Drafts"),
        ("trash", "Trash"),
        ("spam", "Spam"),
        ("archive", "Archive"),
        ("all", "All Mail"),
        ("flagged", "Starred"),
        ("important", "Important"),
        ("other", "Label"),
    ]
    # Folders that mirror other folders (Gmail) - not downloaded to avoid duplicates.
    VIRTUAL_ROLES = {"all", "flagged", "important"}
    # Bootstrap Icons names
    ROLE_ICONS = {
        "inbox": "inbox",
        "sent": "send",
        "drafts": "file-earmark",
        "trash": "trash",
        "spam": "exclamation-octagon",
        "archive": "archive",
        "all": "envelope",
        "flagged": "star",
        "important": "bookmark",
        "other": "tag",
    }

    account = models.ForeignKey(MailAccount, on_delete=models.CASCADE, related_name="folders")
    name = models.CharField(max_length=500, help_text="Raw IMAP mailbox name (modified UTF-7)")
    display_name = models.CharField(max_length=500)
    delimiter = models.CharField(max_length=5, default="/")
    role = models.CharField(max_length=20, choices=ROLES, default="other")
    selectable = models.BooleanField(default=True)
    sync_enabled = models.BooleanField(default=True)
    uidvalidity = models.BigIntegerField(null=True, blank=True)
    last_uid = models.BigIntegerField(default=0)
    unread_count = models.PositiveIntegerField(default=0)
    total_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["display_name"]
        constraints = [models.UniqueConstraint(fields=["account", "name"], name="uniq_account_folder")]

    def __str__(self):
        return self.display_name

    @property
    def icon(self):
        return self.ROLE_ICONS.get(self.role, "tag")

    @property
    def label(self):
        if self.role != "other":
            return dict(self.ROLES)[self.role]
        return self.display_name

    @property
    def short_label(self):
        return self.label.split(self.delimiter)[-1] if self.role == "other" and self.delimiter else self.label

    def get_absolute_url(self):
        if self.role in ("inbox", "sent", "drafts", "trash", "spam", "archive"):
            return reverse("folder_view", args=[self.role])
        return reverse("label_view", args=[self.pk])


class Message(models.Model):
    account = models.ForeignKey(MailAccount, on_delete=models.CASCADE, related_name="messages")
    folder = models.ForeignKey(Folder, on_delete=models.CASCADE, related_name="messages")
    uid = models.BigIntegerField(null=True, blank=True)

    message_id = models.CharField(max_length=998, blank=True, db_index=True)
    in_reply_to = models.CharField(max_length=998, blank=True)
    references = models.TextField(blank=True)
    thread_key = models.CharField(max_length=998, blank=True, db_index=True)

    subject = models.TextField(blank=True)
    from_name = models.CharField(max_length=500, blank=True)
    from_email = models.CharField(max_length=500, blank=True)
    to = models.TextField(blank=True)
    cc = models.TextField(blank=True)
    bcc = models.TextField(blank=True)
    reply_to = models.TextField(blank=True)
    date = models.DateTimeField(db_index=True)

    snippet = models.CharField(max_length=300, blank=True)
    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)

    is_read = models.BooleanField(default=False)
    is_starred = models.BooleanField(default=False)
    is_answered = models.BooleanField(default=False)
    is_draft = models.BooleanField(default=False)
    has_attachments = models.BooleanField(default=False)
    size = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]
        indexes = [
            models.Index(fields=["folder", "-date"]),
            models.Index(fields=["account", "thread_key"]),
            models.Index(fields=["account", "is_starred"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["folder", "uid"], condition=models.Q(uid__isnull=False), name="uniq_folder_uid"
            )
        ]

    def __str__(self):
        return self.subject or "(no subject)"

    @property
    def sender_label(self):
        return self.from_name or self.from_email or "(unknown)"

    @property
    def avatar_letter(self):
        return (self.sender_label.strip()[:1] or "?").upper()

    @property
    def avatar_color(self):
        palette = ["#e8710a", "#1a73e8", "#188038", "#d93025", "#9334e6", "#00796b", "#c2185b", "#5f6368", "#f9ab00"]
        return palette[sum(map(ord, self.from_email or "x")) % len(palette)]


def attachment_path(instance, filename):
    return f"attachments/{instance.message.account_id}/{instance.message_id}/{filename}"


class Attachment(models.Model):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="attachments")
    filename = models.CharField(max_length=500)
    content_type = models.CharField(max_length=200, default="application/octet-stream")
    size = models.PositiveIntegerField(default=0)
    content_id = models.CharField(max_length=500, blank=True)
    is_inline = models.BooleanField(default=False)
    file = models.FileField(upload_to=attachment_path, max_length=500)

    def __str__(self):
        return self.filename

    @property
    def icon(self):
        ct = self.content_type
        if ct.startswith("image/"):
            return "file-earmark-image"
        if ct == "application/pdf":
            return "file-earmark-pdf"
        if "zip" in ct or "compressed" in ct:
            return "file-earmark-zip"
        if ct.startswith("video/"):
            return "file-earmark-play"
        if ct.startswith("audio/"):
            return "file-earmark-music"
        if "sheet" in ct or "excel" in ct or ct == "text/csv":
            return "file-earmark-spreadsheet"
        if "word" in ct:
            return "file-earmark-word"
        return "file-earmark-text"

    @property
    def extension(self):
        return os.path.splitext(self.filename)[1].lstrip(".").upper()[:4] or "FILE"
