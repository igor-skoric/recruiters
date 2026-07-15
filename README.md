# Recruiters — Internal Portal

Interna web aplikacija za sync i prikaz podataka iz postojeće **Pro Transport** aplikacije.

Trenutno projekat sadrži osnovnu arhitekturu, autentifikaciju preko email-a, department/role sistem i placeholder strukturu za buduće sync module.

## Tehnologije

- Django 5.x
- SQLite (lokalni development)
- Django templates
- Struktura spremna za PostgreSQL u production okruženju

## Struktura projekta

```
recruiters/
├── accounts/                 # Custom User, login/logout, permissions
│   ├── management/commands/
│   │   └── create_it_admin.py
│   ├── managers.py
│   ├── permissions.py
│   └── ...
├── config/                   # Django project settings i URL routing
│   └── settings/
│       ├── base.py
│       ├── local.py
│       └── production.py
├── core/                     # Dashboard i shared views
├── departments/              # Department i Role modeli
│   └── management/commands/
│       └── seed_departments_roles.py
├── sync/                     # Placeholder za budući Pro Transport sync
│   └── services/
│       └── pro_transport_sync.py
├── drivers/                  # Driver modeli
├── trucks/                   # Truck modeli
├── relay/                    # Relay Calendar / Planner
│   ├── services/
│   │   └── relay_service.py
│   ├── forms.py
│   └── views.py
├── static/
├── templates/
├── .env.example
├── .env.local
├── .env.production
├── manage.py
└── requirements.txt
```

## Department / Role sistem

Sistem je proširiv kroz modele `Department` i `Role`.

Početni seed podaci:

| Department  | Role       | Access level |
|-------------|------------|--------------|
| IT          | Admin      | Full Access  |
| Recruiting  | Read Only  | Read Only    |

- **IT Admin** ima full access (čitanje i pisanje)
- **Recruiting Read Only** ima samo read access

Permission helper-i, decorator-i i mixin-i nalaze se u `accounts/permissions.py`:

- `user_in_department()`
- `user_has_role()`
- `user_has_read_access()`
- `user_has_full_access()`

## Sync priprema

`sync` aplikacija je pripremljena za buduću integraciju sa Pro Transport PostgreSQL bazom.

Placeholder servis: `sync/services/pro_transport_sync.py`

Kasnije će se implementirati sync za **3 tabele** iz Pro Transport baze. Konekcija ka external bazi biće konfigurisana kroz posebne env parametre (vidi `.env.example`).

## Fleet Planner (Truck Timeline)

**Glavni entitet aplikacije je Truck.** Driver je atribut truck-a u određenom trenutku.

Ceo planner se vrti oko **truck timeline-a** — dispečer za 5 sekundi vidi stanje cele flote.

### Workflow

1. **Sync** drivera i truckova iz Pro Transport baze
2. **Fleet dashboard** (`/`) automatski prikazuje sve truckove sa timeline-om
3. Klik na truck → detalji i korekcije (cycle start, relay date, next driver, notes)
4. Sistem automatski crta **4 crvene + 1 zelenu** nedelju u ciklusu

```bash
python manage.py sync_protransport_snapshot
```

### Poslovna logika

- Driver vozi **4 nedelje OTR**, zatim **1 nedelja home time**
- Truck nema obavezan odmor — drugi driver može odmah da preuzme isti truck
- Intervali su **poluotvoreni**: `[start_date, end_date)` — sledeći assignment sme da krene istog dana kada se prethodni završava
- Bez jasnih datuma / plana → **žuto** (needs review) na timeline-u

### Boje na timeline-u

| Boja | Značenje |
|------|----------|
| Crvena | Truck occupied (OTR) |
| Zelena | Available / relay week |
| Žuta | Needs review |
| Siva | Maintenance / inactive |

### Arhitektura — izvor istine

| Sloj | Uloga |
|------|--------|
| **`RelayAssignment`** | **Glavni istorijski izvor** veze driver ↔ truck kroz vreme (OTR periodi) |
| **`DriverStatusPeriod`** | Istorija statusa drivera (OTR, home_time, available, …); home time se kreira na `complete_assignment` |
| **`Truck.current_driver`** | Cache / sync pomoćno polje; usklađuje se pri activate/complete; nije konačna istina ako postoji ACTIVE assignment |
| **`RelayStatusOverride`** | **Samo fallback / korekcija** kada assignment ne postoji (notes, privremeni plan). Ne prepisuje istorijski assignment |

Prioritet za occupancy / current driver na boardu:

1. ACTIVE `RelayAssignment` (za današnji datum)
2. Sledeći PLANNED assignment (next driver)
3. `RelayStatusOverride` (fallback)
4. `Truck.current_driver` + `Truck.status` (cache / sync)

### Timeline (automatski)

Boje se **ne čuvaju u bazi**. Računaju se pri renderu iz:

1. `RelayAssignment` (truck occupancy)
2. `DriverStatusPeriod` (driver timeline)
3. `Truck.status` (maintenance / inactive)
4. `RelayStatusOverride` samo kao fallback kada nema assignment-a

Truck timeline: **crveno** = assignment preseca nedelju; **zeleno** = slobodan; **žuto** = budući gap bez next plana; **sivo** = maintenance/inactive.  
Kontinuiran handoff (John→Mike isti dan) ostaje crven bez zelene pauze.

ISO nedelje su poluotvorene `[monday, next_monday)`.

### Job / stanje

```bash
python manage.py process_relay_state
# opciono: python manage.py process_relay_state --as-of 2026-07-15
```

Idempotentno aktivira PLANNED / završava istekli ACTIVE. Fleet board takođe poziva ovu funkciju pre prikaza (lokalni development); u produkciji preferirati cron.

### UI

| URL | Opis |
|-----|------|
| `/` | Fleet Planner + period kontrole (prev/today/next, 8/12/16/52w, year) |
| `/trucks/<id>/` | Truck detalj, timeline, history, quick plan → PLANNED assignment |
| `/drivers/<id>/` | Driver history + status periodi + weekly timeline |

### Fleet summary

Dashboard prikazuje brojače: OTR · Available · Needs review · No driver · Maintenance

### Korekcije / planning

Override forma na truck detalju ostaje kao fallback planiranje. Trajna istorija je **Assignment history**. Pravi Pro Transport sync još nije implementiran (`sync_protransport_snapshot` je placeholder).

## Local setup

### 1. Kreiraj virtual environment

```bash
python -m venv .venv
```

Windows (PowerShell):

```powershell
.\.venv\Scripts\Activate.ps1
```

Linux/macOS:

```bash
source .venv/bin/activate
```

### 2. Instaliraj dependencies

```bash
pip install -r requirements.txt
```

### 3. Podesi environment

Za lokalni rad koristi `.env.local` (već postoji kao primer fajl).

Ako želiš da kreneš od nule:

```bash
copy .env.example .env.local
```

Na Linux/macOS:

```bash
cp .env.example .env.local
```

### 4. Pokreni migracije

```bash
python manage.py migrate
```

### 5. Seed department-a i rola

```bash
python manage.py seed_departments_roles
```

### 6. Kreiraj prvog IT admin korisnika

Preporučena komanda:

```bash
python manage.py create_it_admin --email admin@example.com --full-name "IT Admin" --password "your-secure-password"
```

Alternativa: kreiraj korisnika kroz Django admin (`/admin/`) nakon što postoji staff nalog.

### 7. Pokreni development server

```bash
python manage.py runserver
```

Aplikacija je dostupna na: http://127.0.0.1:8000/

- Login: http://127.0.0.1:8000/accounts/login/
- Fleet Planner: http://127.0.0.1:8000/
- Admin: http://127.0.0.1:8000/admin/

## Settings okruženja

| Okruženje   | Settings modul              | Env fajl          |
|-------------|-----------------------------|-------------------|
| Local       | `config.settings.local`     | `.env.local`      |
| Production  | `config.settings.production`| `.env.production` |

`manage.py` podrazumevano koristi `config.settings.local`.

Za production deploy:

```bash
set DJANGO_SETTINGS_MODULE=config.settings.production
```

Linux/macOS:

```bash
export DJANGO_SETTINGS_MODULE=config.settings.production
```

## Baza podataka

Trenutno se koristi **SQLite** za brz lokalni start.

SQLite je privremeno rešenje — production i budući lokalni setup će koristiti **PostgreSQL**.

Prelazak na PostgreSQL:

1. Instaliraj `psycopg2-binary` (odkomentariši u `requirements.txt`)
2. Podesi `DATABASE_*` varijable u `.env.local` ili `.env.production`
3. Pokreni migracije ponovo

Primer vrednosti nalazi se u `.env.example`.

## Autentifikacija

- Login je preko **email + password**
- Nema username polja
- Custom `User` model: `accounts.User`

## Admin panel

U admin panelu su registrovani:

- User
- Department
- Role
- Driver
- Truck
- RelayAssignment
- RelayStatusOverride

User admin prikazuje: email, full_name, department, role, is_active, is_staff.

## Korisne komande

```bash
python manage.py migrate
python manage.py seed_departments_roles
python manage.py create_it_admin --email admin@example.com --full-name "IT Admin" --password "your-secure-password"
python manage.py sync_protransport_snapshot   # import drivers/trucks (placeholder)
python manage.py runserver
python manage.py createsuperuser   # automatski dodeljuje IT / Admin department i rolu
```
