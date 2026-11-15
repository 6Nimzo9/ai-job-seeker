
import requests

API_URL = "https://remotive.com/api/remote-jobs"
TIMEOUT = 30


def fetch_remotive_jobs():
    """
    Mengambil daftar lowongan Remotive satu kali,
    kemudian menyerahkan penyaringan relevansi ke discover_jobs.py.
    """

    headers = {
        "User-Agent": "AIJobSeeker/1.0"
    }

    try:
        response = requests.get(
            API_URL,
            headers=headers,
            timeout=TIMEOUT
        )
        response.raise_for_status()
        payload = response.json()

    except requests.RequestException as exc:
        print(f"[Remotive] Gagal mengambil lowongan: {exc}")
        return []

    except ValueError as exc:
        print(f"[Remotive] Respons JSON tidak valid: {exc}")
        return []

    raw_jobs = payload.get("jobs", [])

    if not isinstance(raw_jobs, list):
        print("[Remotive] Format respons tidak sesuai.")
        return []

    jobs = []
    seen_ids = set()

    for item in raw_jobs:
        if not isinstance(item, dict):
            continue

        job_id = item.get("id")
        job_url = item.get("url")

        if not job_id or not job_url:
            continue

        unique_id = str(job_id)

        if unique_id in seen_ids:
            continue

        seen_ids.add(unique_id)

        category = item.get("category") or ""
        job_type = item.get("job_type") or ""

        jobs.append({
            "id": unique_id,
            "position": item.get("title") or "",
            "company": item.get("company_name") or "",
            "company_url": None,
            "url": job_url,
            "location": (
                item.get("candidate_required_location") or ""
            ),
            "tags": [category, job_type],
            "description": item.get("description") or "",
            "date": item.get("publication_date"),
            "closing_date": next((item.get(k) for k in ("closing_date", "closingDate", "close_date", "closeDate", "application_deadline", "applicationDeadline", "deadline", "expires_at", "expiresAt", "expiryDate", "validThrough") if item.get(k)), None),
            "source_name": "Remotive",
            "source_url": API_URL,
        })

    print(
        f"[Remotive] Total lowongan dari API: "
        f"{len(raw_jobs)}"
    )
    print(
        f"[Remotive] Lowongan valid dan unik: "
        f"{len(jobs)}"
    )

    return jobs
