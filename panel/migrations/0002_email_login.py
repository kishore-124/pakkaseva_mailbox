"""Switch to signing in by e-mail address.

* Users with no e-mail whose username already is an address get it as their e-mail.
* All e-mail addresses are lower-cased, and the internal username is set to the e-mail.
* A unique (case-insensitive) index on the e-mail column makes every address belong to one user.
"""
from django.db import migrations
from django.db.models import Count
from django.db.models.functions import Lower

INDEX = "auth_user_email_lower_uniq"


def forwards(apps, schema_editor):
    User = apps.get_model("auth", "User")
    for u in User.objects.all():
        email = (u.email or "").strip().lower()
        if not email and "@" in u.username:
            email = u.username.strip().lower()
        changed = email != u.email
        u.email = email
        if email and u.username != email and not User.objects.filter(username=email).exclude(pk=u.pk).exists():
            u.username = email
            changed = True
        if changed:
            u.save(update_fields=["email", "username"])

    dupes = (User.objects.exclude(email="").annotate(e=Lower("email")).values("e")
             .annotate(n=Count("id")).filter(n__gt=1).values_list("e", flat=True))
    if dupes:
        print(f"\n  WARNING: these e-mail addresses are used by more than one user, so the unique "
              f"index was NOT created: {', '.join(dupes)}. Give each user a different address in the "
              f"admin panel, then run: python manage.py migrate panel 0001 && python manage.py migrate")
        return
    schema_editor.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX} ON auth_user (LOWER(email)) WHERE email <> ''"
    )


def backwards(apps, schema_editor):
    schema_editor.execute(f"DROP INDEX IF EXISTS {INDEX}")


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("panel", "0001_initial"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
