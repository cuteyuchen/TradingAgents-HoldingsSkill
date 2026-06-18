"""One-off helper to generate and print a token for first-time setup.

Usage:  python -m app.seed
"""
import secrets

from .config import settings


def main() -> None:
    if settings.ADVISOR_TOKEN:
        print("ADVISOR_TOKEN already set in environment.")
        return
    token = "adv_" + secrets.token_urlsafe(24)
    print("Generated ADVISOR_TOKEN (store this in your .env / env var):")
    print(token)
    print()
    print("Add to .env:  ADVISOR_TOKEN=" + token)


if __name__ == "__main__":
    main()
