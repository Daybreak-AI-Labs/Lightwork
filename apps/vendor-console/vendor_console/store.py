"""Repository layer — thin, explicit SQL over the SQLite rows. No ORM, no magic;
every query is visible so an internal tool holding signing keys is easy to audit.
"""
from __future__ import annotations

import json
import sqlite3
import time

from .models import Customer, License, Staff

# ---- staff -----------------------------------------------------------------

def create_staff(conn: sqlite3.Connection, *, email: str, name: str, role: str,
                 pw_hash: str, totp_secret: str = "", at: float | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO staff(email, name, role, pw_hash, totp_secret, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (email.strip().lower(), name, role, pw_hash, totp_secret,
         at if at is not None else time.time()))
    conn.commit()
    return cur.lastrowid


def get_staff_by_email(conn: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM staff WHERE email = ?",
                        (email.strip().lower(),)).fetchone()


def get_staff(conn: sqlite3.Connection, staff_id: int) -> Staff | None:
    r = conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
    return Staff.from_row(r) if r else None


def get_staff_row(conn: sqlite3.Connection, staff_id: int) -> sqlite3.Row | None:
    """Raw row incl. secrets (pw_hash, totp_secret) — for the auth flow only."""
    return conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()


def list_staff(conn: sqlite3.Connection) -> list[Staff]:
    return [Staff.from_row(r) for r in
            conn.execute("SELECT * FROM staff ORDER BY email").fetchall()]


def count_staff(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM staff").fetchone()["n"]


def set_totp(conn: sqlite3.Connection, staff_id: int, secret: str, enrolled: bool) -> None:
    conn.execute("UPDATE staff SET totp_secret = ?, totp_enrolled = ? WHERE id = ?",
                 (secret, 1 if enrolled else 0, staff_id))
    conn.commit()


# ---- customers -------------------------------------------------------------

def create_customer(conn: sqlite3.Connection, *, name: str, primary_contact: str = "",
                    contact_email: str = "", posture: str = "connected",
                    status: str = "trial", notes: str = "",
                    serve_token_hash: str = "", at: float | None = None) -> int:
    now = at if at is not None else time.time()
    cur = conn.execute(
        "INSERT INTO customers(name, primary_contact, contact_email, posture, status, "
        "notes, serve_token_hash, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (name.strip(), primary_contact, contact_email, posture, status, notes,
         serve_token_hash, now, now))
    conn.commit()
    return cur.lastrowid


def get_customer(conn: sqlite3.Connection, customer_id: int) -> Customer | None:
    r = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    return Customer.from_row(r) if r else None


def list_customers(conn: sqlite3.Connection, *, search: str = "") -> list[Customer]:
    if search.strip():
        rows = conn.execute(
            "SELECT * FROM customers WHERE name LIKE ? OR contact_email LIKE ? "
            "ORDER BY name", (f"%{search}%", f"%{search}%")).fetchall()
    else:
        rows = conn.execute("SELECT * FROM customers ORDER BY name").fetchall()
    return [Customer.from_row(r) for r in rows]


def update_customer(conn: sqlite3.Connection, customer_id: int, **fields) -> None:
    allowed = {"primary_contact", "contact_email", "posture", "status", "notes",
               "serve_token_hash"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    cols = ", ".join(f"{k} = ?" for k in sets)
    conn.execute(f"UPDATE customers SET {cols}, updated_at = ? WHERE id = ?",
                 (*sets.values(), time.time(), customer_id))
    conn.commit()


def customer_by_serve_token_hash(conn: sqlite3.Connection, token_hash: str) -> Customer | None:
    if not token_hash:
        return None
    r = conn.execute("SELECT * FROM customers WHERE serve_token_hash = ?",
                     (token_hash,)).fetchone()
    return Customer.from_row(r) if r else None


# ---- licenses --------------------------------------------------------------

def add_license(conn: sqlite3.Connection, *, customer_id: int, license_id: str,
                tier: str, suites: list[str], features: list[str], seats: int | None,
                issued_at: str, expires_at: str | None, grace_days: int, key_id: str,
                doc: dict, note: str, created_by: str, at: float | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO licenses(customer_id, license_id, tier, suites_json, features_json, "
        "seats, issued_at, expires_at, grace_days, key_id, doc_json, note, created_by, "
        "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (customer_id, license_id, tier, json.dumps(suites), json.dumps(features),
         seats, issued_at, expires_at, grace_days, key_id, json.dumps(doc), note,
         created_by, at if at is not None else time.time()))
    conn.commit()
    return cur.lastrowid


def get_license(conn: sqlite3.Connection, license_id: str) -> License | None:
    r = conn.execute("SELECT * FROM licenses WHERE license_id = ?",
                     (license_id,)).fetchone()
    return License.from_row(r) if r else None


def list_licenses(conn: sqlite3.Connection, customer_id: int) -> list[License]:
    rows = conn.execute(
        "SELECT * FROM licenses WHERE customer_id = ? ORDER BY id DESC",
        (customer_id,)).fetchall()
    return [License.from_row(r) for r in rows]


def revoke_license(conn: sqlite3.Connection, license_id: str, *, note: str = "",
                   at: float | None = None) -> bool:
    cur = conn.execute(
        "UPDATE licenses SET revoked_at = ?, note = ? WHERE license_id = ? "
        "AND revoked_at IS NULL",
        (at if at is not None else time.time(), note, license_id))
    conn.commit()
    return cur.rowcount > 0


# ---- fleet check-ins -------------------------------------------------------

def record_checkin(conn: sqlite3.Connection, *, customer_id: int, deployment_id: str = "",
                   version: str = "", license_status: str = "", ip: str = "",
                   at: float | None = None) -> None:
    conn.execute(
        "INSERT INTO checkins(customer_id, deployment_id, version, license_status, ip, at) "
        "VALUES (?,?,?,?,?,?)",
        (customer_id, deployment_id, version, license_status, ip,
         at if at is not None else time.time()))
    conn.commit()


def latest_checkins(conn: sqlite3.Connection, *, limit: int = 500) -> list[dict]:
    """One row per customer: their most recent check-in (fleet health board)."""
    rows = conn.execute(
        "SELECT c.name AS customer, k.* FROM checkins k "
        "JOIN customers c ON c.id = k.customer_id "
        "JOIN (SELECT customer_id, MAX(at) AS mx FROM checkins GROUP BY customer_id) t "
        "  ON t.customer_id = k.customer_id AND t.mx = k.at "
        "ORDER BY k.at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
