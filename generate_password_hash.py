"""Run locally to generate a Streamlit Secrets password hash.

Usage: python generate_password_hash.py
Never commit the resulting password or a real secrets.toml file.
"""
from getpass import getpass
from app import password_hash

password = getpass("New auditor password: ")
confirm = getpass("Confirm password: ")
if not password:
    raise SystemExit("Password cannot be blank.")
if password != confirm:
    raise SystemExit("Passwords did not match.")
print(password_hash(password))
