from django import forms
from django.conf import settings
from django.contrib.auth import password_validation
from django.contrib.auth.models import User

from mail.accounts import clean_unique_email, linux_name_from_email
from mail.forms import BootstrapMixin

from . import provision
from .models import LinuxAccount

SHELL_CHOICES = [
    ("/usr/sbin/nologin", "Mail only - no shell login (recommended)"),
    ("/bin/bash", "Bash - can log in over SSH"),
]


class LinuxFieldsMixin(forms.Form):
    linux_create = forms.BooleanField(
        label="Create this user on the Ubuntu server too", required=False,
        help_text="Creates a matching Linux account (with a home folder and mailbox) using the same password.",
    )
    linux_username = forms.CharField(
        label="Linux user name", required=False, max_length=32,
        help_text="Defaults to the part of the email before the @. Lowercase letters, digits, '_' and '-' only.",
    )
    linux_shell = forms.ChoiceField(label="Server access", choices=SHELL_CHOICES, required=False)
    linux_groups = forms.CharField(
        label="Extra groups", required=False,
        help_text="Optional, comma separated (e.g. mail). Admin groups like sudo are not allowed.",
    )

    def clean_linux(self, data, need_password):
        if not data.get("linux_create"):
            return data
        if not provision.is_enabled():
            self.add_error("linux_create", "Linux user management is not configured on this server (see Server page).")
            return data
        name = (data.get("linux_username") or "").strip() or linux_name_from_email(data.get("email", ""))
        try:
            data["linux_username"] = provision.validate_name(name)
        except provision.ProvisionError as exc:
            self.add_error("linux_username", str(exc))
        try:
            data["linux_groups"] = provision.validate_groups(data.get("linux_groups", ""))
        except provision.ProvisionError as exc:
            self.add_error("linux_groups", str(exc))
        taken = LinuxAccount.objects.filter(username=data.get("linux_username"))
        if getattr(self, "instance", None) is not None and self.instance.pk:
            taken = taken.exclude(user=self.instance)
        if taken.exists():
            self.add_error("linux_username", "Another user is already linked to this Linux name.")
        if need_password:
            try:
                provision.validate_password(data.get("password1") or data.get("password") or "")
            except provision.ProvisionError as exc:
                self.add_error("password1", str(exc))
        data["linux_shell"] = data.get("linux_shell") or settings.LINUX_USERS["DEFAULT_SHELL"]
        return data


class UserCreateForm(BootstrapMixin, LinuxFieldsMixin, forms.ModelForm):
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput(render_value=True), strip=False)
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput(render_value=True), strip=False)

    class Meta:
        model = User
        fields = ["email", "first_name", "last_name", "is_active", "is_staff"]
        labels = {"email": "Email address", "is_staff": "Administrator (can open this admin panel)",
                  "is_active": "Active (can sign in)"}
        help_texts = {"is_active": "", "is_staff": ""}
        widgets = {"email": forms.EmailInput(attrs={"autocomplete": "off"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["is_active"].initial = True
        self.fields["linux_create"].initial = provision.is_enabled()
        self.fields["linux_groups"].initial = settings.LINUX_USERS["DEFAULT_GROUPS"]
        self.fields["linux_shell"].initial = settings.LINUX_USERS["DEFAULT_SHELL"]
        self.fields["email"].required = True
        self.fields["email"].help_text = "The user signs in with this address."

    def clean_email(self):
        return clean_unique_email(self.cleaned_data.get("email"))

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "The two passwords do not match.")
        if p1:
            try:
                password_validation.validate_password(p1, User(username=data.get("email", ""), email=data.get("email", "")))
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return self.clean_linux(data, need_password=True)

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = user.email  # internal only; sign-in is by email
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class UserEditForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ["email", "first_name", "last_name", "is_active", "is_staff"]
        labels = {"email": "Email address (used to sign in)", "is_staff": "Administrator (can open this admin panel)",
                  "is_active": "Active (can sign in)"}
        help_texts = {"is_active": "", "is_staff": ""}

    def __init__(self, *args, current_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_user = current_user
        self.fields["email"].required = True

    def clean_email(self):
        return clean_unique_email(self.cleaned_data.get("email"), exclude_pk=self.instance.pk)

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = user.email
        if commit:
            user.save()
        return user

    def clean(self):
        data = super().clean()
        if self.instance.pk == getattr(self.current_user, "pk", None):
            if not data.get("is_active"):
                self.add_error("is_active", "You cannot deactivate your own account.")
            if not data.get("is_staff"):
                self.add_error("is_staff", "You cannot remove your own administrator rights.")
        return data


class PasswordResetForm(BootstrapMixin, forms.Form):
    password1 = forms.CharField(label="New password", widget=forms.PasswordInput, strip=False)
    password2 = forms.CharField(label="Confirm new password", widget=forms.PasswordInput, strip=False)
    also_linux = forms.BooleanField(label="Also change the Linux password on the server", required=False, initial=True)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if not hasattr(user, "linux_account"):
            del self.fields["also_linux"]

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "The two passwords do not match.")
        if p1:
            try:
                password_validation.validate_password(p1, self.user)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
            if data.get("also_linux"):
                try:
                    provision.validate_password(p1)
                except provision.ProvisionError as exc:
                    self.add_error("password1", str(exc))
        return data


class LinuxLinkForm(BootstrapMixin, LinuxFieldsMixin, forms.Form):
    """Create (or link) a Linux user for an existing Mailbox login."""

    password1 = forms.CharField(
        label="Password for the Linux user", widget=forms.PasswordInput, strip=False, required=False,
        help_text="Required when creating a new Linux user. When linking an existing one, leave blank to keep its password.",
    )
    link_existing = forms.BooleanField(
        label="If this Linux user already exists, link it instead of failing", required=False,
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = user
        self.fields["linux_create"].initial = True
        self.fields["linux_create"].widget = forms.HiddenInput()
        self.fields["linux_username"].initial = linux_name_from_email(user.email) if user else ""
        self.fields["linux_groups"].initial = settings.LINUX_USERS["DEFAULT_GROUPS"]
        self.fields["linux_shell"].initial = settings.LINUX_USERS["DEFAULT_SHELL"]

    def clean(self):
        data = super().clean()
        data["linux_create"] = True
        data["email"] = self.instance.email
        return self.clean_linux(data, need_password=bool(data.get("password1")))


class DeleteUserForm(BootstrapMixin, forms.Form):
    also_linux = forms.BooleanField(label="Also delete the Linux user on the server", required=False, initial=True)
    remove_home = forms.BooleanField(label="Delete their home folder and stored mail on the server", required=False)
