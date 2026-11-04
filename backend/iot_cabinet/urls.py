from django.contrib import admin
from django.urls import path, include

admin.site.site_header = "Quản Trị Hệ Thống IoT Giám Sát Tủ Mạng"
admin.site.site_title = "IoT Cabinet Admin"
admin.site.index_title = "Bảng Điều Khiển Quản Trị Trung Tâm"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("cabinet.urls")),
]
