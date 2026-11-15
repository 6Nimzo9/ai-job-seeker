"""Shared, database-backed AI Job Seeker profile settings."""
from __future__ import annotations
import json
import os
from db import get_connection

DEFAULTS = {
    "profile_name": os.getenv("JOB_SEEKER_PROFILE_NAME", "Remote Automation Engineer"),
    "target_roles": [
        "AI Automation Engineer", "n8n Workflow Architect",
        "Python Automation Developer", "Business Process Automation",
        "Workflow Automation Engineer",
    ],
    "skills": ["python", "n8n", "power automate", "sql", "api integration",
               "workflow automation", "business process automation", "artificial intelligence"],
    "preferred_countries": ["Australia", "Singapore", "Worldwide remote"],
    "preferred_workplace_types": ["Remote"],
    "minimum_salary": "",
    "salary_currency": "AUD",
    "cv_path": r"C:\AIJobSeeker\cv\CV_English.pdf",
}

def parse_list(value, fallback):
    if isinstance(value, list):
        values = value
    else:
        try:
            values = json.loads(value or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            values = []
    clean = []
    for item in values if isinstance(values, list) else []:
        item = str(item).strip()
        if item and item not in clean:
            clean.append(item[:120])
    return clean or list(fallback)

def normalize_profile(row):
    row = dict(row or {})
    return {
        "id": row.get("id"),
        "profile_name": str(row.get("profile_name") or DEFAULTS["profile_name"]),
        "target_roles": parse_list(row.get("target_roles"), DEFAULTS["target_roles"]),
        "skills": parse_list(row.get("skills"), DEFAULTS["skills"]),
        "preferred_countries": parse_list(row.get("preferred_countries"), DEFAULTS["preferred_countries"]),
        "preferred_workplace_types": parse_list(row.get("preferred_workplace_types"), DEFAULTS["preferred_workplace_types"]),
        "minimum_salary": "" if row.get("minimum_salary") is None else str(row.get("minimum_salary")),
        "salary_currency": str(row.get("salary_currency") or DEFAULTS["salary_currency"]).upper()[:8],
        "cv_path": str(row.get("cv_path") or DEFAULTS["cv_path"]),
    }

def get_profile_settings(profile_name=None):
    profile_name = profile_name or DEFAULTS["profile_name"]
    conn = get_connection()
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT id, profile_name, target_roles, skills, preferred_countries,
                      preferred_workplace_types, minimum_salary, salary_currency, cv_path
               FROM user_profile WHERE profile_name = %s LIMIT 1""",
            (profile_name,),
        )
        row = cur.fetchone()
        if not row:
            raise LookupError(f"Profil '{profile_name}' tidak ditemukan di user_profile.")
        return normalize_profile(row)
    finally:
        if cur is not None: cur.close()
        conn.close()

def save_profile_settings(profile_id, values):
    conn = get_connection()
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id FROM user_profile WHERE id = %s FOR UPDATE", (profile_id,))
        if not cur.fetchone():
            raise LookupError("Profil tidak ditemukan; pengaturan tidak disimpan.")
        cur.execute(
            """UPDATE user_profile
               SET profile_name=%s, target_roles=%s, skills=%s,
                   preferred_countries=%s, preferred_workplace_types=%s,
                   minimum_salary=%s, salary_currency=%s, cv_path=%s
               WHERE id=%s""",
            (
                values["profile_name"],
                json.dumps(values["target_roles"], ensure_ascii=False),
                json.dumps(values["skills"], ensure_ascii=False),
                json.dumps(values["preferred_countries"], ensure_ascii=False),
                json.dumps(values["preferred_workplace_types"], ensure_ascii=False),
                values["minimum_salary"] if values["minimum_salary"] != "" else None,
                values["salary_currency"],
                values["cv_path"],
                profile_id,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if cur is not None: cur.close()
        conn.close()
