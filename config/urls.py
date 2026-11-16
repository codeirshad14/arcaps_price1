"""
Root URL configuration.

    /            → kiosk screen (the customer-facing page)
    /api/        → REST API
    /admin/      → Jazzmin admin
    /media/      → uploaded images (development only; see note below)
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from kiosk import pwa

urlpatterns = [
    # Ahead of admin.site.urls, whose catch-all would otherwise claim it.
    # admin_view is what makes it staff-only: anyone else is sent to sign in.
    path("admin/install-app/", admin.site.admin_view(pwa.install_app), name="install-app"),
    path("admin/", admin.site.urls),
    path("api/", include("api.urls")),
    path("customer/", include("customer_portal.urls")),
    path("", include("kiosk.urls")),
]

# Django serves uploaded media only when DEBUG is on. On the showroom PC this
# is fine — it runs with DEBUG=True behind a LAN. For a real deployment, put
# nginx (or WhiteNoise + a media backend) in front and drop this block.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / "static")
