"""Generează un hash bcrypt pentru inserarea manuală a contului admin."""
import getpass
import bcrypt

password = getpass.getpass("Parola inițială pentru admin (minim 12 caractere): ")
if len(password) < 12:
    raise SystemExit("Parola trebuie să aibă minimum 12 caractere.")
print(bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode())
