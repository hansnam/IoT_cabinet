from django.urls import include, path
from rest_framework.routers import DefaultRouter
from apps.alerts.views import AlertViewSet

router = DefaultRouter()
router.register(r'', AlertViewSet, basename='alert')

urlpatterns = [
    path('', include(router.urls)),
]
