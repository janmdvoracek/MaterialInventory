from django.urls import path

from . import views

urlpatterns = [
    # Zpracování is the landing page — it is what a worker opens the app to do,
    # and it is where LOGIN_REDIRECT_URL and the header logo point.
    path('', views.transform_create, name='transform_create'),
    path('machines/', views.machine_dashboard, name='machine_dashboard'),
    path('materials/', views.material_dashboard, name='material_dashboard'),
    path('hours/', views.time_worked, name='time_worked'),
    # Manager review of recorded jobs. Every view behind these is gated by
    # role_required, not just hidden from the nav.
    path('jobs/', views.job_dashboard, name='job_dashboard'),
    path('jobs/<int:pk>/', views.job_detail, name='job_detail'),
    path('jobs/<int:pk>/upravit/', views.job_edit, name='job_edit'),
    path('jobs/<int:pk>/schvalit/', views.job_approve, name='job_approve'),
    path('jobs/<int:pk>/smazat/', views.job_delete, name='job_delete'),
]
