from django.contrib.auth.models import AbstractUser
from django.db import models


class TimeStampedModel(models.Model):
    """Every table records when a row was created and last modified."""

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class User(AbstractUser):
    """Custom user from day one, so reviewer fields can be added without a painful migration."""

    class Meta:
        verbose_name = "用户"
        verbose_name_plural = "用户"
