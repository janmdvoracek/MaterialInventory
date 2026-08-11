from django.urls import path

from . import views

urlpatterns = [
    path('new/', views.transform_create, name='transform_create'),
    path('machines/', views.machine_dashboard, name='machine_dashboard'),
    path('machines/history/', views.machine_usage_history, name='machine_usage_history'),
    path('hours/', views.time_worked, name='time_worked'),
]
