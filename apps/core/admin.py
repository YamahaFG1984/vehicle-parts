from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User

admin.site.register(User, UserAdmin)
admin.site.site_header = "零件资料归一化 · 管理后台"
admin.site.site_title = "零件资料归一化"
