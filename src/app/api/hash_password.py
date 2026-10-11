"""Adds a website user by hand. Prints one AUTH_USERS entry; paste it into .env (or Key Vault), separated by ';'.

    python -m app.api.hash_password someone@example.com
"""

import getpass
import sys

from app.api.security import hash_password

MIN_LENGTH = 12

if __name__ == "__main__":
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        sys.exit("usage: python -m app.api.hash_password <email>")
    password = getpass.getpass("Password: ")
    if len(password) < MIN_LENGTH:
        sys.exit(f"Use at least {MIN_LENGTH} characters.")
    if password != getpass.getpass("Repeat: "):
        sys.exit("The passwords differ.")
    print(f"{sys.argv[1].strip().lower()}={hash_password(password)}")
