# IDM gRPC + MariaDB

## 1. Baza de date
Execută `schema.sql` pe instanța MariaDB disponibilă. Creează un utilizator SQL dedicat serviciului (nu folosi root în aplicație), de exemplu:

```sql
CREATE USER 'idm_app'@'%' IDENTIFIED BY 'CHANGE_ME_DB_PASSWORD';
GRANT SELECT, INSERT, UPDATE ON idm.users TO 'idm_app'@'%';
FLUSH PRIVILEGES;
```

Restrânge host-ul SQL și expunerea porturilor conform mediului tău. Dacă MariaDB rulează într-un alt container pe aceeași rețea Docker, setează `DB_HOST` la numele acelui serviciu/container și atașează ambele containere la aceeași rețea. `host.docker.internal` este potrivit când MariaDB rulează pe gazdă; pe Linux este configurat prin `extra_hosts`.

## 2. Inserează manual contul admin
Instalează bcrypt local (`pip install bcrypt`), apoi rulează `python create_admin_hash.py` și copiază hash-ul rezultat în SQL:

```sql
INSERT INTO users (uid, username, password, role, is_active)
VALUES (UUID(), 'admin', '<HASH_BCRYPT_GENERAT>', 'admin', TRUE);
```

Nu salva parola în clar în tabel.

## 3. Configurează și pornește serviciul
În `docker-compose.yml`, schimbă `DB_PASSWORD` și `JWT_SECRET` cu valori secrete, unice. Apoi:

```bash
docker compose up -d --build
docker compose logs -f idm
```

Generarea codului protobuf se face în timpul build-ului.

`schema.sql` rulează doar la prima inițializare a volumului `mariadb_data`. Dacă volumul există deja cu schema veche (`password NOT NULL`), fie îl recreezi (`docker compose down -v`), fie rulezi:

```sql
ALTER TABLE idm.users MODIFY password VARCHAR(255) NULL;
```

## Rulare locală / client de test
```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m grpc_tools.protoc -I./proto --python_out=. --grpc_python_out=. ./proto/idm.proto

.venv/bin/python client.py login admin '<parola admin>'          # afișează access_token
export IDM_TOKEN=<access_token>
.venv/bin/python client.py create ion user                      # doar username + rol
.venv/bin/python client.py login ion '<parola aleasă de ion>'   # primul login setează parola
.venv/bin/python client.py list
.venv/bin/python client.py deactivate <uid>
```

## 4. Metode gRPC și reguli
- `Login`: nu cere token; returnează JWT dacă parola este corectă și contul activ.
- `ListUsers`: doar `admin`.
- `CreateUser`: primește doar `username` și `role`. `admin` poate crea rolurile `admin`, `maintainer`, `user`; `maintainer` poate crea doar `user`. Contul nou nu are parolă (`password` este NULL).
- Primul `Login` pentru un cont fără parolă setează parola trimisă (minim 12 caractere, maxim 72 de octeți), apoi returnează JWT. Loginurile ulterioare verifică parola normal.
- `DeactivateUser` / `ActivateUser`: doar `admin`.
- Toate metodele în afară de `Login` cer metadata `authorization: Bearer <JWT>`.
- Parolele sunt stocate cu bcrypt; câmpul `password` din DB conține hash-ul, nu parola originală.
- Conturile dezactivate nu se pot autentifica, iar tokenurile lor sunt refuzate la fiecare cerere protejată.

## Note
Acesta este un schelet didactic. Pentru producție, configurează TLS/mTLS, secret management, rate limiting pentru login, audit logging și politici de parole. JWT-ul este semnat cu un secret comun și expiră după o oră. Schema are un singur tabel `users`.
