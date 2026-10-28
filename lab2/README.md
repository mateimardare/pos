# IDM gRPC + MariaDB

## 1. Configurare `.env`
Toate secretele stau în `.env` (lângă `docker-compose.yml`), nu în `docker-compose.yml`:

```env
MARIADB_ROOT_PASSWORD=<parola root MariaDB>
DB_PASSWORD=<parola utilizatorului idm_app>
JWT_SECRET=<secret aleator, minim 32 de octeți>
# opțional: JWT_ISSUER=http://localhost:50051
```

`JWT_SECRET` nu este generat de proiect; generează-l o singură dată, aleator:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
# sau
openssl rand -base64 48
```

Nu comite `.env` în git.

## 2. Pornește serviciile
```bash
docker compose up -d --build
docker compose logs -f idm
```

Generarea codului protobuf se face în timpul build-ului. La prima pornire, containerul `mariadb` creează baza `idm`, utilizatorul `idm_app` (cu `DB_PASSWORD`) și rulează `schema.sql`.

`schema.sql` rulează doar la prima inițializare a volumului `mariadb_data`. Dacă volumul există deja cu schema veche (`password NOT NULL`), fie îl recreezi (`docker compose down -v`, **șterge toate datele**), fie rulezi:

```sql
ALTER TABLE idm.users MODIFY password VARCHAR(255) NULL;
```

### MariaDB în afara Docker Compose
Execută `schema.sql` pe instanța disponibilă și creează un utilizator SQL dedicat serviciului (nu folosi root în aplicație):

```sql
CREATE USER 'idm_app'@'%' IDENTIFIED BY 'CHANGE_ME_DB_PASSWORD';
GRANT SELECT, INSERT, UPDATE ON idm.users TO 'idm_app'@'%';
FLUSH PRIVILEGES;
```

Restrânge host-ul SQL și expunerea porturilor conform mediului tău. Setează `DB_HOST` la numele serviciului/containerului MariaDB (aceeași rețea Docker) sau la `host.docker.internal` dacă MariaDB rulează pe gazdă (pe Linux, prin `extra_hosts`).

## 3. Inserează manual primul cont (admin)
Rulează scriptul din folderul proiectului (importă `uuid7.py` local):

```bash
.venv/bin/pip install bcrypt          # dacă nu e deja instalat
.venv/bin/python create_admin_hash.py
```

Scriptul cere parola (minim 12 caractere) și afișează un `INSERT` gata de rulat, de forma:

```sql
INSERT INTO users VALUES (UNHEX('<uid hex>'), 'admin', '<hash bcrypt>', 'administrator', TRUE);
```

Rulează-l în baza de date (vezi [Acces manual la baza de date](#acces-manual-la-baza-de-date)), apoi verifică:

```sql
SELECT HEX(uid), username, role, is_active FROM users;
```

Atenție la schemă:
- `uid` este `BINARY(16)`, deci se folosește `UNHEX('<32 caractere hex>')`, nu `UUID()` (care întoarce un șir de 36 de caractere).
- `role` acceptă doar valorile din `ENUM`-ul din `schema.sql` (`'administrator'`, `'maintainer'`).

Nu salva parola în clar în tabel.

## 4. Schimbarea parolei admin
Nu există o metodă gRPC pentru schimbarea parolei; se face direct în baza de date.

1. Generează un hash nou (parola: minim 12 caractere, maxim 72 de octeți):

   ```bash
   .venv/bin/python -c "import bcrypt, getpass; print(bcrypt.hashpw(getpass.getpass().encode(), bcrypt.gensalt()).decode())"
   ```

   (Sau rulează `create_admin_hash.py` și folosește doar linia `hash:`.)

2. Actualizează rândul (hash-ul între apostrofuri simple, altfel `$` poate fi interpretat):

   ```sql
   UPDATE users SET password = '<hash nou>' WHERE username = 'admin';
   ```

3. Testează: `.venv/bin/python client.py login admin '<parola nouă>'`

Tokenurile emise înainte rămân valide până expiră (max. 1 oră). Pentru invalidare imediată, vezi [Invalidarea tuturor tokenurilor](#invalidarea-tuturor-tokenurilor).

## Rulare locală / client de test
```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m grpc_tools.protoc -I./proto --python_out=. --grpc_python_out=. ./proto/idm.proto

.venv/bin/python client.py login admin '<parola admin>'          # afișează access_token
export IDM_TOKEN=<access_token>
.venv/bin/python client.py create ion maintainer                # doar username + rol
.venv/bin/python client.py login ion '<parola aleasă de ion>'   # primul login setează parola
.venv/bin/python client.py list
.venv/bin/python client.py deactivate <uid>
```

## 5. Metode gRPC și reguli
- `Login`: nu cere token; returnează JWT dacă parola este corectă și contul activ.
- `ListUsers`: doar administratorul.
- `CreateUser`: primește doar `username` și `role`. Contul nou nu are parolă (`password` este NULL) și este inactiv până la primul login.
- Primul `Login` pentru un cont fără parolă setează parola trimisă (minim 12 caractere, maxim 72 de octeți), apoi returnează JWT. Loginurile ulterioare verifică parola normal.
- `DeactivateUser` / `ActivateUser`: doar administratorul.
- Toate metodele în afară de `Login` cer metadata `authorization: Bearer <JWT>`.
- Parolele sunt stocate cu bcrypt; câmpul `password` din DB conține hash-ul, nu parola originală.
- Conturile dezactivate nu se pot autentifica, iar tokenurile lor sunt refuzate la fiecare cerere protejată.

## 6. Instrucțiuni de urgență

### Stare și loguri
```bash
docker compose ps                     # ce rulează și starea healthcheck-ului
docker compose logs --tail=100 idm    # ultimele erori ale serverului gRPC
docker compose logs --tail=100 mariadb
```

### Repornire
```bash
docker compose restart idm            # repornește doar serverul gRPC
docker compose restart                # repornește tot
docker compose up -d --build idm      # după modificări în cod (*.py, proto, requirements)
docker compose up -d                  # după modificări în .env (restart NU recitește .env)
docker compose down && docker compose up -d   # oprire completă + pornire; datele rămân în volum
```

### Acces manual la baza de date
Ca root (parola este deja în containerul `mariadb`, din `MARIADB_ROOT_PASSWORD`):

```bash
docker exec -it mariadb sh -c 'mariadb -u root -p"$MARIADB_ROOT_PASSWORD" idm'
```

Ca utilizatorul aplicației (cere `DB_PASSWORD`):

```bash
docker exec -it mariadb mariadb -u idm_app -p idm
```

Interogări utile:

```sql
SELECT HEX(uid), username, role, is_active, password IS NOT NULL AS are_parola FROM users;
UPDATE users SET is_active = TRUE  WHERE username = 'admin';   -- reactivează admin-ul
UPDATE users SET is_active = FALSE WHERE username = '<user>';  -- blochează un cont
```

### Admin blocat / parolă uitată
- Cont dezactivat: reactivează-l cu `UPDATE ... SET is_active = TRUE` (mai sus).
- Parolă uitată: urmează pașii din [secțiunea 4](#4-schimbarea-parolei-admin).
- Rândul admin lipsește: refă [secțiunea 3](#3-inserează-manual-primul-cont-admin).

### Invalidarea tuturor tokenurilor
JWT-urile nu sunt verificate față de parolă, deci rămân valide până expiră. Pentru a le invalida pe toate (ex. token scurs), schimbă `JWT_SECRET` în `.env` și recreează containerul:

```bash
docker compose up -d idm
```

Toți utilizatorii vor trebui să se autentifice din nou.

### Backup și restaurare
```bash
docker exec mariadb sh -c 'mariadb-dump -u root -p"$MARIADB_ROOT_PASSWORD" idm' > backup_idm.sql
docker exec -i mariadb sh -c 'mariadb -u root -p"$MARIADB_ROOT_PASSWORD" idm' < backup_idm.sql
```

### Resetare completă (șterge TOATE datele)
```bash
docker compose down -v                # șterge volumul mariadb_data
docker compose up -d --build          # schema.sql rulează din nou
```

Apoi refă [secțiunea 3](#3-inserează-manual-primul-cont-admin) pentru contul admin.

## Note
Acesta este un schelet didactic. Pentru producție, configurează TLS/mTLS, secret management, rate limiting pentru login, audit logging și politici de parole. JWT-ul este semnat cu un secret comun (HS256) și expiră după o oră. Schema are un singur tabel `users`.
