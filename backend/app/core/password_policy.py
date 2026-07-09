MIN_PASSWORD_LENGTH = 6


def password_length_message() -> str:
    return f"Password must contain at least {MIN_PASSWORD_LENGTH} characters."
