from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('search/', views.search_query, name='search-query'),
    path('domains/', views.domains, name='domains'),
    path('models/', views.free_models, name='free-models'),
]
