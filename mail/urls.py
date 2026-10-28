from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import LoginForm

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", auth_views.LoginView.as_view(redirect_authenticated_user=True, authentication_form=LoginForm), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("signup/", views.signup, name="signup"),

    path("mail/label/<int:label_id>/", views.mailbox, name="label_view"),
    path("mail/<slug:view>/", views.mailbox, name="folder_view"),
    path("message/<int:pk>/", views.thread, name="thread"),
    path("message/<int:pk>/body/", views.message_body, name="message_body"),
    path("attachment/<int:pk>/", views.attachment, name="attachment"),

    path("api/actions/", views.actions, name="api_actions"),
    path("api/sync/", views.sync_now, name="api_sync"),
    path("api/counts/", views.counts, name="api_counts"),
    path("api/contacts/", views.contacts, name="api_contacts"),
    path("api/compose-data/", views.compose_data, name="api_compose_data"),
    path("api/send/", views.send, name="api_send"),
    path("api/draft/", views.save_draft, name="api_draft"),
    path("api/draft/<int:pk>/discard/", views.discard_draft, name="api_draft_discard"),

    path("settings/accounts/", views.account_list, name="account_list"),
    path("settings/accounts/add/", views.account_edit, name="account_add"),
    path("settings/accounts/<int:pk>/", views.account_edit, name="account_edit"),
    path("settings/accounts/<int:pk>/delete/", views.account_delete, name="account_delete"),
    path("settings/accounts/<int:pk>/switch/", views.account_switch, name="account_switch"),
    path("settings/folders/<int:pk>/toggle-sync/", views.folder_toggle_sync, name="folder_toggle_sync"),
]
