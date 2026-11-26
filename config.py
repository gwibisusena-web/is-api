import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Autentikasi (JWT)
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
    JWT_ALGORITHM = "HS256"
    JWT_EXPIRES_MINUTES = int(os.getenv("JWT_EXPIRES_MINUTES", "60"))

    # Audit logging
    LOG_ENABLED = os.getenv("LOG_ENABLED", "1").strip().lower() in (
        "1", "true", "yes", "y", "on"
    )
    LOG_MAX_BODY_BYTES = int(os.getenv("LOG_MAX_BODY_BYTES", "8192"))
