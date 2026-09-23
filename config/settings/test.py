import tempfile
from pathlib import Path

from .base import *  # noqa: F403

DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="vp-media-"))
EXPORT_DIR = Path(tempfile.mkdtemp(prefix="vp-exports-"))
