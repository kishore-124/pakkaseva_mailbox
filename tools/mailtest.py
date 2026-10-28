#!/usr/bin/env python3
"""
mailtest.py - test an IMAP/SMTP mail account from the command line.

Only uses the Python standard library (Python 3.8+). Each step is checked
separately (DNS -> TCP -> TLS certificate -> login -> folders / send) so you can
see exactly where a connection fails.

Examples
--------
  # test IMAP (lists folders and the latest 5 messages in INBOX)
  python mailtest.py imap --host mail.example.com --user info@example.com

  # test SMTP login only
  python mailtest.py smtp --host mail.example.com --port 587 --security starttls --user info@example.com

  # check SMTP authentication in detail: methods offered before/after TLS,
  # try PLAIN, LOGIN and CRAM-MD5 one by one, confirm unauthenticated sending is refused
  python mailtest.py smtp-auth --host mail.example.com --user info@example.com --check-relay

  # SMTP test using one specific login method
  python mailtest.py smtp --host mail.example.com --user info@example.com --auth LOGIN

  # test SMTP and send a real test e-mail
  python mailtest.py smtp --host mail.example.com --user info@example.com --to you@gmail.com

  # test both with the same host and credentials
  python mailtest.py all --host mail.example.com --user info@example.com --to you@gmail.com

  # server only has a self-signed certificate / you must use the IP address
  python mailtest.py imap --host 66.116.244.109 --user info@example.com --insecure

The password is asked for interactively, or read from --password or the
MAIL_PASSWORD environment variable.
"""
import argparse
import getpass
import imaplib
import os
import re
import smtplib
import socket
import ssl
import sys
import time
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesHeaderParser
from email.utils import formatdate, make_msgid

USE_COLOR = sys.stdout.isatty() and os.name != "nt"


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def ok(msg):
    print(_c("32", "  [OK]   ") + msg)


def fail(msg):
    print(_c("31", "  [FAIL] ") + msg)


def info(msg):
    print("         " + msg)


def hint(msg):
    print(_c("33", "  [HINT] ") + msg)


def step(title):
    print("\n" + _c("1", f"== {title}"))


class Abort(Exception):
    pass


# ---------------------------------------------------------------- common network checks

def check_dns(host):
    step(f"DNS lookup: {host}")
    try:
        addrs = sorted({ai[4][0] for ai in socket.getaddrinfo(host, None)})
    except socket.gaierror as exc:
        fail(f"cannot resolve host name: {exc}")
        hint("Check the spelling of the server name. It must be a host name such as "
             "mail.example.com, not an e-mail address.")
        raise Abort
    ok("resolves to " + ", ".join(addrs))


def check_tcp(host, port, timeout):
    step(f"TCP connection to {host}:{port}")
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except socket.timeout:
        fail(f"timed out after {timeout}s")
        hint("The port is probably blocked by a firewall (yours, your ISP's, your cloud "
             "provider's, or the mail server's), or the server does not listen on this port.")
        raise Abort
    except OSError as exc:
        fail(f"cannot connect: {exc}")
        hint("Connection refused = nothing listens on that port. Check the port number "
             "(IMAP 993 SSL / 143 STARTTLS, SMTP 465 SSL / 587 STARTTLS / 25).")
        raise Abort
    ok(f"port is open ({(time.monotonic() - start) * 1000:.0f} ms)")


def ssl_context(insecure):
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def describe_cert(host, port, timeout, starttls=None):
    """Fetch the server certificate WITHOUT verifying it and print who it is issued to."""
    step("TLS certificate")
    raw_ctx = ssl.create_default_context()
    raw_ctx.check_hostname = False
    raw_ctx.verify_mode = ssl.CERT_NONE
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        if starttls:
            starttls(sock)
        with raw_ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
            version, cipher = tls.version(), tls.cipher()[0]
    except Exception as exc:  # noqa: BLE001
        fail(f"TLS handshake failed: {exc}")
        hint("Wrong security mode? Port 993/465 use SSL/TLS, ports 143/587 use STARTTLS.")
        raise Abort
    info(f"protocol {version}, cipher {cipher}")

    # Decode the certificate with the ssl module's test helper (stdlib, no extra packages).
    names, subject, issuer, not_after = [], "", "", ""
    try:
        import tempfile
        pem = ssl.DER_cert_to_PEM_cert(der)
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as fh:
            fh.write(pem)
            path = fh.name
        try:
            decoded = ssl._ssl._test_decode_cert(path)  # pylint: disable=protected-access
        finally:
            os.unlink(path)
        subject = ", ".join(f"{k}={v}" for rdn in decoded.get("subject", ()) for k, v in rdn)
        issuer = ", ".join(f"{k}={v}" for rdn in decoded.get("issuer", ()) for k, v in rdn)
        names = [v for k, v in decoded.get("subjectAltName", ()) if k in ("DNS", "IP Address")]
        not_after = decoded.get("notAfter", "")
    except Exception:  # noqa: BLE001
        pass
    if subject:
        info(f"subject : {subject}")
    if issuer:
        info(f"issuer  : {issuer}")
    if names:
        info("valid for: " + ", ".join(names))
    if not_after:
        info(f"expires : {not_after}")
    if names and host.lower() not in [n.lower() for n in names] and not any(
            n.startswith("*.") and host.lower().endswith(n[1:].lower()) for n in names):
        hint(f"'{host}' is not on this certificate. Use one of: " + ", ".join(names[:5]))

    # Now verify properly, the same way the Mailbox app does.
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        if starttls:
            starttls(sock)
        with ssl.create_default_context().wrap_socket(sock, server_hostname=host):
            pass
        ok("certificate is trusted and matches the host name")
        return True
    except ssl.SSLCertVerificationError as exc:
        fail(f"certificate is NOT accepted: {exc.verify_message}")
        if "mismatch" in str(exc).lower() or "doesn't match" in str(exc).lower():
            if names:
                hint(f"Connect using one of these names instead of '{host}': " + ", ".join(names[:5]))
            else:
                hint("Connect using the host name the certificate was issued for.")
        elif "self" in str(exc).lower():
            hint("The server uses a self-signed certificate. Ask your host for a real one "
                 "(e.g. Let's Encrypt), or test with --insecure.")
        elif "expired" in str(exc).lower():
            hint("The server's certificate has expired - your hosting provider must renew it.")
        return False
    except Exception as exc:  # noqa: BLE001
        fail(f"verification failed: {exc}")
        return False


# ---------------------------------------------------------------- IMAP

def imap_starttls_probe(sock):
    """Speak just enough IMAP to switch a raw socket to TLS (for certificate inspection)."""
    f = sock.makefile("rb")
    f.readline()  # greeting
    sock.sendall(b"a1 STARTTLS\r\n")
    line = f.readline()
    while line.startswith(b"*"):
        line = f.readline()
    if not line.startswith(b"a1 OK"):
        raise ssl.SSLError("server refused STARTTLS: " + line.decode(errors="replace").strip())


def imap_utf7(name):
    """Decode IMAP modified UTF-7 folder names (e.g. 'Entw&APw-rfe' -> 'Entwürfe')."""
    import base64

    def repl(m):
        if not m.group(1):
            return "&"
        b64 = m.group(1).replace(",", "/")
        try:
            return base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-16-be")
        except Exception:  # noqa: BLE001
            return m.group(0)
    return re.sub(r"&([A-Za-z0-9+,]*)-", repl, name)


def decode(value):
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:  # noqa: BLE001
        return value or ""


def test_imap(a, password):
    print(_c("1;36", f"\n######## IMAP test: {a.user} @ {a.host}:{a.port} ({a.security})"))
    check_dns(a.host)
    check_tcp(a.host, a.port, a.timeout)
    if a.security != "none":
        trusted = describe_cert(a.host, a.port, a.timeout,
                                imap_starttls_probe if a.security == "starttls" else None)
        if not trusted and not a.insecure:
            hint("Re-run with --insecure to continue the test without certificate checks.")
            raise Abort

    step("IMAP login")
    ctx = ssl_context(a.insecure)
    try:
        if a.security == "ssl":
            conn = imaplib.IMAP4_SSL(a.host, a.port, ssl_context=ctx, timeout=a.timeout)
        else:
            conn = imaplib.IMAP4(a.host, a.port, timeout=a.timeout)
            if a.security == "starttls":
                conn.starttls(ssl_context=ctx)
    except Exception as exc:  # noqa: BLE001
        fail(f"could not open IMAP session: {exc}")
        raise Abort
    info("greeting: " + conn.welcome.decode(errors="replace")[:120])
    info("capabilities: " + " ".join(conn.capabilities))
    try:
        conn._encoding = "utf-8"  # allow non-ASCII passwords
        conn.login(a.user, password)
    except imaplib.IMAP4.error as exc:
        fail("login rejected: " + (exc.args[0].decode() if isinstance(exc.args[0], bytes) else str(exc)))
        hint("Check username (usually the full e-mail address) and password. "
             "Gmail / Yahoo / Outlook need an App Password.")
        raise Abort
    ok(f"logged in as {a.user}")

    step("Folders")
    typ, data = conn.list()
    folders = []
    for line in data or []:
        if not line:
            continue
        text = line.decode(errors="replace") if isinstance(line, bytes) else str(line[0])
        m = re.match(r'\((?P<flags>[^)]*)\) (?P<d>"[^"]*"|NIL) (?P<name>.+)$', text)
        if m:
            name = m.group("name").strip().strip('"')
            folders.append(name)
            flags = m.group("flags")
            special = " ".join(f for f in flags.split() if f.lower() in (
                "\\sent", "\\drafts", "\\trash", "\\junk", "\\archive", "\\all", "\\flagged"))
            info(f"{imap_utf7(name):<35} {special}")
    ok(f"{len(folders)} folder(s)")

    step("INBOX")
    typ, data = conn.select("INBOX", readonly=True)
    if typ != "OK":
        fail("cannot open INBOX: " + str(data))
        raise Abort
    total = int(data[0] or 0)
    _, unseen = conn.uid("SEARCH", None, "UNSEEN")
    ok(f"{total} message(s), {len((unseen[0] or b'').split())} unread")
    if total and a.show:
        _, uids = conn.uid("SEARCH", None, "ALL")
        last = (uids[0] or b"").split()[-a.show:]
        _, data = conn.uid("FETCH", b",".join(last).decode(),
                           "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        print()
        for item in reversed([d for d in data if isinstance(d, tuple)]):
            h = BytesHeaderParser().parsebytes(item[1])
            info(f"{decode(h.get('Date', ''))[:31]:<32} {decode(h.get('From', ''))[:30]:<31} {decode(h.get('Subject', ''))[:60]}")
    conn.logout()
    print(_c("32;1", "\nIMAP test passed."))


# ---------------------------------------------------------------- SMTP

def smtp_starttls_probe(sock):
    f = sock.makefile("rb")

    def read_reply():
        line = f.readline()
        while line[3:4] == b"-":
            line = f.readline()
        return line

    read_reply()  # 220 greeting
    sock.sendall(b"EHLO mailtest\r\n")
    read_reply()
    sock.sendall(b"STARTTLS\r\n")
    line = read_reply()
    if not line.startswith(b"220"):
        raise ssl.SSLError("server refused STARTTLS: " + line.decode(errors="replace").strip())


def test_smtp(a, password):
    print(_c("1;36", f"\n######## SMTP test: {a.user} @ {a.host}:{a.port} ({a.security})"))
    check_dns(a.host)
    check_tcp(a.host, a.port, a.timeout)
    if a.security != "none":
        trusted = describe_cert(a.host, a.port, a.timeout,
                                smtp_starttls_probe if a.security == "starttls" else None)
        if not trusted and not a.insecure:
            hint("Re-run with --insecure to continue the test without certificate checks.")
            raise Abort

    step("SMTP session")
    ctx = ssl_context(a.insecure)
    try:
        if a.security == "ssl":
            server = smtplib.SMTP_SSL(a.host, a.port, context=ctx, timeout=a.timeout)
        else:
            server = smtplib.SMTP(a.host, a.port, timeout=a.timeout)
        if a.debug:
            server.set_debuglevel(1)
        code, msg = server.ehlo()
        info(f"EHLO -> {code}")
        if a.security == "starttls":
            if not server.has_extn("starttls"):
                fail("server does not offer STARTTLS on this port")
                raise Abort
            server.starttls(context=ctx)
            server.ehlo()
            ok("STARTTLS upgrade done")
        exts = sorted(server.esmtp_features)
        info("extensions: " + ", ".join(exts))
        if "size" in server.esmtp_features:
            try:
                info(f"max message size: {int(server.esmtp_features['size']) / 1048576:.1f} MB")
            except ValueError:
                pass
    except Abort:
        raise
    except Exception as exc:  # noqa: BLE001
        fail(f"could not open SMTP session: {exc}")
        raise Abort

    step("SMTP login")
    if not server.has_extn("auth"):
        fail("server offers no AUTH on this connection")
        hint("Many servers only allow login after STARTTLS or on port 465/587.")
        raise Abort
    info("auth methods: " + server.esmtp_features.get("auth", ""))
    try:
        mech = getattr(a, "auth", None)
        if mech:
            info(f"forcing AUTH {mech.upper()}")
            if mech.upper() not in server.esmtp_features.get("auth", "").upper().split():
                fail(f"server does not offer AUTH {mech.upper()}")
                hint("Pick one of the methods listed above, or leave out --auth.")
                raise Abort
            server.user, server.password = a.user, password
            server.auth(mech.upper(), getattr(server, AUTH_METHODS[mech.upper()]), initial_response_ok=True)
        else:
            server.login(a.user, password)
    except smtplib.SMTPNotSupportedError:
        fail(f"server does not support AUTH {mech.upper()}")
        raise Abort
    except smtplib.SMTPAuthenticationError as exc:
        fail(f"login rejected: {exc.smtp_code} {exc.smtp_error.decode(errors='replace')}")
        hint("Check username (usually the full e-mail address) and password.")
        raise Abort
    ok(f"logged in as {a.user}")

    if a.to:
        step(f"Sending test message to {a.to}")
        sender = a.sender or a.user
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = a.to
        msg["Subject"] = f"Mailbox test message {time.strftime('%Y-%m-%d %H:%M:%S')}"
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1] if "@" in sender else None)
        msg.set_content(
            f"This is a test message sent by mailtest.py\n\n"
            f"SMTP server: {a.host}:{a.port} ({a.security})\nLogin: {a.user}\n"
        )
        try:
            refused = server.send_message(msg, from_addr=sender, to_addrs=[a.to])
        except smtplib.SMTPException as exc:
            fail(f"sending failed: {exc}")
            hint("'Sender address rejected' = the From address must belong to the logged-in account.")
            raise Abort
        if refused:
            fail(f"recipient refused: {refused}")
            raise Abort
        ok(f"message accepted by the server (Message-ID {msg['Message-ID']})")
        info("If it does not arrive, check the recipient's Spam folder and your domain's SPF/DKIM records.")
    else:
        info("(no --to given, so no message was sent)")
    server.quit()
    print(_c("32;1", "\nSMTP test passed."))


AUTH_METHODS = {"PLAIN": "auth_plain", "LOGIN": "auth_login", "CRAM-MD5": "auth_cram_md5"}


def smtp_open(host, port, security, ctx, timeout, debug=False):
    """Return (server, auth_before_tls). auth_before_tls is the AUTH line seen before STARTTLS."""
    if security == "ssl":
        server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=timeout)
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)
    if debug:
        server.set_debuglevel(1)
    server.ehlo()
    before = server.esmtp_features.get("auth") if server.has_extn("auth") else None
    if security == "starttls":
        if not server.has_extn("starttls"):
            server.close()
            raise smtplib.SMTPNotSupportedError("server does not offer STARTTLS on this port")
        server.starttls(context=ctx)
        server.ehlo()
    return server, before


def test_smtp_auth(a, password):
    print(_c("1;36", f"\n######## SMTP AUTH test: {a.user} @ {a.host}:{a.port} ({a.security})"))
    check_dns(a.host)
    check_tcp(a.host, a.port, a.timeout)
    if a.security != "none":
        trusted = describe_cert(a.host, a.port, a.timeout,
                                smtp_starttls_probe if a.security == "starttls" else None)
        if not trusted and not a.insecure:
            hint("Re-run with --insecure to continue the test without certificate checks.")
            raise Abort
    ctx = ssl_context(a.insecure)

    # --- what the server advertises
    step("Advertised authentication methods")
    try:
        server, before = smtp_open(a.host, a.port, a.security, ctx, a.timeout, a.debug)
    except Exception as exc:  # noqa: BLE001
        fail(f"could not open SMTP session: {exc}")
        raise Abort
    after = server.esmtp_features.get("auth") if server.has_extn("auth") else None
    server.quit()
    if a.security == "starttls":
        info(f"before STARTTLS: {before.strip() if before else '(none)'}")
        info(f"after  STARTTLS: {after.strip() if after else '(none)'}")
        if before:
            hint("Server offers AUTH before encryption - passwords could be sent in clear text.")
    elif a.security == "none":
        info(f"without encryption: {after.strip() if after else '(none)'}")
        if after:
            hint("Login over an unencrypted connection sends your password in clear text.")
    else:
        info(f"over SSL/TLS: {after.strip() if after else '(none)'}")
    if not after:
        fail("server offers no authentication on this connection")
        hint("Try port 587 with --security starttls, or port 465 with --security ssl. "
             "Port 25 is usually for server-to-server mail and often has no AUTH.")
        raise Abort
    offered = [m.upper() for m in after.split()]
    ok("offered: " + ", ".join(offered))

    # --- try every mechanism separately (fresh connection each time)
    step("Login with each method")
    wanted = [m.upper() for m in a.methods] if a.methods else list(AUTH_METHODS)
    results = {}
    for mech in wanted:
        if mech not in AUTH_METHODS:
            info(f"{mech:<9} skipped (not supported by this script)")
            continue
        if mech not in offered:
            info(f"{mech:<9} not offered by server")
            continue
        try:
            server, _ = smtp_open(a.host, a.port, a.security, ctx, a.timeout, a.debug)
            server.user, server.password = a.user, password
            code, resp = server.auth(mech, getattr(server, AUTH_METHODS[mech]), initial_response_ok=True)
            results[mech] = True
            ok(f"{mech:<9} accepted ({code} {resp.decode(errors='replace')[:60]})")
            server.quit()
        except smtplib.SMTPAuthenticationError as exc:
            results[mech] = False
            fail(f"{mech:<9} rejected ({exc.smtp_code} {exc.smtp_error.decode(errors='replace')[:80]})")
        except Exception as exc:  # noqa: BLE001
            results[mech] = False
            fail(f"{mech:<9} error: {exc}")
    for extra in offered:
        if extra not in AUTH_METHODS:
            info(f"{extra:<9} offered, not tested (needs OAuth or another client)")

    # --- does the server refuse to send without login?
    relay_open = False
    if a.check_relay:
        step("Sending without login (should be refused)")
        rcpt = a.relay_to or "relay-test@example.com"
        try:
            server, _ = smtp_open(a.host, a.port, a.security, ctx, a.timeout, a.debug)
            code, resp = server.mail(a.user)
            if code >= 400:
                ok(f"MAIL FROM refused without login ({code}) - good")
            else:
                code, resp = server.rcpt(rcpt)
                if code >= 400:
                    ok(f"recipient {rcpt} refused without login ({code} {resp.decode(errors='replace')[:60]}) - good")
                else:
                    relay_open = True
                    fail(f"server accepted {rcpt} WITHOUT login ({code}) - it may be an open relay")
                    hint("Ask your mail host to require SMTP authentication for outgoing mail.")
            server.rset()
            server.quit()
        except smtplib.SMTPException as exc:
            ok(f"refused without login ({exc}) - good")
        except Exception as exc:  # noqa: BLE001
            info(f"could not run this check: {exc}")

    if not results:
        fail("none of the requested methods could be tested")
        raise Abort
    if not any(results.values()):
        hint("All methods rejected: check the username (usually the full e-mail address) and the "
             "password. Gmail/Yahoo/Outlook need an App Password; some hosts lock the account "
             "after several failed logins.")
        raise Abort
    good = [m for m, v in results.items() if v]
    if relay_open:
        info(f"Login works ({', '.join(good)}), but the server also accepts mail without login.")
        raise Abort
    print(_c("32;1", f"\nSMTP AUTH test passed ({', '.join(good)})."))


# ---------------------------------------------------------------- CLI

def main():
    p = argparse.ArgumentParser(
        description="Test IMAP and SMTP mail server settings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples", 1)[1] if "Examples" in __doc__ else "",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, port, security):
        sp.add_argument("--host", required=True, help="server host name, e.g. mail.example.com")
        sp.add_argument("--port", type=int, default=port, help=f"port (default {port})")
        sp.add_argument("--security", choices=["ssl", "starttls", "none"], default=security,
                        help=f"connection security (default {security})")
        sp.add_argument("--user", required=True, help="login, usually the full e-mail address")
        sp.add_argument("--password", help="password (default: $MAIL_PASSWORD or prompt)")
        sp.add_argument("--insecure", action="store_true", help="do not verify the TLS certificate")
        sp.add_argument("--timeout", type=int, default=20, help="network timeout in seconds")

    imap_p = sub.add_parser("imap", help="test incoming mail (IMAP)")
    common(imap_p, 993, "ssl")
    imap_p.add_argument("--show", type=int, default=5, help="list the newest N INBOX messages (default 5)")

    smtp_p = sub.add_parser("smtp", help="test outgoing mail (SMTP)")
    common(smtp_p, 587, "starttls")
    smtp_p.add_argument("--to", help="send a test e-mail to this address")
    smtp_p.add_argument("--from", dest="sender", help="From address (default: --user)")
    smtp_p.add_argument("--debug", action="store_true", help="print the raw SMTP conversation")
    smtp_p.add_argument("--auth", type=str.upper, choices=list(AUTH_METHODS),
                        help="force one login method (default: automatic)")

    auth_p = sub.add_parser("smtp-auth", help="check SMTP authentication methods in detail")
    common(auth_p, 587, "starttls")
    auth_p.add_argument("--methods", nargs="+", metavar="METHOD",
                        help="methods to try: PLAIN LOGIN CRAM-MD5 (default: all offered)")
    auth_p.add_argument("--check-relay", action="store_true",
                        help="also check that sending WITHOUT login is refused")
    auth_p.add_argument("--relay-to", metavar="EMAIL",
                        help="outside address used by --check-relay (default relay-test@example.com)")
    auth_p.add_argument("--debug", action="store_true", help="print the raw SMTP conversation")

    all_p = sub.add_parser("all", help="test IMAP and SMTP on the same host")
    all_p.add_argument("--host", required=True)
    all_p.add_argument("--smtp-host", help="SMTP host if different from --host")
    all_p.add_argument("--user", required=True)
    all_p.add_argument("--password")
    all_p.add_argument("--imap-port", type=int, default=993)
    all_p.add_argument("--imap-security", choices=["ssl", "starttls", "none"], default="ssl")
    all_p.add_argument("--smtp-port", type=int, default=587)
    all_p.add_argument("--smtp-security", choices=["ssl", "starttls", "none"], default="starttls")
    all_p.add_argument("--to", help="send a test e-mail to this address")
    all_p.add_argument("--from", dest="sender")
    all_p.add_argument("--insecure", action="store_true")
    all_p.add_argument("--timeout", type=int, default=20)
    all_p.add_argument("--show", type=int, default=5)
    all_p.add_argument("--debug", action="store_true")

    a = p.parse_args()
    password = a.password or os.environ.get("MAIL_PASSWORD") or getpass.getpass(f"Password for {a.user}: ")

    failed = False
    try:
        if a.cmd == "imap":
            test_imap(a, password)
        elif a.cmd == "smtp":
            test_smtp(a, password)
        elif a.cmd == "smtp-auth":
            test_smtp_auth(a, password)
        else:
            imap_args = argparse.Namespace(host=a.host, port=a.imap_port, security=a.imap_security, user=a.user,
                                           insecure=a.insecure, timeout=a.timeout, show=a.show)
            smtp_args = argparse.Namespace(host=a.smtp_host or a.host, port=a.smtp_port, security=a.smtp_security,
                                           user=a.user, insecure=a.insecure, timeout=a.timeout, to=a.to,
                                           sender=a.sender, debug=a.debug, auth=None)
            for fn, args in ((test_imap, imap_args), (test_smtp, smtp_args)):
                try:
                    fn(args, password)
                except Abort:
                    failed = True
    except Abort:
        failed = True
    except KeyboardInterrupt:
        print("\ncancelled")
        return 130
    if failed:
        print(_c("31;1", "\nTest FAILED - see the messages above."))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
