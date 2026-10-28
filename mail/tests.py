from django.contrib.auth.models import User
from django.test import TestCase

from .models import Folder, MailAccount, Message
from .parsing import imap_utf7_decode, imap_utf7_encode, normalize_subject, parse_message
from .sanitize import clean_html
from .search import apply_search

RAW = (
    b"From: =?utf-8?q?Bob_B=C3=BCilder?= <bob@example.com>\r\n"
    b"To: Alice <alice@example.com>\r\n"
    b"Subject: =?utf-8?b?Q2Fmw6kgbWVudQ==?=\r\n"
    b"Message-ID: <abc@example.com>\r\n"
    b"Date: Tue, 27 Oct 2026 10:00:00 +0530\r\n"
    b"MIME-Version: 1.0\r\n"
    b"Content-Type: multipart/mixed; boundary=XX\r\n\r\n"
    b"--XX\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>Hello <b>there</b></p><script>x()</script>\r\n"
    b"--XX\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=\"a.pdf\"\r\n"
    b"Content-Transfer-Encoding: base64\r\n\r\nJVBERi0=\r\n--XX--\r\n"
)


class ParsingTests(TestCase):
    def test_parse_message(self):
        p = parse_message(RAW)
        self.assertEqual(p["subject"], "Café menu")
        self.assertEqual(p["from_name"], "Bob Büilder")
        self.assertEqual(p["message_id"], "<abc@example.com>")
        self.assertEqual(len(p["attachments"]), 1)
        self.assertEqual(p["attachments"][0]["filename"], "a.pdf")
        self.assertIn("Hello there", p["snippet"])

    def test_utf7_roundtrip(self):
        for name in ["INBOX", "Clients Ünï", "Entwürfe", "A&B", "日本語"]:
            self.assertEqual(imap_utf7_decode(imap_utf7_encode(name)), name)
        self.assertEqual(imap_utf7_encode("A&B"), "A&-B")

    def test_sanitize(self):
        out = clean_html('<p onclick="x">hi</p><script>bad()</script><a href="javascript:x">l</a>')
        self.assertNotIn("script", out)
        self.assertNotIn("onclick", out)
        self.assertNotIn("javascript", out)

    def test_normalize_subject(self):
        self.assertEqual(normalize_subject("Re: Fwd: RE: Hello"), "Hello")


class SearchTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("u", password="p")
        self.acc = MailAccount.objects.create(user=user, email="a@x.com", imap_host="h", imap_username="a", smtp_host="h")
        inbox = Folder.objects.create(account=self.acc, name="INBOX", display_name="INBOX", role="inbox")
        from django.utils import timezone
        Message.objects.create(account=self.acc, folder=inbox, uid=1, subject="Invoice March", from_email="bob@x.com",
                               date=timezone.now(), has_attachments=True)
        Message.objects.create(account=self.acc, folder=inbox, uid=2, subject="Lunch", from_email="carol@x.com",
                               date=timezone.now(), is_read=True)

    def test_operators(self):
        qs = Message.objects.filter(account=self.acc)
        self.assertEqual(apply_search(qs, self.acc, "from:bob")[0].count(), 1)
        self.assertEqual(apply_search(qs, self.acc, "has:attachment")[0].count(), 1)
        self.assertEqual(apply_search(qs, self.acc, "is:unread")[0].count(), 1)
        self.assertEqual(apply_search(qs, self.acc, "lunch")[0].count(), 1)
        self.assertTrue(apply_search(qs, self.acc, "in:inbox")[1])
