from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.google_login, name="google_login"),
    path("callback/", views.google_callback, name="google_callback"),
]
