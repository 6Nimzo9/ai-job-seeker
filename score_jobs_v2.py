"""Scoring AI Job Seeker versi revisi.

Kompatibel dengan struktur tabel proyek yang sudah ada.
Tidak membuat/menghapus tabel; hanya melakukan upsert pada job_matches.
"""

import json
import os
import re
from collections import Counter

from db import get_connection
from profile_settings import get_profile_settings


PROFILE_NAME = os.getenv("JOB_SEEKER_PROFILE_NAME", "Remote Automation Engineer")

TARGET_ROLES = [
    "AI Automation Engineer",
    "n8n Workflow Architect",
    "Python Automation Developer",
    "Business Process Automation",
    "Workflow Automation Engineer",
]

SKILLS = [
    "python",
    "n8n",
    "power automate",
    "sql",
    "api integration",
    "workflow automation",
    "business process automation",
    "artificial intelligence",
]

COUNTRY_PREFERENCES = ["Australia", "Singapore", "Worldwide remote"]
WORKPLACE_PREFERENCES = ["Remote"]

# Bobot total 100. Skor lokasi juga mengikuti urutan prioritas pengguna:
# Australia > Singapore > Worldwide > remote yang belum terverifikasi.
SKILL_WEIGHT = 0.35
ROLE_WEIGHT = 0.40
LOCATION_WEIGHT = 0.25

# Kecocokan jabatan menggunakan judul, bukan sekadar kata yang ada di deskripsi.
ROLE_RULES = {
    "ai automation engineer": 100,
    "workflow automation engineer": 100,
    "workflow automation developer": 100,
    "n8n workflow architect": 100,
    "python automation developer": 98,
    "python automation engineer": 98,
    "ai automation developer": 98,
    "ai automation specialist": 95,
    "business process automation": 95,
    "power automate engineer": 95,
    "power automate developer": 95,
    "n8n developer": 95,
    "intelligent automation": 92,
    "llm automation": 92,
    "automation architect": 92,
    "workflow architect": 90,
    "automation engineer": 88,
    "automation developer": 88,
    "automation specialist": 88,
    "automation consultant": 82,
    "python automation": 90,
    "workflow engineer": 90,
    "automation analyst": 78,
    "rpa developer": 78,
    "integration engineer": 65,
    "platform and integration engineer": 65,
    "ai enablement engineer": 68,
    "ai enablement": 60,
    "gtm automation": 60,
}

# Pekerjaan ini cenderung berada di jalur testing/QA, jaringan, atau security,
# bukan target utama workflow/business automation pengguna.
EXCLUDED_TITLE_PATTERNS = (
    r"\bqa\b",
    r"\bsdet\b",
    r"software development engineer in test",
    r"quality assurance",
    r"test automation",
    r"security telemetry",
    r"network systems automation",
    r"network automation",
)

SKILL_GROUPS = {
    "python": ("python", "python developer", "python programming"),
    "sql/database": ("sql", "mysql", "postgresql", "oracle database", "sql server"),
    "automation/workflow": ("automation", "automated workflow", "workflow automation", "process automation"),
    "integration/api": ("integration", "api integration", "system integration", "integrate systems", "rest api"),
    "n8n": ("n8n",),
    "power automate": ("power automate", "power platform"),
    "ai/llm": ("artificial intelligence", "generative ai", "large language model", "llm", "agentic ai"),
}

# Named locations outside the user's selected priorities. A country appearing
# only in a job description is not enough; this list is applied to the location field.
NON_PRIORITY_LOCATION_TERMS = (
    "united states", "united states of america", "usa", "u.s.", "us only",
    "canada", "united kingdom", "uk only", "germany", "europe only",
    "europe", "north america", "pakistan", "india", "armenia", "spain",
    "hong kong", "taiwan", "japan", "brazil", "mexico", "france",
    "poland", "portugal", "netherlands", "ireland", "israel", "philippines",
)

COUNTRY_RESTRICTION_PATTERNS = (
    r"\bremote\s*[-–]?\s*(?:u\.?s\.?|usa|united states)(?:\s+only)?\b",
    r"\b(?:u\.?s\.?|usa|united states|canada|united kingdom|uk|germany|europe|north america)\s+only\b",
    r"\b(?:must be based|must reside|must live|residents? of)\s+(?:in\s+)?(?:the\s+)?(?:u\.?s\.?|usa|united states|canada|united kingdom|uk|germany|spain|india|pakistan|australia|singapore)\b",
    r"\b(?:u\.?s\.?|usa|united states) citizens? only\b",
    r"\b(?:must be authorized|authorized|must have authorization) to work in (?:the )?(?:u\.?s\.?|usa|united states|canada|united kingdom|uk|germany|australia|singapore)\b",
    r"\b(?:right to work|work authorization required) in (?:the )?(?:u\.?s\.?|usa|united states|canada|united kingdom|uk|germany|australia|singapore)\b",
    r"\b(?:green card holders|ead holders)\b",
)

GLOBAL_LOCATION_TERMS = (
    "worldwide", "work from anywhere", "global remote", "any country",
    "remote worldwide", "open to all countries", "anywhere in the world",
    "international remote", "location unrestricted",
)

REMOTE_LOCATION_TERMS = (
    "remote", "distributed", "work from home", "remote-first", "remote first",
)

# Status rank for human review and reports.
STATUS_PRIORITY = {
    "australia_listed": 1,
    "singapore_listed": 2,
    "worldwide_claimed": 3,
    "remote_eligibility_unverified": 4,
    "location_restriction_review": 5,
    "location_not_matched": 6,
}


def normalize_text(value):
    """Lowercase text and remove HTML/extra whitespace for consistent matching."""
    value = str(value or "").lower()
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"&nbsp;|&#\d+;|&amp;", " ", value)
    value = re.sub(r"[^a-z0-9+#.\s-]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def contains_phrase(text, phrase):
    """Match a phrase without matching it as a substring of another word."""
    pattern = r"(?<![a-z0-9])" + re.escape(phrase.lower()) + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def get_or_create_profile(cursor):
    cursor.execute(
        "SELECT id FROM user_profile WHERE profile_name = %s LIMIT 1",
        (PROFILE_NAME,),
    )
    row = cursor.fetchone()
    if row:
        return row["id"]

    cursor.execute(
        """
        INSERT INTO user_profile (
            profile_name, target_roles, skills, preferred_countries,
            preferred_workplace_types, salary_currency
        ) VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            PROFILE_NAME,
            json.dumps(TARGET_ROLES),
            json.dumps(SKILLS),
            json.dumps(COUNTRY_PREFERENCES),
            json.dumps(WORKPLACE_PREFERENCES),
            "AUD",
        ),
    )
    return cursor.lastrowid



def get_role_score(title_value):
    title = normalize_text(title_value)

    if any(re.search(pattern, title) for pattern in EXCLUDED_TITLE_PATTERNS):
        return 0, [], "excluded_role_family"

    dynamic_roles = globals().get("DYNAMIC_TARGET_ROLES", [])
    if dynamic_roles:
        dynamic_matches = [role for role in dynamic_roles if contains_phrase(title, role)]
        if dynamic_matches:
            return 100, dynamic_matches, "dynamic_profile_title_match"
        # Do not fall back to old hard-coded role rules when a profile is configured.
        return 0, [], "no_dynamic_profile_title_match"

    matches = [
        (keyword, score)
        for keyword, score in ROLE_RULES.items()
        if contains_phrase(title, keyword)
    ]

    if matches:
        best_score = max(score for _, score in matches)
        best_matches = [
            keyword for keyword, score in matches
            if score == best_score
        ]
        return best_score, best_matches, "direct_title_match"

    # Peran terkait AI dan automation.
    has_automation = contains_phrase(title, "automation")
    has_ai = (
        contains_phrase(title, "ai")
        or contains_phrase(title, "agentic ai")
    )

    has_engineering_word = any(
        contains_phrase(title, term)
        for term in (
            "engineer",
            "developer",
            "architect",
            "analyst",
            "specialist",
            "consultant",
        )
    )

    if has_automation and has_ai and has_engineering_word:
        return 72, ["adjacent AI + automation role"], "adjacent_title_match"

    if (
        has_automation
        and contains_phrase(title, "marketing")
        and contains_phrase(title, "lead")
    ):
        return 65, ["marketing automation lead - manual review"], "adjacent_title_match"

    if has_automation and has_engineering_word:
        return 60, ["adjacent automation role"], "adjacent_title_match"

    if has_automation and contains_phrase(title, "manager"):
        return 45, ["automation management role"], "adjacent_title_match"

    return 0, [], "no_title_match"


def _timezone_offsets(location_raw):
    """Return the listed UTC offsets, or None when no timezone restriction is given."""
    match = re.search(r"timezone restrictions\s*:\s*(.*)$", str(location_raw or ""), re.IGNORECASE)
    if not match:
        return None
    values = re.findall(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?", match.group(1))
    if not values:
        return set()
    offsets = set()
    for value in values:
        try:
            offsets.add(round(float(value), 2))
        except ValueError:
            continue
    return offsets


def _has_explicit_country_restriction(text):
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in COUNTRY_RESTRICTION_PATTERNS)


def _contains_location_term(text, terms):
    normalized = normalize_text(text)
    return any(contains_phrase(normalized, term) for term in terms)


def classify_location(job):
    """Return (location_score, eligibility_status, explanation).

    The location field is evaluated separately from the job description to avoid
    false exclusions caused by a country being mentioned casually in the text.
    Indonesia is treated as UTC+7 for timezone restrictions.
    """
    location_raw = str(job.get("location") or "").strip()
    country_raw = str(job.get("country") or "").strip()
    description = str(job.get("description") or "")
    location_text = normalize_text(location_raw)
    country_text = normalize_text(country_raw)
    location_and_country = f"{location_text} {country_text}".strip()
    description_text = normalize_text(description)

    # Explicit country/work-rights restrictions override broad global wording.
    restriction_text = f"{job.get('title') or ''} {description}"
    if _has_explicit_country_restriction(restriction_text):
        return (
            0,
            "location_restriction_review",
            "Ada indikasi persyaratan negara/izin kerja tertentu; perlu verifikasi manual",
        )

    # Timezone restrictions are treated as an eligibility gate for a candidate in UTC+7.
    offsets = _timezone_offsets(location_raw)
    timezone_mismatch = offsets is not None and 7.0 not in offsets

    is_global = _contains_location_term(location_and_country, GLOBAL_LOCATION_TERMS) or contains_phrase(
        country_text, "international remote"
    )

    australia_terms = ("australia", "sydney", "melbourne", "brisbane", "perth", "adelaide", "canberra")
    has_australia = _contains_location_term(location_text, australia_terms) or contains_phrase(country_text, "australia")
    has_singapore = contains_phrase(location_text, "singapore") or contains_phrase(country_text, "singapore")

    # If a job lists several named jurisdictions and one is outside the preferred
    # countries, don't assume a candidate in Indonesia is eligible.
    non_priority_found = _contains_location_term(location_text, NON_PRIORITY_LOCATION_TERMS)
    if not is_global and non_priority_found:
        if has_australia or has_singapore:
            return (
                0,
                "location_restriction_review",
                "Lokasi mencantumkan beberapa yurisdiksi; kelayakan dari Indonesia belum jelas",
            )
        return (
            0,
            "location_not_matched",
            "Lokasi yang tercantum di luar prioritas Australia/Singapura/remote worldwide",
        )

    if is_global and non_priority_found:
        return (
            0,
            "location_restriction_review",
            "Lokasi mencampur klaim worldwide dengan negara tertentu; verifikasi cakupan negara",
        )

    if is_global:
        return (
            80,
            "worldwide_claimed",
            "Lokasi mengklaim cakupan global; pastikan pelamar dari Indonesia diterima",
        )

    if has_australia:
        reason = (
            "Australia tercantum; periksa syarat domisili dan izin kerja"
        )
        if timezone_mismatch:
            reason += "; zona waktu tidak mencakup UTC+7, verifikasi jam kerja"
        return (
            100,
            "australia_listed",
            reason,
        )

    if has_singapore:
        return (
            90,
            "singapore_listed",
            "Singapura menjadi prioritas kedua; periksa syarat domisili dan izin kerja",
        )

    dynamic_countries = globals().get("DYNAMIC_COUNTRIES", [])
    generic_location_labels = {
        "worldwide", "worldwide remote", "remote", "remote worldwide",
        "global", "anywhere", "international remote", "remote anywhere",
    }
    for index, preferred_country in enumerate(dynamic_countries):
        preferred_norm = normalize_text(preferred_country)
        if not preferred_norm or preferred_norm in generic_location_labels:
            continue
        if contains_phrase(location_and_country, preferred_norm):
            dynamic_score = max(50, 85 - index * 10)
            return (
                dynamic_score,
                "preferred_country_listed",
                f"{preferred_country} cocok dengan prioritas lokasi profil (urutan {index + 1}); izin kerja tetap perlu diverifikasi",
            )

    if _contains_location_term(location_and_country, REMOTE_LOCATION_TERMS):
        return (
            45,
            "remote_eligibility_unverified",
            "Remote terdeteksi tetapi negara yang diterima belum jelas",
        )

    return (
        0,
        "location_not_matched",
        "Lokasi tidak menunjukkan Australia, Singapura, atau remote worldwide yang jelas",
    )


def calculate_score(job):
    title = normalize_text(job.get("title"))
    description = normalize_text(job.get("description"))
    searchable = f"{title} {description}".strip()

    dynamic_skills = globals().get("DYNAMIC_SKILLS", [])
    if dynamic_skills:
        matched_skills = [
            skill for skill in dynamic_skills
            if contains_phrase(searchable, skill)
        ]
        skill_score = round(100 * len(matched_skills) / len(dynamic_skills), 2)
    else:
        matched_skills = [
            skill for skill, terms in SKILL_GROUPS.items()
            if any(contains_phrase(searchable, term) for term in terms)
        ]
        skill_score = round(100 * len(matched_skills) / len(SKILL_GROUPS), 2)

    role_score, matched_roles, role_match_type = get_role_score(job.get("title"))
    location_score, eligibility, location_reason = classify_location(job)

    overall = (
        skill_score * SKILL_WEIGHT
        + role_score * ROLE_WEIGHT
        + location_score * LOCATION_WEIGHT
    )

    role_reason = ", ".join(matched_roles) if matched_roles else "tidak cocok dengan judul target"
    reasons = [
        "Skill terdeteksi: " + (", ".join(matched_skills) if matched_skills else "belum terdeteksi"),
        f"Jabatan ({role_match_type}): {role_reason} (skor {role_score})",
        f"Lokasi: {location_reason}",
    ]

    return {
        "skill_score": round(skill_score, 2),
        "role_score": round(role_score, 2),
        "location_score": round(location_score, 2),
        "overall_score": round(overall, 2),
        "eligibility_status": eligibility,
        "reasoning": "; ".join(reasons),
    }


def main():
    with get_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            profile_id = get_or_create_profile(cursor)
            cursor.execute(
                """SELECT profile_name FROM user_profile WHERE id = %s LIMIT 1""",
                (profile_id,),
            )
            profile_row = cursor.fetchone() or {}
            profile_config = get_profile_settings(profile_row.get("profile_name") or PROFILE_NAME)
            globals()["DYNAMIC_TARGET_ROLES"] = profile_config["target_roles"]
            globals()["DYNAMIC_SKILLS"] = profile_config["skills"]
            globals()["DYNAMIC_COUNTRIES"] = profile_config["preferred_countries"]
            cursor.execute("SELECT id, title, description, location, country, job_url FROM jobs")
            jobs = cursor.fetchall()
            scored_jobs = []

            for job in jobs:
                scores = calculate_score(job)
                cursor.execute(
                    """
                    INSERT INTO job_matches (
                        job_id, profile_id, skill_score, role_score, location_score,
                        overall_score, eligibility_status, reasoning
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        skill_score = VALUES(skill_score),
                        role_score = VALUES(role_score),
                        location_score = VALUES(location_score),
                        overall_score = VALUES(overall_score),
                        eligibility_status = VALUES(eligibility_status),
                        reasoning = VALUES(reasoning),
                        scored_at = CURRENT_TIMESTAMP
                    """,
                    (
                        job["id"], profile_id, scores["skill_score"], scores["role_score"],
                        scores["location_score"], scores["overall_score"],
                        scores["eligibility_status"], scores["reasoning"],
                    ),
                )
                scored_jobs.append({**job, **scores})

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()

    counts = Counter(job["eligibility_status"] for job in scored_jobs)
    print("=" * 74)
    print("AI JOB SEEKER — SCORING REVISI")
    print("=" * 74)
    print(f"Total lowongan dinilai: {len(scored_jobs)}")
    print("\nRingkasan kelayakan lokasi:")
    for status, total in sorted(counts.items(), key=lambda item: STATUS_PRIORITY.get(item[0], 99)):
        print(f"  {status}: {total}")

    preferred_statuses = {"australia_listed", "singapore_listed", "worldwide_claimed"}
    preferred_jobs = [
        job for job in scored_jobs
        if job["eligibility_status"] in preferred_statuses and job["role_score"] >= 60
    ]
    preferred_jobs.sort(
        key=lambda job: (
            STATUS_PRIORITY.get(job["eligibility_status"], 99),
            -job["overall_score"],
            job["title"].lower(),
        )
    )

    print("\nLOWONGAN PRIORITAS UNTUK DITINJAU (Australia > Singapore > Worldwide)")
    if not preferred_jobs:
        print("  Belum ada lowongan yang memenuhi filter prioritas dan relevansi jabatan.")
        print("  Ini bukan error database; berarti perlu mengambil lebih banyak lowongan atau memperbaiki sumber/keyword.")
    else:
        for job in preferred_jobs[:20]:
            print(
                f"\n- {job['title']} | {job.get('country') or job.get('location') or 'Lokasi unknown'}"
                f"\n  Skor: {job['overall_score']:.2f}/100"
                f" | Role: {job['role_score']:.0f}"
                f" | Skill: {job['skill_score']:.2f}"
                f" | Lokasi: {job['location_score']:.0f}"
                f"\n  Status: {job['eligibility_status']}"
                f"\n  URL: {job.get('job_url') or '(URL tidak dimuat oleh query scoring)'}"
            )

    print("\nSelesai. Hasil scoring telah diperbarui pada tabel job_matches.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Scoring gagal: {type(exc).__name__}: {exc}")
        raise
