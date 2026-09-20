from django.urls import path

from . import reports, views

urlpatterns = [
    # Zpracování is the landing page — it is what a worker opens the app to do,
    # and it is where LOGIN_REDIRECT_URL and the header logo point.
    path('', views.transform_create, name='transform_create'),
    path('machines/', reports.machine_dashboard, name='machine_dashboard'),
    path('materials/', reports.material_dashboard, name='material_dashboard'),
    path('hours/', reports.time_worked, name='time_worked'),
    # The summary table of each of those three as a CSV. Same querystring as the
    # page, so a download is whatever was on screen; each is gated exactly like
    # the page it belongs to.
    path('machines/export/', reports.machine_dashboard_export, name='machine_dashboard_export'),
    path('materials/export/', reports.material_dashboard_export, name='material_dashboard_export'),
    path('hours/export/', reports.time_worked_export, name='time_worked_export'),
    # The one row-level download: Materiál's line items, the whole filtered set
    # rather than the page on screen.
    path('materials/detail/export/', reports.material_detail_export, name='material_detail_export'),
    # Manager review of recorded jobs. Every view behind these is gated by
    # role_required, not just hidden from the nav.
    path('jobs/', views.job_dashboard, name='job_dashboard'),
    path('jobs/<int:pk>/', views.job_detail, name='job_detail'),
    path('jobs/<int:pk>/upravit/', views.job_edit, name='job_edit'),
    path('jobs/<int:pk>/schvalit/', views.job_approve, name='job_approve'),
    path('jobs/<int:pk>/smazat/', views.job_delete, name='job_delete'),
]
