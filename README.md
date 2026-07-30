# Recruiters — Fleet Planner

Interna Django aplikacija za planiranje flote (truck / driver / assignment).

**Stack:** Django 5 · SQLite (local) · PostgreSQL (prod) · templates

## Roles

| Department | Role | Access |
|------------|------|--------|
| IT | Admin | Full |
| Recruiting | Read Only | Read |

## Fleet Planner — kratko

- **`RelayAssignment`** = izvor istine (driver ↔ truck period)
- **`DriverStatusPeriod`** = OTR / home time istorija
- **`Truck.current_driver`** = cache
- **`RelayStatusOverride`** = legacy fallback (nije glavni UI)

Ručni workflow: **Start Assignment** → **Plan Next** → **Complete**.  
Timeline boje (crvena / zelena / žuta / siva) se računaju pri prikazu.

```
/                  Fleet board
/trucks/<id>/      Truck detail
/drivers/<id>/     Driver detail
/dashboard/        Dashboard
```

## Local setup (PowerShell)

Prvo pokretanje:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env.local
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_departments_roles
```

Najjednostavnije pokretanje servera — aktivacija environmenta nije potrebna:

```powershell
.\.venv\Scripts\python.exe manage.py runserver
```

Server je dostupan na http://127.0.0.1:8000/. Za zaustavljanje pritisni `Ctrl+C`.

Ako želiš aktiviran environment i `(.venv)` u promptu:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python manage.py runserver
```

`-Scope Process` važi samo za trenutni PowerShell prozor i ne menja trajno sistemsku
execution policy. Komanda `.venv\Scripts\activate.bat` namenjena je za CMD, ne za
PowerShell.

## Local setup (CMD)

```bat
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
copy .env.example .env.local
python manage.py migrate
python manage.py seed_departments_roles
python manage.py create_it_admin --email admin@example.com --full-name "IT Admin" --password "your-password"
python manage.py runserver
```

- App: http://127.0.0.1:8000/
- Login: `/accounts/login/`
- Admin: `/admin/`

> `createsuperuser` radi tek **posle** `seed_departments_roles`.

## Production setup

Na serveru (SSH), u folderu projekta + aktiviran venv:

```bash
export DJANGO_SETTINGS_MODULE=config.settings.production
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_departments_roles
python manage.py create_it_admin --email admin@example.com --full-name "IT Admin" --password "your-password"
python manage.py collectstatic --noinput
```

Podesi `.env.production` (`SECRET_KEY`, `ALLOWED_HOSTS`, baza).

Static (admin CSS) služi **WhiteNoise** — mora `collectstatic` + restart app.
Za početak koristi **SQLite** (kao u primeru `.env.production`).  
Postgres tek kad imaš pravu bazu + `pip install -r requirements.txt`.

Web app (Passenger/WSGI) koristi `config.settings.production` — učitava `.env.production`.  
Posle izmene env fajla: **Restart** Python app u cPanelu.
Opciono (cron) — relay state + periodični Pro Transport master sync.
Koristi `flock` da paralelni cron jobovi ne trče istovremeno:

```bash
# Relay handoffs (scheduler only — NOT on page GET)
*/15 * * * * flock -n /var/lock/recruiters_process_relay_state.lock \
  bash -lc 'cd /path/to/recruiters && DJANGO_SETTINGS_MODULE=config.settings.production \
  /path/to/.venv/bin/python manage.py process_relay_state'

# Master data from Pro Transport
*/30 * * * * flock -n /var/lock/recruiters_sync_protransport_master.lock \
  bash -lc 'cd /path/to/recruiters && DJANGO_SETTINGS_MODULE=config.settings.production \
  /path/to/.venv/bin/python manage.py sync_protransport_master'

# Optional nightly integrity report (read-only)
# 15 3 * * * flock -n /var/lock/recruiters_audit_fleet.lock \
#   bash -lc 'cd /path/to/recruiters && DJANGO_SETTINGS_MODULE=config.settings.production \
#   /path/to/.venv/bin/python manage.py audit_fleet_integrity'
```

Jednokratni bootstrap assignment-a (ručno, sa potvrdom):

```bash
# Preview
python manage.py sync_protransport_master --dry-run
python manage.py bootstrap_protransport_assignments --dry-run --default-start-date=2026-07-01

# Write
python manage.py sync_protransport_master
python manage.py bootstrap_protransport_assignments --confirm --default-start-date=2026-07-01
python manage.py audit_fleet_integrity
```

U `.env.production` (nije u Gitu) popuni read-only pristup PT bazi:

```
PRO_TRANSPORT_DB_HOST=...
PRO_TRANSPORT_DB_NAME=...
PRO_TRANSPORT_DB_USER=...
PRO_TRANSPORT_DB_PASSWORD=...
PRO_TRANSPORT_DB_PORT=5432
# Opciono cutover datum za bootstrap:
# PRO_TRANSPORT_BOOTSTRAP_DEFAULT_START_DATE=2026-07-01
```

**Master sync** ažurira companies (`company_data`), pa drivers/trucks po stabilnim PT ID-jevima
(`driver_id` / `protransport_id`) i vezuje `division` preko PT `division_id`.
**Novi vozači** se kreiraju samo ako su PT employment `ACTIVE` i company driver;
**novi kamioni** samo ako su PT-active (`source_is_active`, ne `total loss`)
(`IMPORT_*` allowlists u `sync/services/master_sync.py` — lako proširiti kasnije).
Već postojeći zapisi se i dalje ažuriraju.
**Ne dira** `RelayAssignment`, `DriverStatusPeriod`, `Truck.current_driver`, ni lokalni
operational status (`otr` / `yard` / `home_time`). Excel import je legacy fallback.

`Driver.driver_id` = Pro Transport `drivers.id` (stabilni ključ; nije preimenovan zbog kompatibilnosti).
`Truck.protransport_id` = Pro Transport `trucks.id`.

## Settings

| Env | Settings | File | Ko koristi |
|-----|----------|------|------------|
| Local | `config.settings.local` | `.env.local` | `manage.py` / `runserver` |
| Prod | `config.settings.production` | `.env.production` | WSGI / sajt |
CMD (prod settings lokalno za test):

```bat
set DJANGO_SETTINGS_MODULE=config.settings.production
```

## Korisne komande

```bat
python manage.py migrate
python manage.py seed_departments_roles
python manage.py create_it_admin --email admin@example.com --full-name "IT Admin" --password "your-password"
python manage.py collectstatic --noinput
python manage.py process_relay_state
python manage.py sync_protransport_master
python manage.py sync_protransport_master --dry-run
python manage.py bootstrap_protransport_assignments --dry-run --default-start-date=YYYY-MM-DD
python manage.py rebuild_current_driver_cache
python manage.py audit_fleet_integrity
python manage.py runserver
```

## Napomena

Pro Transport: `sync_protransport_master` (periodično) + `bootstrap_protransport_assignments`
(jednom). Excel import je legacy fallback. Bootstrap match isključivo po PT ID-jevima
(`driver_id` / `protransport_id`). Estimated start dates imaju `start_date_is_estimated`
i čiste se kad korisnik ručno izmeni datume u UI.
