from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User

from .accounts import clean_unique_email, normalize_email
from .models import MailAccount


class BootstrapMixin:
    """Give every widget the right Bootstrap 5 class."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            w = field.widget
            if isinstance(w, forms.CheckboxInput):
                css = "form-check-input"
            elif isinstance(w, forms.Select):
                css = "form-select"
            else:
                css = "form-control"
            w.attrs["class"] = (w.attrs.get("class", "") + " " + css).strip()

    def is_valid(self):
        ok = super().is_valid()
        for name in self.errors:
            if name in self.fields:
                w = self.fields[name].widget
                w.attrs["class"] = w.attrs.get("class", "") + " is-invalid"
        return ok


class EmailLoginMixin:
    """Turn Django's 'username' login field into an e-mail field."""

    error_messages = {
        "invalid_login": "Wrong email address or password.",
        "inactive": "This account has been deactivated. Please contact your administrator.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        f = self.fields["username"]
        f.label = "Email address"
        f.max_length = 254
        f.widget = forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email", "inputmode": "email",
                                           "class": f.widget.attrs.get("class", "")})

    def clean_username(self):
        return normalize_email(self.cleaned_data.get("username"))


class LoginForm(EmailLoginMixin, BootstrapMixin, AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs["class"] = "form-control"
        for field in self.fields.values():  # needed by Bootstrap floating labels
            field.widget.attrs["placeholder"] = field.label


class AdminLoginForm(EmailLoginMixin, AdminAuthenticationForm):
    """Django admin (/admin/) login, also by e-mail."""


class SignupForm(BootstrapMixin, forms.ModelForm):
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
                                strip=False, help_text="At least 8 characters, not only numbers, not a common password.")
    password2 = forms.CharField(label="Confirm password", strip=False,
                                widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    class Meta:
        model = User
        fields = ("email", "first_name", "last_name")
        labels = {"email": "Email address"}
        widgets = {"email": forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True
        self.fields["email"].help_text = "You will sign in with this address."

    def clean_email(self):
        return clean_unique_email(self.cleaned_data.get("email"))

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "The two passwords do not match.")
        if p1 and data.get("email"):
            try:
                password_validation.validate_password(p1, User(username=data["email"], email=data["email"]))
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = user.email
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class MailAccountForm(BootstrapMixin, forms.ModelForm):
    imap_password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(render_value=False),
        required=False,
        help_text="For Gmail use an App Password (Google Account → Security → App passwords).",
    )
    smtp_password = forms.CharField(
        label="SMTP password",
        widget=forms.PasswordInput(render_value=False),
        required=False,
        help_text="Leave blank to use the same password as IMAP.",
    )

    class Meta:
        model = MailAccount
        fields = [
            "email", "display_name",
            "imap_host", "imap_port", "imap_security", "imap_username",
            "smtp_host", "smtp_port", "smtp_security", "smtp_username",
            "save_sent_copy", "signature", "is_active",
        ]
        widgets = {"signature": forms.Textarea(attrs={"rows": 3})}
        labels = {
            "email": "Email address", "display_name": "Your name",
            "imap_host": "IMAP server", "imap_port": "Port", "imap_security": "Security", "imap_username": "Username",
            "smtp_host": "SMTP server", "smtp_port": "Port", "smtp_security": "Security", "smtp_username": "SMTP username",
            "save_sent_copy": "Save a copy of sent mail in the Sent folder", "is_active": "Sync this account",
        }

    def clean(self):
        data = super().clean()
        if not self.instance.pk and not data.get("imap_password"):
            self.add_error("imap_password", "Password is required.")
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get("imap_password"):
            obj.imap_password = self.cleaned_data["imap_password"]
        if self.cleaned_data.get("smtp_password"):
            obj.smtp_password = self.cleaned_data["smtp_password"]
        if commit:
            obj.save()
        return obj
