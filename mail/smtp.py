"""Building and sending outgoing mail with smtplib."""
import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from django.conf import settings

from .imap import MailError
from .parsing import html_to_text, split_addresses


def build_message(account, *, to, cc="", bcc="", subject="", html_body="", attachments=(), in_reply_to="", references=""):
    msg = EmailMessage()
    msg["From"] = account.from_header
    if to:
        msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc  # stripped by smtplib.send_message, kept in the Sent copy
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    domain = account.email.rsplit("@", 1)[-1] if "@" in account.email else None
    msg["Message-ID"] = make_msgid(domain=domain)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = (references + " " + in_reply_to).strip() if in_reply_to not in references else references
    msg["X-Mailer"] = "Mailbox (Django)"

    text = html_to_text(html_body) or " "
    msg.set_content(text)
    if html_body:
        msg.add_alternative(
            f'<!doctype html><html><body><div style="font-family:Arial,Helvetica,sans-serif;font-size:14px">'
            f"{html_body}</div></body></html>",
            subtype="html",
        )
    for name, ctype, data in attachments:
        ctype = ctype or mimetypes.guess_type(name)[0] or "application/octet-stream"
        maintype, _, subtype = ctype.partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=name)
    return msg


def recipients(to, cc, bcc):
    seen, out = set(), []
    for addr in split_addresses(to) + split_addresses(cc) + split_addresses(bcc):
        if addr.lower() not in seen:
            seen.add(addr.lower())
            out.append(addr)
    return out


def _open(account):
    timeout = settings.MAILBOX["IMAP_TIMEOUT"]
    ctx = ssl.create_default_context()
    if account.smtp_security == "ssl":
        server = smtplib.SMTP_SSL(account.smtp_host, account.smtp_port, context=ctx, timeout=timeout)
    else:
        server = smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=timeout)
        server.ehlo()
        if account.smtp_security == "starttls":
            server.starttls(context=ctx)
            server.ehlo()
    try:
        if account.smtp_login and account.smtp_password and server.has_extn("auth"):
            server.login(account.smtp_login, account.smtp_password)
    except Exception:
        server.close()
        raise
    return server


def send(account, msg, rcpts):
    if not rcpts:
        raise MailError("Please specify at least one recipient.")
    try:
        with _open(account) as server:
            refused = server.send_message(msg, from_addr=account.email, to_addrs=rcpts)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError("SMTP login failed. Check the username/password (Gmail needs an App Password).") from exc
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise MailError(f"Sending failed: {exc}") from exc
    if refused:
        raise MailError("Some recipients were refused: " + ", ".join(refused))


def test_smtp(account):
    try:
        with _open(account):
            pass
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise MailError(f"SMTP connection failed: {exc}") from exc
