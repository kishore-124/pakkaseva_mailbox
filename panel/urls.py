from django.urls import path

from . import views

urlpatterns = [
    path("", views.users, name="panel_users"),
    path("users/new/", views.user_create, name="panel_user_create"),
    path("users/<int:pk>/", views.user_detail, name="panel_user"),
    path("users/<int:pk>/password/", views.user_password, name="panel_user_password"),
    path("users/<int:pk>/toggle/", views.user_toggle, name="panel_user_toggle"),
    path("users/<int:pk>/delete/", views.user_delete, name="panel_user_delete"),
    path("users/<int:pk>/linux/link/", views.linux_link, name="panel_linux_link"),
    path("users/<int:pk>/linux/<str:action>/", views.linux_action, name="panel_linux_action"),
    path("server/", views.server, name="panel_server"),
    path("log/", views.logs, name="panel_logs"),
]
