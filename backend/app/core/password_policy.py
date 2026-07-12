from app.core.limits import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH

__all__ = ["MAX_PASSWORD_LENGTH", "MIN_PASSWORD_LENGTH", "password_length_message"]


def password_length_message() -> str:
    return f"Password must contain at least {MIN_PASSWORD_LENGTH} characters."
