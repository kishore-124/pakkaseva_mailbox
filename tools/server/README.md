# Creating Linux users from the Mailbox admin panel

When an administrator ticks **"Create this user on the Ubuntu server too"**, the Mailbox app
connects to your Ubuntu server over SSH and creates a matching Linux user with the same password.
Locking, unlocking, password changes and deletion from the admin panel are applied on the server too.

## How it stays safe

* The app never runs `useradd`, `chpasswd` or a shell as root. It logs in as a normal user
  (`mailboxadm`) that may run **only one command** through sudo: `/usr/local/sbin/mailbox-user`.
* That helper script only creates ordinary users and only changes users with UID ≥ 1000. It refuses
  `root`, system accounts (`daemon`, `dovecot`, `postfix`, …), admin groups (`sudo`, `adm`,
  `docker`, …) and shells that are not listed in `/etc/shells`.
* Passwords are sent on the SSH channel's standard input, never on a command line, so they do not
  appear in `ps` or in logs.
* User names are checked by the app **and** again by the helper on the server.
* With `LINUX_SSH_HOST_FINGERPRINT` set, the app refuses to talk to any machine that is not your server.
* Every action is written to the **Activity log** in the admin panel.

## Setup (once)

### 1. On the Ubuntu server

Copy `mailbox-user` from this folder to the server, then:

```bash
# a dedicated user for the app (no password login)
sudo adduser --disabled-password --gecos "" mailboxadm

# install the helper, owned by root
sudo install -o root -g root -m 0755 mailbox-user /usr/local/sbin/mailbox-user

# allow mailboxadm to run ONLY the helper as root
echo 'mailboxadm ALL=(root) NOPASSWD: /usr/local/sbin/mailbox-user' | sudo tee /etc/sudoers.d/mailbox-user
sudo chmod 0440 /etc/sudoers.d/mailbox-user
sudo visudo -c                              # must say "parsed OK"

# test it
sudo -u mailboxadm sudo -n /usr/local/sbin/mailbox-user ping

# the server's identity, needed in step 3
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

### 2. On the machine that runs Docker (in the project folder)

```bash
mkdir -p ssh
ssh-keygen -t ed25519 -N "" -f ssh/id_ed25519 -C mailbox-app
cat ssh/id_ed25519.pub
```

Add that public key line to `/home/mailboxadm/.ssh/authorized_keys` on the server:

```bash
sudo -u mailboxadm mkdir -p -m 700 /home/mailboxadm/.ssh
echo 'ssh-ed25519 AAAA... mailbox-app' | sudo -u mailboxadm tee -a /home/mailboxadm/.ssh/authorized_keys
sudo chmod 600 /home/mailboxadm/.ssh/authorized_keys
```

### 3. In `.env`

```ini
LINUX_USERS_ENABLED=True
LINUX_SSH_HOST=66.116.244.109        # your server; use host.docker.internal if it is the Docker host itself
LINUX_SSH_PORT=22
LINUX_SSH_USER=mailboxadm
LINUX_SSH_KEY_FILE=/app/ssh/id_ed25519
LINUX_SSH_HOST_FINGERPRINT=SHA256:…  # from step 1
LINUX_DEFAULT_SHELL=/usr/sbin/nologin   # mail-only users; /bin/bash allows SSH logins
LINUX_DEFAULT_GROUPS=                   # optional, e.g. mail
```

Then `docker compose up -d` and open **Admin panel → Ubuntu server → Test connection**.

## Helper commands (for reference)

```
mailbox-user ping
mailbox-user exists  <name>
mailbox-user create  <name> [--shell SHELL] [--groups g1,g2] [--comment TEXT]   < password
mailbox-user passwd  <name>                                                     < password
mailbox-user lock    <name>
mailbox-user unlock  <name>
mailbox-user delete  <name> [--remove-home]
```

## Troubleshooting

| Message in the panel | Fix |
|---|---|
| `sudo: a password is required` | The sudoers line from step 1 is missing or has a typo (`sudo visudo -c`). |
| `… is /usr/local/sbin/mailbox-user installed on the server?` | Install the helper (step 1). |
| `SSH login … failed` | Public key not in `authorized_keys`, wrong `LINUX_SSH_USER`, or the key file is not mounted at `/app/ssh/`. |
| `host key does not match` | Wrong fingerprint, or you are not talking to your server. Re-check step 1. |
| `Cannot reach …:22` | Firewall, wrong IP/port, or SSH not running on the server. |
