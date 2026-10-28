from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from . import provision
from .models import AuditLog, LinuxAccount

LINUX_ON = {
    "ENABLED": True, "SSH_HOST": "server.test", "SSH_PORT": 22, "SSH_USER": "mailboxadm",
    "SSH_KEY_FILE": "/tmp/key", "SSH_KEY_PASSPHRASE": "", "SSH_PASSWORD": "", "SSH_KNOWN_HOSTS": "",
    "SSH_HOST_FINGERPRINT": "SHA256:x", "HELPER": "/usr/local/sbin/mailbox-user", "USE_SUDO": True,
    "DEFAULT_SHELL": "/usr/sbin/nologin", "DEFAULT_GROUPS": "", "TIMEOUT": 5,
}


class FakeServer:
    """Stands in for provision.Server and records what would run on the Ubuntu server."""

    users = {}
    calls = []
    fail_on = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _maybe_fail(self, op):
        FakeServer.calls.append(op)
        if FakeServer.fail_on == op:
            raise provision.ProvisionError(f"{op} refused by server")

    def exists(self, name):
        self._maybe_fail("exists")
        return FakeServer.users.get(name)

    def create(self, name, password, shell=None, groups="", comment=""):
        provision.validate_name(name)
        self._maybe_fail("create")
        FakeServer.users[name] = 2000 + len(FakeServer.users)
        return FakeServer.users[name]

    def set_password(self, name, pw):
        self._maybe_fail("passwd")

    def lock(self, name):
        self._maybe_fail("lock")

    def unlock(self, name):
        self._maybe_fail("unlock")

    def delete(self, name, remove_home=False):
        self._maybe_fail("delete")
        FakeServer.users.pop(name, None)
        return f"deleted {name}"


@override_settings(LINUX_USERS=LINUX_ON)
@mock.patch("panel.views.provision.Server", FakeServer)
class PanelTests(TestCase):
    def setUp(self):
        FakeServer.users, FakeServer.calls, FakeServer.fail_on = {}, [], None
        self.admin = User.objects.create_user("boss@x.com", email="boss@x.com", password="Boss-pass-123", is_staff=True)
        self.client.force_login(self.admin)

    def create(self, **extra):
        data = {"email": "priya@easybillo.in", "first_name": "Priya", "password1": "Priya-pass-2026",
                "password2": "Priya-pass-2026", "is_active": "on", "linux_create": "on",
                "linux_username": "priya", "linux_shell": "/usr/sbin/nologin"}
        data.update(extra)
        return self.client.post("/manage/users/new/", data)

    def test_non_staff_cannot_open_panel(self):
        joe = User.objects.create_user("joe@x.com", email="joe@x.com", password="Joe-pass-123")
        self.client.force_login(joe)
        self.assertEqual(self.client.get("/manage/").status_code, 302)
        self.assertEqual(self.client.post("/manage/users/new/", {}).status_code, 302)

    def test_create_with_linux_user(self):
        r = self.create()
        self.assertEqual(r.status_code, 302)
        u = User.objects.get(email="priya@easybillo.in")
        self.assertTrue(u.check_password("Priya-pass-2026"))
        self.assertEqual(u.linux_account.username, "priya")
        self.assertIn("priya", FakeServer.users)
        self.assertTrue(AuditLog.objects.filter(action="create user", ok=True).exists())

    def test_linux_name_suggested_from_email(self):
        self.create(email="Priya.Sharma@EasyBillo.in", linux_username="")
        u = User.objects.get(email="priya.sharma@easybillo.in")
        self.assertEqual(u.username, "priya.sharma@easybillo.in")
        self.assertEqual(u.linux_account.username, "priyasharma")

    def test_duplicate_email_rejected(self):
        self.create()
        r = self.create(email="PRIYA@easybillo.in", linux_username="priya2")
        self.assertContains(r, "already exists")
        self.assertEqual(User.objects.filter(email__iexact="priya@easybillo.in").count(), 1)

    def test_create_without_checkbox_does_not_touch_server(self):
        self.create(linux_create="")
        self.assertTrue(User.objects.filter(email="priya@easybillo.in").exists())
        self.assertEqual(FakeServer.calls, [])
        self.assertFalse(LinuxAccount.objects.exists())

    def test_server_refusal_creates_nothing(self):
        FakeServer.fail_on = "create"
        r = self.create()
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Nothing was created")
        self.assertFalse(User.objects.filter(email="priya@easybillo.in").exists())

    def test_failure_after_create_cleans_up_server(self):
        self.create(is_active="")          # inactive -> create then lock
        FakeServer.users.clear(); FakeServer.calls.clear()
        User.objects.all().exclude(pk=self.admin.pk).delete()
        FakeServer.fail_on = "lock"
        self.create(is_active="")
        self.assertFalse(User.objects.filter(email="priya@easybillo.in").exists())
        self.assertNotIn("priya", FakeServer.users)       # rolled back on the server too
        self.assertIn("delete", FakeServer.calls)

    def test_existing_linux_user_is_refused(self):
        FakeServer.users["priya"] = 1500
        r = self.create()
        self.assertContains(r, "already exists on the server")
        self.assertFalse(User.objects.filter(email="priya@easybillo.in").exists())

    def test_reserved_and_invalid_names_rejected(self):
        for bad in ["root", "x;id", "Upper", "../etc"]:
            r = self.create(email=f"u{len(bad)}@x.com", linux_username=bad)
            self.assertEqual(r.status_code, 200, bad)
        self.assertEqual(FakeServer.calls, [])

    def test_deactivate_locks_and_activate_unlocks(self):
        self.create()
        u = User.objects.get(email="priya@easybillo.in")
        self.client.post(f"/manage/users/{u.pk}/toggle/")
        u.refresh_from_db()
        self.assertFalse(u.is_active)
        self.assertEqual(u.linux_account.status, LinuxAccount.STATUS_LOCKED)
        self.client.post(f"/manage/users/{u.pk}/toggle/")
        u.linux_account.refresh_from_db()
        self.assertEqual(u.linux_account.status, LinuxAccount.STATUS_ACTIVE)

    def test_password_not_changed_when_server_refuses(self):
        self.create()
        u = User.objects.get(email="priya@easybillo.in")
        FakeServer.fail_on = "passwd"
        self.client.post(f"/manage/users/{u.pk}/password/",
                         {"pw-password1": "Brand-new-pass-9", "pw-password2": "Brand-new-pass-9", "pw-also_linux": "on"})
        u.refresh_from_db()
        self.assertTrue(u.check_password("Priya-pass-2026"))

    def test_delete_removes_linux_user(self):
        self.create()
        u = User.objects.get(email="priya@easybillo.in")
        self.client.post(f"/manage/users/{u.pk}/delete/", {"del-also_linux": "on"})
        self.assertFalse(User.objects.filter(email="priya@easybillo.in").exists())
        self.assertNotIn("priya", FakeServer.users)

    def test_cannot_delete_or_deactivate_self(self):
        self.client.post(f"/manage/users/{self.admin.pk}/delete/")
        self.client.post(f"/manage/users/{self.admin.pk}/toggle/")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)


class ValidationTests(TestCase):
    def test_names(self):
        for ok in ["priya", "ravi_k", "_svc", "a-1"]:
            provision.validate_name(ok)
        for bad in ["", "root", "Priya", "1abc", "a b", "x;id", "a" * 33, "dovecot"]:
            with self.assertRaises(provision.ProvisionError):
                provision.validate_name(bad)

    def test_groups(self):
        self.assertEqual(provision.validate_groups(" mail, users "), "mail,users")
        for bad in ["sudo", "docker", "mail;id"]:
            with self.assertRaises(provision.ProvisionError):
                provision.validate_groups(bad)

    def test_passwords(self):
        for bad in ["", "a:b", "line\nbreak"]:
            with self.assertRaises(provision.ProvisionError):
                provision.validate_password(bad)


class PartialPageTests(TestCase):
    def test_partial_url_redirects_for_browsers(self):
        from mail.models import Folder, MailAccount
        u = User.objects.create_user("m@x.com", email="m@x.com", password="M-pass-12345")
        acc = MailAccount.objects.create(user=u, email="m@x.com", imap_host="h", imap_username="m", smtp_host="h")
        Folder.objects.create(account=acc, name="INBOX", display_name="INBOX", role="inbox")
        self.client.force_login(u)
        r = self.client.get("/mail/inbox/?partial=1&page=1")
        self.assertRedirects(r, "/mail/inbox/?page=1", fetch_redirect_response=False)
        r = self.client.get("/mail/inbox/?partial=1", HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "<html")
        self.assertEqual(r["Cache-Control"], "no-store")
        r = self.client.get("/message/1/?back=//evil.com/x")  # open-redirect guard (404: no message)
        self.assertIn(r.status_code, (302, 404))


class EmailLoginTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("info@easybillo.in", email="info@easybillo.in", password="Info-pass-2026")

    def login(self, ident, pw="Info-pass-2026"):
        return self.client.post("/login/", {"username": ident, "password": pw})

    def test_login_with_email_any_case(self):
        self.assertEqual(self.login("Info@EasyBillo.in").status_code, 302)

    def test_username_is_not_accepted(self):
        User.objects.create_user("oldstyle", email="old@x.com", password="Old-pass-2026")
        r = self.client.post("/login/", {"username": "oldstyle", "password": "Old-pass-2026"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Wrong email address or password")

    def test_wrong_password_and_inactive(self):
        self.assertContains(self.login("info@easybillo.in", "nope"), "Wrong email address or password")
        self.u.is_active = False
        self.u.save()
        self.assertEqual(self.login("info@easybillo.in").status_code, 200)

    def test_signup_by_email(self):
        r = self.client.post("/signup/", {"email": "New@EasyBillo.in", "first_name": "N", "last_name": "",
                                          "password1": "Signup-pass-2026", "password2": "Signup-pass-2026"})
        self.assertEqual(r.status_code, 302)
        u = User.objects.get(email="new@easybillo.in")
        self.assertEqual(u.username, "new@easybillo.in")
        r = self.client.post("/signup/", {"email": "new@easybillo.in", "password1": "Signup-pass-2026",
                                          "password2": "Signup-pass-2026"})
        self.assertContains(r, "already exists")

    def test_database_rejects_duplicate_email(self):
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user("dup", email="INFO@easybillo.in", password="x")

    def test_admin_site_login_by_email(self):
        User.objects.create_superuser("root@easybillo.in", email="root@easybillo.in", password="Root-pass-2026")
        r = self.client.post("/admin/login/?next=/admin/", {"username": "root@easybillo.in", "password": "Root-pass-2026"})
        self.assertEqual(r.status_code, 302)


class EmailMigrationTests(TestCase):
    def test_existing_users_are_migrated(self):
        from importlib import import_module
        from django.apps import apps
        from django.db import connection
        mig = import_module("panel.migrations.0002_email_login")
        a = User.objects.create_user("legacy@Example.com", password="x")      # username is an address
        b = User.objects.create_user("bob", email="Bob@Example.com", password="x")
        with connection.schema_editor() as editor:
            mig.forwards(apps, editor)
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((a.email, a.username), ("legacy@example.com", "legacy@example.com"))
        self.assertEqual((b.email, b.username), ("bob@example.com", "bob@example.com"))


class EnsureAdminTests(TestCase):
    def test_creates_admin_once(self):
        from django.core.management import call_command
        with mock.patch.dict("os.environ", {"DJANGO_SUPERUSER_EMAIL": "Boss@EasyBillo.in", "DJANGO_SUPERUSER_PASSWORD": "Boss-pass-2026"}):
            call_command("ensure_admin")
            call_command("ensure_admin")
        u = User.objects.get(email="boss@easybillo.in")
        self.assertTrue(u.is_superuser and u.check_password("Boss-pass-2026"))
