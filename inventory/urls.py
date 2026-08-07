from django.urls import path

from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('receipts/new/', views.receipt_create, name='receipt_create'),
    path('shipments/new/', views.shipment_create, name='shipment_create'),
    path('adjustments/new/', views.adjustment_create, name='adjustment_create'),
    path('history/', views.movement_history, name='movement_history'),
    path('history/export/', views.movement_history_export, name='movement_history_export'),
]
