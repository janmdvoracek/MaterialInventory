from django.urls import path

from . import views

urlpatterns = [
    # Zpracování is the landing page — it is what a worker opens the app to do,
    # and it is where LOGIN_REDIRECT_URL and the header logo point.
    path('', views.transform_create, name='transform_create'),
    path('machines/', views.machine_dashboard, name='machine_dashboard'),
    path('machines/history/', views.machine_usage_history, name='machine_usage_history'),
    path('hours/', views.time_worked, name='time_worked'),
]
