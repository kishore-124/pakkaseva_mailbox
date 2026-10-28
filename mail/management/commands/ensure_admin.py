import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from mail.accounts import normalize_email


class Command(BaseCommand):
    help = "Create the first administrator from DJANGO_SUPERUSER_EMAIL / DJANGO_SUPERUSER_PASSWORD (if missing)."

    def handle(self, *args, **opts):
        email = normalize_email(os.environ.get("DJANGO_SUPERUSER_EMAIL"))
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
        if not email or not password:
            return
        User = get_user_model()
        if User.objects.filter(email__iexact=email).exists():
            return
        User.objects.create_superuser(username=email, email=email, password=password)
        self.stdout.write(f"Created administrator {email} - sign in with this email address.")
