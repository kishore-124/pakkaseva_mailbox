# Mailbox — a Gmail-style web mail client (Django 6.1 + PostgreSQL)

A self-hosted mailbox app that connects to any IMAP/SMTP account (Gmail, Outlook, Yahoo, Zoho,
your own server), downloads mail into PostgreSQL and gives you a Gmail-like interface to read,
search, organise and send mail.

**Stack:** Python 3.13 · Django 6.1.2 · PostgreSQL 18 · Bootstrap 5.3.8 + Bootstrap Icons 1.13 · Python's `imaplib` / `smtplib` · Gunicorn · WhiteNoise · Docker Compose

## Features

- **Gmail-style UI built with Bootstrap 5.3** (navbar, offcanvas sidebar on mobile, dropdowns,
  toasts, modals, list groups, cards, form controls) plus **light / dark / auto theme**: sidebar (Inbox, Starred, Sent, Drafts, All Mail, Archive, Spam, Trash, Labels),
  pill search bar, conversation list with hover actions, unread bold rows, stars, pagination,
  collapsible sidebar, mobile layout.
- **Conversations**: messages are threaded via `Message-ID` / `In-Reply-To` / `References`;
  the thread view collapses read messages and expands unread + latest, like Gmail.
- **IMAP sync** (incremental, UID-based, handles UIDVALIDITY): new mail, read/star flag changes and
  deletions are synced both ways. A background **worker** container polls every 60 s; the refresh
  button syncs immediately.
- **Actions pushed to the server**: read/unread, star, archive, delete → Trash, delete forever,
  report spam / not spam, move to any folder (uses IMAP `MOVE`, falls back to `COPY`+`EXPUNGE`).
- **Compose** window (bottom-right, minimise / full screen): rich text formatting, Cc/Bcc,
  contact autocomplete, attachments (drag & drop), reply / reply-all / forward with quoted text and
  original attachments, signature, **Undo Send** (5 s), drafts auto-saved to the IMAP Drafts folder.
- **Sent mail** is stored in your Sent folder (Gmail does this itself, so it is skipped there).
- **Search** with Gmail operators: `from:` `to:` `subject:` `is:unread` `is:starred` `has:attachment`
  `in:sent` `label:Work` `after:2026/01/01` `before:2026/06/30`, quoted phrases and `-negation`,
  plus an advanced-search panel.
- **Safe HTML rendering**: e-mail HTML is sanitised (nh3) and shown in a sandboxed iframe with a
  strict CSP; inline `cid:` images and attachment previews work.
- **Multiple accounts** per login, account switcher, per-folder sync toggle.
- Mail passwords are **encrypted at rest** (Fernet).
- Keyboard shortcuts (press `?` in the app for the full list): `j`/`k` move, `o` open, `x` select,
  `s` star, `c` compose, `/` search, `r`/`a`/`f` reply / reply all / forward, `e` archive, `#` delete,
  `u` back, `g` then `i`/`s`/`t`/`d` go to Inbox/Starred/Sent/Drafts, `Ctrl+Enter` send.

- **Quick settings** (gear icon): light / dark / auto theme, list density (default, comfortable
  with sender avatars, compact) and message previews on/off. Attachment chips under messages,
  Gmail-style select menu (All / None / Read / Unread / Starred / Unstarred), loading bar.
- **Admin panel** (`/manage/`, staff users only): list, search, add, edit, activate/deactivate,
  reset password and delete users, plus an activity log of every change.
  **Tick "Create this user on the Ubuntu server too"** and the user is also created as a Linux user
  on your server over SSH, with the same password. Deactivating locks the Linux user, password
  resets and deletion are applied on the server as well. Setup: `tools/server/README.md`.

## Run it with Docker

```bash
cp .env.example .env          # then edit DJANGO_SECRET_KEY and the passwords
docker compose up -d --build
```

Open http://localhost:8000 and **sign in with your email address** (the administrator is
`DJANGO_SUPERUSER_EMAIL` / `DJANGO_SUPERUSER_PASSWORD` from `.env`, or click **Create account**),
then add your mail account. The first sync downloads the newest 500 messages per folder
(`MAILBOX_INITIAL_SYNC_LIMIT`).

Services:

| service  | what it does                                              |
|----------|-----------------------------------------------------------|
| `db`     | PostgreSQL 18 (data in the `pgdata` volume)               |
| `web`    | Django + Gunicorn on port 8000, runs migrations on start  |
| `worker` | `python manage.py sync_mail --loop` — background IMAP sync |

Attachments are stored in the `media` volume.

Useful commands:

```bash
docker compose logs -f worker                       # watch mail sync
docker compose exec web python manage.py sync_mail  # sync all accounts once
docker compose exec web python manage.py createsuperuser
docker compose down                                 # stop (data is kept in volumes)
```

## Signing in

Everyone signs in with **email address + password** (capitals don't matter); usernames are not
accepted, including on Django's own `/admin/` page. Each email address can belong to only one user.
When upgrading, existing users keep working: their email becomes their sign-in. A user with no
email address cannot sign in until an administrator adds one in the admin panel.

## Connecting Gmail

1. Turn on 2-Step Verification for your Google account.
2. Create an **App Password** at https://myaccount.google.com/apppasswords.
3. In Mailbox → *Add account* click **Gmail** (fills `imap.gmail.com:993 SSL` and
   `smtp.gmail.com:587 STARTTLS`) and paste the app password.

Gmail's virtual folders *All Mail*, *Starred* and *Important* are not downloaded (they duplicate
other folders); the **Starred** and **All Mail** views are built from the synced folders instead.
Archiving moves a conversation out of the Inbox, exactly like Gmail.

Other presets: Outlook/Office 365, Yahoo (app password), Zoho. Any IMAP/SMTP server works.

## Configuration (`.env`)

| variable | default | meaning |
|---|---|---|
| `DJANGO_SECRET_KEY` | — | **required**, long random string |
| `FIELD_ENCRYPTION_KEY` | derived from secret key | Fernet key for stored mail passwords |
| `DJANGO_ALLOWED_HOSTS` / `DJANGO_CSRF_TRUSTED_ORIGINS` | localhost | add your domain when deploying |
| `DJANGO_SECURE_COOKIES` | `False` | set `True` behind HTTPS |
| `TIME_ZONE` | `Asia/Kolkata` | display time zone |
| `MAILBOX_ALLOW_SIGNUP` | `True` | allow new logins to register |
| `MAILBOX_SYNC_INTERVAL` | `60` | worker poll interval (seconds) |
| `MAILBOX_INITIAL_SYNC_LIMIT` | `500` | newest messages per folder on first sync |
| `MAILBOX_PAGE_SIZE` | `50` | conversations per page |
| `MAILBOX_MAX_ATTACHMENT_MB` | `25` | outgoing attachment limit |

If you change `DJANGO_SECRET_KEY` without setting `FIELD_ENCRYPTION_KEY`, saved mail passwords can
no longer be decrypted — re-enter them under *Settings → Accounts*.

For production put it behind a reverse proxy with HTTPS (nginx, Caddy, Traefik) and set
`DJANGO_SECURE_COOKIES=True`, `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS`.

## Local development without Docker

```bash
pip install -r requirements.txt
export POSTGRES_HOST=localhost DJANGO_DEBUG=True
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver          # terminal 1
python manage.py sync_mail --loop   # terminal 2
python manage.py test mail          # unit tests
```

## Project layout

```
config/               Django settings / urls / wsgi
mail/models.py        MailAccount, Folder, Message, Attachment
mail/imap.py          IMAP client: folder discovery, incremental sync, flag/move/delete/append
mail/smtp.py          building and sending messages
mail/parsing.py       MIME parsing, header decoding, IMAP modified UTF-7
mail/sanitize.py      HTML sanitising for display and sending
mail/search.py        Gmail-style search operators
mail/views.py         pages + JSON API used by the front-end
mail/management/commands/sync_mail.py   background worker
panel/                admin panel: users, Linux user provisioning over SSH (provision.py), activity log
tools/server/         mailbox-user helper script + setup guide for the Ubuntu server
tools/mailtest.py     command-line IMAP/SMTP tester
templates/            Gmail-style templates (Django 6 template partials)
mail/static/mail/     app.css (Gmail look on top of Bootstrap), app.js, Roboto font
mail/static/vendor/   Bootstrap 5.3.8 and Bootstrap Icons, bundled so the container works offline
docker/entrypoint.sh  waits for DB, migrates, starts gunicorn or the worker
```
