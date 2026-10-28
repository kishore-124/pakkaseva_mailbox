"""Manage Linux users on the Ubuntu server over SSH.

Nothing is run as root directly: the app only calls the restricted helper script
(tools/server/mailbox-user) through sudo. Passwords travel over the SSH channel's
standard input, never on a command line, so they do not show up in `ps` or logs.
"""
import base64
import hashlib
import logging
import re
import shlex

from django.conf import settings

log = logging.getLogger("panel.provision")

NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
GROUP_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
RESERVED = {
    "root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news", "uucp", "proxy",
    "www-data", "backup", "list", "irc", "gnats", "nobody", "systemd-network", "systemd-resolve",
    "systemd-timesync", "messagebus", "syslog", "_apt", "sshd", "dovecot", "dovenull", "postfix",
    "ubuntu", "admin",
}
FORBIDDEN_GROUPS = {"sudo", "adm", "root", "wheel", "shadow", "disk", "admin", "lxd", "docker"}


class ProvisionError(Exception):
    pass


def cfg():
    return settings.LINUX_USERS


def is_enabled():
    c = cfg()
    return bool(c["ENABLED"] and c["SSH_HOST"] and c["SSH_USER"] and (c["SSH_KEY_FILE"] or c["SSH_PASSWORD"]))


def host_key_checked():
    c = cfg()
    return bool(c["SSH_KNOWN_HOSTS"] or c["SSH_HOST_FINGERPRINT"])


def validate_name(name):
    if not NAME_RE.match(name or ""):
        raise ProvisionError(
            "Linux user names must start with a lowercase letter or '_' and contain only lowercase "
            "letters, digits, '_' or '-' (max 32 characters)."
        )
    if name in RESERVED:
        raise ProvisionError(f"'{name}' is a reserved system account name.")
    return name


def validate_groups(groups):
    out = []
    for g in [x.strip() for x in (groups or "").split(",") if x.strip()]:
        if not GROUP_RE.match(g):
            raise ProvisionError(f"Invalid group name '{g}'.")
        if g in FORBIDDEN_GROUPS:
            raise ProvisionError(f"Adding users to the '{g}' group is not allowed from the web panel.")
        out.append(g)
    return ",".join(out)


def validate_password(pw):
    if not pw:
        raise ProvisionError("A password is required for the Linux user.")
    if ":" in pw or "\n" in pw or "\r" in pw:
        raise ProvisionError("The Linux password must not contain ':' or line breaks.")
    if len(pw) > 256:
        raise ProvisionError("The password is too long.")
    return pw


class _FingerprintPolicy:
    """paramiko host key policy that accepts exactly one SHA256 fingerprint."""

    def __init__(self, expected):
        self.expected = expected.strip().removeprefix("SHA256:").rstrip("=")

    def missing_host_key(self, client, hostname, key):
        got = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if got != self.expected:
            raise ProvisionError(
                f"The server's SSH host key does not match LINUX_SSH_HOST_FINGERPRINT "
                f"(server sent SHA256:{got}). Refusing to connect."
            )


def fingerprint_of(key):
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


class Server:
    """One SSH connection; use as a context manager."""

    def __init__(self):
        if not is_enabled():
            raise ProvisionError(
                "Linux user management is not configured. Set LINUX_USERS_ENABLED, LINUX_SSH_HOST, "
                "LINUX_SSH_USER and LINUX_SSH_KEY_FILE (or LINUX_SSH_PASSWORD) in .env."
            )
        self.client = None
        self.host_fingerprint = ""

    def __enter__(self):
        import paramiko

        c = cfg()
        client = paramiko.SSHClient()
        if c["SSH_KNOWN_HOSTS"]:
            client.load_host_keys(c["SSH_KNOWN_HOSTS"])
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        elif c["SSH_HOST_FINGERPRINT"]:
            client.set_missing_host_key_policy(_FingerprintPolicy(c["SSH_HOST_FINGERPRINT"]))
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = dict(
            hostname=c["SSH_HOST"], port=c["SSH_PORT"], username=c["SSH_USER"], timeout=c["TIMEOUT"],
            banner_timeout=c["TIMEOUT"], auth_timeout=c["TIMEOUT"], allow_agent=False, look_for_keys=False,
        )
        if c["SSH_KEY_FILE"]:
            kwargs["key_filename"] = c["SSH_KEY_FILE"]
            if c["SSH_KEY_PASSPHRASE"]:
                kwargs["passphrase"] = c["SSH_KEY_PASSPHRASE"]
        if c["SSH_PASSWORD"]:
            kwargs["password"] = c["SSH_PASSWORD"]
        try:
            client.connect(**kwargs)
        except ProvisionError:
            raise
        except paramiko.AuthenticationException as exc:
            raise ProvisionError(f"SSH login to {c['SSH_USER']}@{c['SSH_HOST']} failed: {exc}") from exc
        except paramiko.BadHostKeyException as exc:
            raise ProvisionError(f"SSH host key mismatch for {c['SSH_HOST']} - possible attack, refusing: {exc}") from exc
        except paramiko.SSHException as exc:
            raise ProvisionError(f"SSH error: {exc}") from exc
        except OSError as exc:
            raise ProvisionError(f"Cannot reach {c['SSH_HOST']}:{c['SSH_PORT']}: {exc}") from exc
        self.client = client
        key = client.get_transport().get_remote_server_key()
        self.host_fingerprint = fingerprint_of(key)
        return self

    def __exit__(self, *exc):
        if self.client:
            self.client.close()

    # -- low level
    def helper(self, *args, stdin=None):
        c = cfg()
        cmd = " ".join(shlex.quote(str(a)) for a in [c["HELPER"], *args])
        if c["USE_SUDO"]:
            cmd = "sudo -n " + cmd
        try:
            chan_in, chan_out, chan_err = self.client.exec_command(cmd, timeout=c["TIMEOUT"])
            if stdin is not None:
                chan_in.write(stdin + "\n")
                chan_in.flush()
            chan_in.channel.shutdown_write()
            out = chan_out.read().decode(errors="replace").strip()
            err = chan_err.read().decode(errors="replace").strip()
            code = chan_out.channel.recv_exit_status()
        except Exception as exc:  # noqa: BLE001
            raise ProvisionError(f"Running the helper on the server failed: {exc}") from exc
        if code != 0:
            msg = err or out or f"exit code {code}"
            if "sudo:" in msg and ("password" in msg or "not allowed" in msg or "a terminal" in msg):
                msg += (" - the SSH user is not allowed to run the helper with sudo. "
                        "See tools/server/README.md for the sudoers line.")
            elif code == 127 or "No such file" in msg or "command not found" in msg:
                msg += f" - is {c['HELPER']} installed on the server?"
            raise ProvisionError(msg.removeprefix("ERROR: "))
        log.info("server: %s -> %s", args[0], out)
        return out

    # -- operations
    def ping(self):
        return self.helper("ping")

    def exists(self, name):
        validate_name(name)
        out = self.helper("exists", name)
        if out.startswith("yes"):
            m = re.search(r"uid=(\d+)", out)
            return int(m.group(1)) if m else -1
        return None

    def create(self, name, password, shell=None, groups="", comment=""):
        validate_name(name)
        validate_password(password)
        args = ["create", name, "--shell", shell or cfg()["DEFAULT_SHELL"]]
        groups = validate_groups(groups)
        if groups:
            args += ["--groups", groups]
        comment = re.sub(r"[^\w .@-]", "", comment or "")[:80]
        if comment:
            args += ["--comment", comment]
        out = self.helper(*args, stdin=password)
        m = re.search(r"uid=(\d+)", out)
        return int(m.group(1)) if m else None

    def set_password(self, name, password):
        validate_name(name)
        validate_password(password)
        return self.helper("passwd", name, stdin=password)

    def lock(self, name):
        validate_name(name)
        return self.helper("lock", name)

    def unlock(self, name):
        validate_name(name)
        return self.helper("unlock", name)

    def delete(self, name, remove_home=False):
        validate_name(name)
        args = ["delete", name] + (["--remove-home"] if remove_home else [])
        return self.helper(*args)
