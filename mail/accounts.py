"""Sign-in by e-mail address.

Users sign in with their e-mail address and password. Django's built-in User model is kept;
the internal `username` is simply set to the (lower-cased) e-mail address, and e-mail addresses
are unique (case-insensitive, enforced by a database index).
"""
import re

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


def normalize_email(value):
    return (value or "").strip().lower()


def email_taken(email, exclude_pk=None):
    qs = get_user_model()._default_manager.filter(email__iexact=normalize_email(email))
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


def clean_unique_email(email, exclude_pk=None):
    email = normalize_email(email)
    if not email:
        raise forms.ValidationError("Enter an email address.")
    forms.EmailField().clean(email)
    if email_taken(email, exclude_pk):
        raise forms.ValidationError("A user with this email address already exists.")
    return email


def linux_name_from_email(email):
    """'Priya.Sharma@easybillo.in' -> 'priya.sharma' -> 'priyasharma' (valid Linux user name)."""
    local = normalize_email(email).split("@")[0]
    name = re.sub(r"[^a-z0-9_-]", "", local)
    name = re.sub(r"^[^a-z_]+", "", name)
    return name[:32]


class EmailBackend(ModelBackend):
    """Authenticate with e-mail + password. Usernames are not accepted."""

    def authenticate(self, request, username=None, password=None, email=None, **kwargs):
        identifier = normalize_email(email or username or kwargs.get(get_user_model().USERNAME_FIELD))
        if not identifier or "@" not in identifier or password is None:
            return None
        users = list(get_user_model()._default_manager.filter(email__iexact=identifier)[:2])
        if len(users) != 1:
            # Run the password hasher anyway so a missing account takes as long as a wrong password.
            get_user_model()().set_password(password)
            return None
        user = users[0]
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
