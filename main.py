"""
Backend API dla aplikacji mobilnej pracowników.
Łączy się z tą samą bazą SQLite co aplikacja desktopowa Tkinter.
Uruchamiać obok aplikacji desktopowej — oba korzystają z tej samej bazy.

Uruchomienie:
    pip install fastapi uvicorn python-jose[cryptography] passlib[bcrypt]
    uvicorn main:app --host 0.0.0.0 --port 8000

Railway.app / Render.com: ustaw START_COMMAND na powyższe.
"""

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional, List
import sqlite3
import hashlib
import datetime
import json
import os
from jose import JWTError, jwt

# ── Konfiguracja ────────────────────────────────────────────────────────────

SECRET_KEY = os.getenv("SECRET_KEY", "zmien-na-losowy-ciag-znakow-w-produkcji")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 12

# Ścieżka do bazy danych — ta sama co aplikacja desktopowa
# Na serwerze Railway: ustaw env DB_PATH na ścieżkę do pliku lub użyj PostgreSQL
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "produkcja_v8", "db", "produkcja.db"))

app = FastAPI(title="Produkcja Mobile API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()


# ── Baza danych ──────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def hash_pass(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


# ── JWT ──────────────────────────────────────────────────────────────────────

def create_token(user_id: int, username: str, role: str) -> str:
    expire = datetime.datetime.utcnow() + datetime.timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode(
        {"sub": str(user_id), "username": username, "role": role, "exp": expire},
        SECRET_KEY, algorithm=ALGORITHM
    )


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return {
            "id": int(payload["sub"]),
            "username": payload["username"],
            "role": payload["role"]
        }
    except JWTError:
        raise HTTPException(status_code=401, detail="Nieprawidłowy token")


# ── Modele ───────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class StartSesjaRequest(BaseModel):
    operacja_id: int
    typ: str = "obrobka"


class StopSesjaRequest(BaseModel):
    sesja_id: int
    ilosc_sztuk: int = 0
    uwagi: str = ""


class PauzaRequest(BaseModel):
    sesja_id: int
    akcja: str  # "start" lub "stop"


class UwagiRequest(BaseModel):
    sesja_id: int
    uwagi: str


# ── Endpointy ────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok", "service": "Produkcja Mobile API v1.0"}


@app.get("/health")
def health():
    try:
        conn = get_conn()
        conn.execute("SELECT 1")
        conn.close()
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        return {"status": "error", "db": str(e)}


# ── AUTH ─────────────────────────────────────────────────────────────────────

@app.post("/api/login")
def login(req: LoginRequest):
    conn = get_conn()
    user = conn.execute(
        "SELECT id, username, full_name, role FROM users WHERE username=? AND password=?",
        (req.username, hash_pass(req.password))
    ).fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=401, detail="Błędny login lub hasło")

    token = create_token(user["id"], user["username"], user["role"])
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "full_name": user["full_name"],
            "role": user["role"]
        }
    }


# ── QR / ZLECENIA ─────────────────────────────────────────────────────────────

@app.get("/api/qr/{kod}")
def scan_qr(kod: str, user=Depends(get_current_user)):
    """Skanowanie QR — zwraca zlecenie z operacjami lub konkretną operację."""
    conn = get_conn()

    # Sprawdź czy to numer zlecenia
    zlecenie = conn.execute(
        "SELECT id, numer, nazwa, status FROM zlecenia WHERE numer=?", (kod,)
    ).fetchone()

    if zlecenie:
        ops = conn.execute("""
            SELECT id, nazwa, stanowisko, czas_norma, status, kolejnosc
            FROM operacje WHERE zlecenie_id=? ORDER BY kolejnosc
        """, (zlecenie["id"],)).fetchall()
        conn.close()
        return {
            "typ": "zlecenie",
            "zlecenie": dict(zlecenie),
            "operacje": [dict(op) for op in ops]
        }

    # Fallback: kod QR konkretnej operacji
    op = conn.execute("""
        SELECT o.id, o.nazwa, o.stanowisko, o.czas_norma, o.status, o.kolejnosc,
               z.id as zlecenie_id, z.numer as zl_numer, z.nazwa as zl_nazwa
        FROM operacje o JOIN zlecenia z ON o.zlecenie_id=z.id
        WHERE o.qr_code=? OR CAST(o.id AS TEXT)=?
    """, (kod, kod)).fetchone()
    conn.close()

    if not op:
        raise HTTPException(status_code=404, detail=f"Nie znaleziono kodu: {kod}")

    return {"typ": "operacja", "operacja": dict(op)}


# ── SESJE PRACY ───────────────────────────────────────────────────────────────

@app.post("/api/sesje/start")
def start_sesja(req: StartSesjaRequest, user=Depends(get_current_user)):
    """Rozpoczyna sesję pracy (odpowiednik START w panelu pracownika)."""
    conn = get_conn()

    # Sprawdź czy operacja istnieje
    op = conn.execute(
        "SELECT id, status, zlecenie_id FROM operacje WHERE id=?", (req.operacja_id,)
    ).fetchone()
    if not op:
        conn.close()
        raise HTTPException(status_code=404, detail="Operacja nie istnieje")

    # Sprawdź czy pracownik nie ma już aktywnej sesji dla tej operacji
    aktywna = conn.execute(
        "SELECT id FROM sesje_pracy WHERE operacja_id=? AND user_id=? AND status='aktywna'",
        (req.operacja_id, user["id"])
    ).fetchone()
    if aktywna:
        conn.close()
        raise HTTPException(status_code=400, detail="Masz już aktywną sesję dla tej operacji")

    now = datetime.datetime.now().isoformat()
    c = conn.cursor()
    c.execute("""
        INSERT INTO sesje_pracy (operacja_id, user_id, typ, start_time, status, pauzy)
        VALUES (?, ?, ?, ?, 'aktywna', '[]')
    """, (req.operacja_id, user["id"], req.typ, now))

    sesja_id = c.lastrowid

    # Aktualizuj status operacji i zlecenia
    c.execute("UPDATE operacje SET status='w_toku' WHERE id=?", (req.operacja_id,))
    c.execute("""
        UPDATE zlecenia SET status='w_toku'
        WHERE id=(SELECT zlecenie_id FROM operacje WHERE id=?)
    """, (req.operacja_id,))

    conn.commit()
    conn.close()

    return {"sesja_id": sesja_id, "start_time": now, "status": "aktywna"}


@app.post("/api/sesje/stop")
def stop_sesja(req: StopSesjaRequest, user=Depends(get_current_user)):
    """Kończy sesję pracy z podaniem ilości sztuk i uwag."""
    conn = get_conn()

    sesja = conn.execute(
        "SELECT id, operacja_id, start_time, pauzy FROM sesje_pracy WHERE id=? AND user_id=? AND status='aktywna'",
        (req.sesja_id, user["id"])
    ).fetchone()
    if not sesja:
        conn.close()
        raise HTTPException(status_code=404, detail="Aktywna sesja nie istnieje")

    now = datetime.datetime.now().isoformat()
    c = conn.cursor()

    c.execute("""
        UPDATE sesje_pracy
        SET end_time=?, ilosc_sztuk=?, uwagi=?, status='zakonczona'
        WHERE id=?
    """, (now, req.ilosc_sztuk, req.uwagi, req.sesja_id))

    # Oznacz operację jako zakończoną
    c.execute("UPDATE operacje SET status='zakonczona' WHERE id=?", (sesja["operacja_id"],))

    # Powiadomienie dla biura
    op_info = conn.execute("""
        SELECT o.nazwa, o.stanowisko, o.zlecenie_id, o.kolejnosc
        FROM operacje o WHERE o.id=?
    """, (sesja["operacja_id"],)).fetchone()

    if op_info:
        nxt = conn.execute("""
            SELECT nazwa FROM operacje
            WHERE zlecenie_id=? AND kolejnosc>? AND status='oczekuje'
            ORDER BY kolejnosc LIMIT 1
        """, (op_info["zlecenie_id"], op_info["kolejnosc"])).fetchone()

        nastepna = nxt["nazwa"] if nxt else ""
        tresc = f"{op_info['nazwa']}|{op_info['stanowisko'] or '—'}|{nastepna}"
        c.execute("""
            INSERT INTO powiadomienia (zlecenie_id, operacja_id, typ, tytul, tresc, dla_roli)
            VALUES (?, ?, 'operacja_zakonczona', 'Etap zakończony', ?, 'all')
        """, (op_info["zlecenie_id"], sesja["operacja_id"], tresc))

    conn.commit()
    conn.close()

    return {"status": "zakonczona", "end_time": now}


@app.post("/api/sesje/pauza")
def pauza_sesja(req: PauzaRequest, user=Depends(get_current_user)):
    """Start/stop pauzy w sesji."""
    conn = get_conn()

    sesja = conn.execute(
        "SELECT id, pauzy FROM sesje_pracy WHERE id=? AND user_id=? AND status='aktywna'",
        (req.sesja_id, user["id"])
    ).fetchone()
    if not sesja:
        conn.close()
        raise HTTPException(status_code=404, detail="Aktywna sesja nie istnieje")

    pauzy = json.loads(sesja["pauzy"] or "[]")
    now = datetime.datetime.now().isoformat()

    if req.akcja == "start":
        pauzy.append({"start": now, "sek": 0})
    elif req.akcja == "stop":
        if pauzy and "end" not in pauzy[-1]:
            start_p = datetime.datetime.fromisoformat(pauzy[-1]["start"])
            sek = (datetime.datetime.now() - start_p).total_seconds()
            pauzy[-1]["end"] = now
            pauzy[-1]["sek"] = int(sek)

    conn.execute(
        "UPDATE sesje_pracy SET pauzy=? WHERE id=?",
        (json.dumps(pauzy), req.sesja_id)
    )
    conn.commit()
    conn.close()

    return {"status": req.akcja, "pauzy_count": len(pauzy)}


@app.get("/api/sesje/aktywne")
def get_aktywne_sesje(user=Depends(get_current_user)):
    """Aktywne sesje bieżącego pracownika (po restarcie apki)."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT s.id, s.operacja_id, s.start_time, s.pauzy,
               o.nazwa as op_nazwa, o.stanowisko, o.czas_norma,
               z.numer as zl_numer, z.nazwa as zl_nazwa
        FROM sesje_pracy s
        JOIN operacje o ON s.operacja_id=o.id
        JOIN zlecenia z ON o.zlecenie_id=z.id
        WHERE s.user_id=? AND s.status='aktywna'
    """, (user["id"],)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── WYDAJNOŚĆ ─────────────────────────────────────────────────────────────────

@app.get("/api/wydajnosc")
def get_wydajnosc(
    dni: int = 7,
    user=Depends(get_current_user)
):
    """Panel wydajności pracownika — tylko własne dane."""
    od = (datetime.date.today() - datetime.timedelta(days=dni)).isoformat()
    conn = get_conn()

    rows = conn.execute("""
        SELECT s.id, s.start_time, s.end_time, s.pauzy,
               o.czas_norma, o.nazwa as op_nazwa, o.stanowisko,
               z.numer as zl_numer, z.nazwa as zl_nazwa,
               s.ilosc_sztuk, s.uwagi
        FROM sesje_pracy s
        JOIN operacje o ON s.operacja_id=o.id
        JOIN zlecenia z ON o.zlecenie_id=z.id
        WHERE s.user_id=? AND s.status='zakonczona'
          AND DATE(s.end_time) >= ?
        ORDER BY s.end_time DESC
        LIMIT 100
    """, (user["id"], od)).fetchall()
    conn.close()

    wyniki = []
    total_czas = 0
    total_sztuki = 0
    total_norma = 0

    for r in rows:
        if not r["start_time"] or not r["end_time"]:
            continue
        try:
            start = datetime.datetime.fromisoformat(r["start_time"])
            end = datetime.datetime.fromisoformat(r["end_time"])
        except ValueError:
            continue

        pauzy = json.loads(r["pauzy"] or "[]")
        pause_sek = sum(p.get("sek", 0) for p in pauzy)
        rzecz_h = max((end - start).total_seconds() / 3600 - pause_sek / 3600, 0)
        norma_h = (r["czas_norma"] or 0) * (r["ilosc_sztuk"] or 0) / 60

        wydajnosc = round(norma_h / rzecz_h * 100, 1) if rzecz_h > 0 and norma_h > 0 else None

        total_czas += rzecz_h
        total_sztuki += (r["ilosc_sztuk"] or 0)
        if norma_h > 0:
            total_norma += norma_h

        wyniki.append({
            "data": r["end_time"][:10],
            "op_nazwa": r["op_nazwa"],
            "stanowisko": r["stanowisko"] or "—",
            "zl_numer": r["zl_numer"],
            "ilosc_sztuk": r["ilosc_sztuk"] or 0,
            "czas_h": round(rzecz_h, 2),
            "norma_h": round(norma_h, 2),
            "wydajnosc": wydajnosc,
            "uwagi": r["uwagi"] or "",
        })

    srednia_wydajnosc = round(total_norma / total_czas * 100, 1) if total_czas > 0 and total_norma > 0 else None

    return {
        "sesje": wyniki,
        "podsumowanie": {
            "total_czas_h": round(total_czas, 2),
            "total_sztuki": total_sztuki,
            "srednia_wydajnosc": srednia_wydajnosc,
            "liczba_sesji": len(wyniki),
        }
    }


# ── POWIADOMIENIA ─────────────────────────────────────────────────────────────

@app.get("/api/powiadomienia")
def get_powiadomienia(user=Depends(get_current_user)):
    conn = get_conn()
    rows = conn.execute("""
        SELECT p.id, p.typ, p.tresc, p.created_at, p.odczytane,
               z.numer as zl_numer, z.nazwa as zl_nazwa
        FROM powiadomienia p
        LEFT JOIN zlecenia z ON p.zlecenie_id=z.id
        WHERE (p.dla_roli=? OR p.dla_roli='all') AND p.odczytane=0
        ORDER BY p.created_at DESC LIMIT 20
    """, (user["role"],)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
