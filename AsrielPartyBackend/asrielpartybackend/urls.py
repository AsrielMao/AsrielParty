"""
URL configuration for asrielpartybackend project.

业务接口都挂在 party/urls.py 里，这里只做总装。
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('party.urls')),
]

if settings.DEBUG:
    # 开发期由 Django 直接伺服上传的头像；正式环境交给 Nginx
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
