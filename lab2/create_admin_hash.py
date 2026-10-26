"""Generează uid-ul și hash-ul bcrypt pentru inserarea manuală a contului admin."""
import getpass
import bcrypt

from uuid7 import uuid7

password = getpass.getpass("Parola inițială pentru admin (minim 12 caractere): ")
if len(password) < 12:
    raise SystemExit("Parola trebuie să aibă minimum 12 caractere.")
uid_hex = uuid7().hex
password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
print(f"uid:  {uid_hex}")
print(f"hash: {password_hash}")
print()
print("SQL:")
print(f"INSERT INTO users VALUES (UNHEX('{uid_hex}'), 'admin', '{password_hash}', 'administrator', TRUE);")
