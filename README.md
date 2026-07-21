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
Opciono (cron):

```bash
python manage.py process_relay_state
```

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
python manage.py runserver
```

## Napomena

Pro Transport sync još nije live (`sync_protransport_snapshot` = placeholder). Driveri i truckovi se unose ručno.
