from mai.auth.hasher import Argon2Hasher
from mai.config import settings
from mai.db.session import SessionFactory
from mai.web.app import create_app


def build_app():
    return create_app(SessionFactory, Argon2Hasher(), settings.session_secret,
                      cookie_secure=settings.cookie_secure)
