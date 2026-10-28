from django.conf import settings
from django.db import models


class LinuxAccount(models.Model):
    """A Linux user on the Ubuntu server that belongs to a Mailbox login."""

    STATUS_ACTIVE = "active"
    STATUS_LOCKED = "locked"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_LOCKED, "Locked"),
        (STATUS_FAILED, "Failed"),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="linux_account")
    username = models.CharField("Linux user name", max_length=32, unique=True)
    shell = models.CharField(max_length=100, default="/usr/sbin/nologin")
    groups = models.CharField(max_length=200, blank=True)
    uid = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.username

    @property
    def can_login_shell(self):
        return not self.shell.endswith("nologin") and not self.shell.endswith("false")


class AuditLog(models.Model):
    """Every change made from the admin panel, including what the server answered."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")
    target = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=60)
    ok = models.BooleanField(default=True)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.target}"
