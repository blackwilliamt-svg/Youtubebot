#!/usr/bin/env python3
"""
One-off helper to generate the value for DASHBOARD_PASSWORD_HASH in .env.
Run it, paste a password, paste the printed hash into .env, and never put
the plaintext password in any file.

    python gen_password_hash.py
"""
import getpass

from werkzeug.security import generate_password_hash

if __name__ == "__main__":
    pw = getpass.getpass("Dashboard password: ")
    pw2 = getpass.getpass("Confirm: ")
    if pw != pw2:
        raise SystemExit("Passwords did not match.")
    print("\nDASHBOARD_PASSWORD_HASH=" + generate_password_hash(pw))
