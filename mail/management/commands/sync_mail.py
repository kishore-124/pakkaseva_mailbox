import logging
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from mail.imap import MailError, sync_account
from mail.models import MailAccount

log = logging.getLogger("mail.worker")


class Command(BaseCommand):
    help = "Fetch new mail over IMAP for every active account (use --loop for the background worker)."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Keep running and sync periodically")
        parser.add_argument("--interval", type=int, default=settings.MAILBOX["SYNC_INTERVAL"])
        parser.add_argument("--account", type=int, help="Only sync this account id")

    def handle(self, *args, **opts):
        self.running = True
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)
        while self.running:
            started = time.monotonic()
            self.sync_all(opts.get("account"))
            if not opts["loop"]:
                break
            # sleep in small steps so SIGTERM stops the worker quickly
            while self.running and time.monotonic() - started < opts["interval"]:
                time.sleep(1)

    def _stop(self, *_):
        self.running = False

    def sync_all(self, only=None):
        close_old_connections()
        qs = MailAccount.objects.filter(is_active=True)
        if only:
            qs = qs.filter(pk=only)
        for account in qs:
            if not self.running:
                return
            try:
                new = sync_account(account)
                if new is None:
                    log.info("%s: sync already running, skipped", account)
                elif new:
                    log.info("%s: %d new message(s)", account, new)
            except MailError as exc:
                log.warning("%s: %s", account, exc)
            except Exception:
                log.exception("%s: unexpected sync error", account)
