import requests

API_URL = "https://himalayas.app/jobs/api/search"
TIMEOUT = 30

# Expanded but still focused search coverage for the existing target roles.
# Results are deduplicated by the Himalayas job GUID below.
SEARCH_TERMS = (
    "AI automation",
    "AI automation specialist",
    "AI and workflow automation specialist",
    "AI workflow automation",
    "workflow automation",
    "automation developer",
    "n8n",
    "Power Automate",
    "Python automation",
    "application integration",
    "integration engineer",
    "process automation",
)

# Fetch deeper result pages and add more targeted phrases. Results remain
# deduplicated by Himalayas GUID before being passed to the existing pipeline.
MAX_PAGES_PER_TERM = 3

def fetch_himalayas_jobs():
    """Cari lowongan terarah dari Himalayas dan hapus duplikat."""

    headers = {"User-Agent": "AIJobSeeker/1.0"}
    jobs_by_id = {}

    for term in SEARCH_TERMS:
        print(f"[Himalayas] Mencari: {term}")

        for page in range(1, MAX_PAGES_PER_TERM + 1):
            try:
                response = requests.get(
                    API_URL,
                    params={
                        "q": term,
                        "sort": "recent",
                        "page": page,
                    },
                    headers=headers,
                    timeout=TIMEOUT,
                )
                response.raise_for_status()
                payload = response.json()

            except requests.RequestException as exc:
                print(
                    f"[Himalayas] Gagal mencari '{term}', "
                    f"halaman {page}: {exc}"
                )
                break

            except ValueError as exc:
                print(
                    f"[Himalayas] JSON tidak valid "
                    f"untuk '{term}': {exc}"
                )
                break

            page_jobs = payload.get("jobs", [])

            if not isinstance(page_jobs, list) or not page_jobs:
                break

            for item in page_jobs:
                if not isinstance(item, dict):
                    continue

                job_id = item.get("guid")
                job_url = item.get("applicationLink")

                if not job_id or not job_url:
                    continue

                restrictions = item.get("locationRestrictions")
                timezone_restrictions = item.get("timezoneRestrictions")

                if isinstance(restrictions, list):
                    if not restrictions:
                        # Daftar kosong berarti tidak ada batasan negara.
                        location = "Worldwide"
                    else:
                        location_names = []

                        for country in restrictions:
                            if isinstance(country, dict):
                                name = (
                                    country.get("name")
                                    or country.get("alpha2")
                                    or country.get("slug")
                                )
                            elif isinstance(country, str):
                                name = country
                            else:
                                name = None

                            if name:
                                location_names.append(str(name))

                        location = (
                            ", ".join(location_names)
                            if location_names
                            else "Location restrictions present but names unavailable"
                        )

                elif isinstance(restrictions, str) and restrictions.strip():
                    location = restrictions.strip()

                else:
                    # Informasi tidak tersedia bukan berarti Worldwide.
                    location = "Location restrictions unspecified"

                if isinstance(timezone_restrictions, list):
                    timezone_names = [
                        str(zone.get("name", ""))
                        if isinstance(zone, dict)
                        else str(zone)
                        for zone in timezone_restrictions
                        if zone
                    ]
                    timezone_text = ", ".join(timezone_names)
                elif isinstance(timezone_restrictions, str):
                    timezone_text = timezone_restrictions.strip()
                else:
                    timezone_text = ""

                title_lower = str(item.get("title") or "").lower()
                description_lower = str(
                    item.get("description") or ""
                ).lower()

                if any(term in title_lower for term in (
                    "remote - u.s.",
                    "remote us",
                    "remote usa",
                    "united states only",
                )):
                    location += "; Title indicates U.S. restriction"

                elif any(term in description_lower for term in (
                    "100% remote (u.s.)",
                    "remote - u.s.",
                    "remote (u.s.)",
                    "united states only",
                    "must be based in the us",
                    "must reside in the us",
                )):
                    location = (
                        "United States; "
                        "Description indicates U.S. restriction"
                    )

                if timezone_text:
                    location += f"; Timezone restrictions: {timezone_text}"

                categories = item.get("categories") or []
                parent_categories = item.get("parentCategories") or []

                tags = []
                for tag in categories + parent_categories:
                    if tag and tag not in tags:
                        tags.append(tag)

                if item.get("employmentType"):
                    tags.append(item["employmentType"])

                if item.get("seniority"):
                    seniority = item["seniority"]
                    if isinstance(seniority, list):
                        tags.extend(seniority)
                    else:
                        tags.append(str(seniority))

                jobs_by_id[str(job_id)] = {
                    "id": str(job_id),
                    "position": item.get("title") or "",
                    "company": item.get("companyName") or "",
                    "company_url": None,
                    "url": job_url,
                    "location": location,
                    "tags": tags,
                    "description": item.get("description") or "",
                    "date": item.get("pubDate"),
                    "closing_date": next((item.get(k) for k in ("closing_date", "closingDate", "close_date", "closeDate", "application_deadline", "applicationDeadline", "deadline", "expires_at", "expiresAt", "expiryDate", "validThrough") if item.get(k)), None),
                    "source_name": "Himalayas",
                    "source_url": "https://himalayas.app",
                }

    results = list(jobs_by_id.values())

    print(
        f"[Himalayas] Total lowongan unik: {len(results)}"
    )

    return results
