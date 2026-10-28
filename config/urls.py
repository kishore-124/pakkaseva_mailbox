from django.contrib import admin
from django.urls import include, path

from mail.forms import AdminLoginForm

admin.site.login_form = AdminLoginForm
admin.site.site_header = "Administration"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("manage/", include("panel.urls")),
    path("", include("mail.urls")),
]
