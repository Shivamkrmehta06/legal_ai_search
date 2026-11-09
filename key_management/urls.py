from django.urls import path

from . import views

urlpatterns = [
    path("keys/", views.key_list, name="key-list"),
]
