from django.conf import settings
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User

admin.site.register(User, UserAdmin)
admin.site.site_header = f"{settings.SITE_NAME} · 管理后台"
admin.site.site_title = settings.SITE_NAME
admin.site.index_title = "管理后台"
