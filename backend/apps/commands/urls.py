from django.urls import include, path
from rest_framework.routers import DefaultRouter
from apps.commands.views import CommandViewSet

router = DefaultRouter()
router.register(r'', CommandViewSet, basename='command')

urlpatterns = [
    path('', include(router.urls)),
]
