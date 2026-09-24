from django.conf import settings


def site(request):
    """The system name, defined once in settings.SITE_NAME."""
    return {"SITE_NAME": settings.SITE_NAME}
