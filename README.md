# Backend API — Produkcja Mobile

## Uruchomienie lokalne (test)

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Otwórz http://localhost:8000/docs — interaktywna dokumentacja API.

---

## Deployment na Railway.app (darmowy)

1. Wejdź na https://railway.app i zaloguj się przez GitHub
2. Kliknij **New Project → Deploy from GitHub repo**
3. Wgraj folder `backend/` jako repozytorium
4. Railway automatycznie wykryje `Procfile` i zainstaluje `requirements.txt`
5. Ustaw zmienne środowiskowe (Settings → Variables):
   - `SECRET_KEY` = dowolny długi losowy ciąg, np. `openssl rand -hex 32`
   - `DB_PATH` = ścieżka do bazy danych (patrz niżej)

---

## WAŻNE: Baza danych

### Opcja A — Współdzielona baza (apka desktopowa + mobilna na tym samym PC)

Jeśli backend działa na tym samym komputerze co Tkinter:
- Ustaw `DB_PATH` na bezwzględną ścieżkę do `produkcja.db`, np.:
  `C:\Users\Jan\Desktop\produkcja_v8\db\produkcja.db`
- Uruchom backend lokalnie komendą powyżej
- W aplikacji mobilnej wpisz IP komputera zamiast adresu Railway

### Opcja B — Railway (dostęp przez internet)

Railway nie ma trwałego dysku dla plików. Musisz migrować bazę na PostgreSQL:

1. Dodaj PostgreSQL w Railway: **New → Database → PostgreSQL**
2. Railway automatycznie doda `DATABASE_URL` do zmiennych
3. Uruchom skrypt migracji: `python migrate_to_postgres.py`

Lub użyj **darmowego Supabase** (PostgreSQL przez internet):
- Utwórz projekt na https://supabase.com
- Skopiuj connection string do `DB_PATH` jako `postgresql://...`

### Opcja C — Prostszy setup: ngrok (do testów)

Uruchom backend lokalnie, a ngrok udostępni go przez internet:
```bash
ngrok http 8000
```
Skopiuj adres ngrok do aplikacji mobilnej.

---

## Jak to współgra z aplikacją desktopową Tkinter

```
Tkinter (PC biuro)          Telefon Android
     |                            |
     |-- bezpośrednio SQLite      |-- HTTP → Backend API → SQLite
     |                            |
     └──────── ta sama baza ──────┘
```

Tkinter ma **pełny podgląd na produkcję w czasie rzeczywistym** — czyta tę
samą bazę co backend API. Pracownicy wpisują dane przez telefon, kierownik
widzi je od razu w Tkinterze.

---

## Testowanie API

```bash
# Login
curl -X POST http://localhost:8000/api/login \
  -H "Content-Type: application/json" \
  -d '{"username":"piotr.p","password":"pracownik1"}'

# Skan QR (z tokenem)
curl http://localhost:8000/api/qr/ZL-001 \
  -H "Authorization: Bearer <TOKEN>"
```
