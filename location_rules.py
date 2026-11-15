
import html
import re


# Lokasi global tetap perlu diverifikasi.
GLOBAL_TERMS = (
    "worldwide",
    "work from anywhere",
    "global remote",
    "anywhere in the world",
    "international remote",
    "open to all countries",
    "any country",
)

# Indikasi pembatasan negara atau izin kerja.
COUNTRY_RESTRICTIONS = {
    "United States": (
        "100% remote (u.s.)",
        "remote (u.s.)",
        "remote - u.s.",
        "remote u.s.",
        "remote - us",
        "remote us",
        "remote usa",
        "united states only",
        "u.s. only",
        "us only",
        "usa only",
        "must be based in the us",
        "must be based in the united states",
        "must reside in the us",
        "must reside in the united states",
        "must live in the us",
        "must be authorized to work in the us",
        "must be authorized to work in the united states",
        "us work authorization required",
        "u.s. citizens",
        "us citizens",
        "green card holders",
        "ead holders",
        "h-1b transfer candidates",
        "title indicates u.s. restriction",
        "description indicates u.s. restriction",
    ),
    "Canada": (
        "canada only",
        "must be based in canada",
        "must reside in canada",
        "canadian work authorization required",
    ),
    "United Kingdom": (
        "uk only",
        "united kingdom only",
        "must be based in the uk",
        "must reside in the uk",
    ),
    "Germany": (
        "germany only",
        "must be based in germany",
    ),
}

# Indikasi pembatasan yang negara tujuannya belum tentu diketahui.
GENERIC_RESTRICTIONS = (
    "must be based in",
    "must reside in",
    "must live in",
    "residents of",
    "right to work in",
    "work authorization required",
    "relocation required",
    "office attendance required",
    "monthly onsite",
    "monthly on-site",
    "monthly in-person",
)

COUNTRY_ALIASES = {
    "Australia": ("australia", "sydney", "melbourne", "brisbane", "perth"),
    "Singapore": ("singapore",),
    "United States": ("united states", "u.s.", "usa", "us"),
    "Canada": ("canada",),
    "United Kingdom": ("united kingdom", "uk", "britain"),
    "Germany": ("germany",),
    "India": ("india",),
    "Pakistan": ("pakistan",),
    "Philippines": ("philippines", "the philippines"),
    "Colombia": ("colombia",),
    "Ireland": ("ireland",),
    "Spain": ("spain",),
    "France": ("france",),
    "Italy": ("italy",),
    "Netherlands": ("netherlands",),
    "New Zealand": ("new zealand",),
    "Indonesia": ("indonesia",),
    "Malaysia": ("malaysia",),
    "Japan": ("japan",),
    "Brazil": ("brazil",),
    "Mexico": ("mexico",),
    "South Africa": ("south africa",),
}


def normalize_text(value):
    """Bersihkan HTML dan normalisasi teks untuk pencocokan."""
    value = html.unescape(str(value or ""))
    value = re.sub(r"<[^>]*>", " ", value)
    return re.sub(r"\s+", " ", value).strip().lower()


def contains_phrase(text, phrase):
    """Cocokkan frasa tanpa mencocokkan bagian dari kata lain."""
    text = normalize_text(text)
    phrase = normalize_text(phrase)

    if not text or not phrase:
        return False

    pattern = (
        r"(?<![a-z0-9])"
        + re.escape(phrase)
        + r"(?![a-z0-9])"
    )
    return re.search(pattern, text) is not None


def infer_country(location):
    """Ambil negara atau cakupan lokasi dari kolom lokasi."""
    text = normalize_text(location)

    if not text:
        return None

    first_part = text.split(";", 1)[0].strip()

    if any(contains_phrase(first_part, term) for term in GLOBAL_TERMS):
        return "International Remote"

    if (
        "location restrictions unspecified" in first_part
        or "location restrictions present but names unavailable"
        in first_part
        or first_part in ("remote", "unknown", "not specified")
    ):
        return None

    for country, aliases in COUNTRY_ALIASES.items():
        if any(contains_phrase(first_part, alias) for alias in aliases):
            return country

    # Untuk nama lokasi yang belum ada di daftar alias.
    return first_part.title()


def detect_restriction(title="", location="", description=""):
    """
    Periksa indikasi pembatasan pada judul, lokasi, dan deskripsi.
    Hasilnya adalah indikasi untuk ditinjau, bukan keputusan hukum.
    """
    fields = (
        ("title", title),
        ("location", location),
        ("description", description),
    )

    for field_name, value in fields:
        text = normalize_text(value)

        for country, phrases in COUNTRY_RESTRICTIONS.items():
            if any(contains_phrase(text, phrase) for phrase in phrases):
                return {
                    "found": True,
                    "country": country,
                    "field": field_name,
                }

        if any(
            contains_phrase(text, phrase)
            for phrase in GENERIC_RESTRICTIONS
        ):
            return {
                "found": True,
                "country": infer_country(location),
                "field": field_name,
            }

    return {
        "found": False,
        "country": None,
        "field": None,
    }


def normalize_location_fields(location="", title="", description=""):
    """
    Normalisasi lokasi dan negara sebelum disimpan.
    Pembatasan eksplisit dapat mengalahkan label Worldwide.
    """
    location = str(location or "").strip()
    restriction = detect_restriction(title, location, description)
    current_country = infer_country(location)

    if restriction["found"]:
        restricted_country = (
            restriction["country"] or current_country
        )
        field_label = {
            "title": "Title",
            "location": "Location",
            "description": "Description",
        }.get(restriction["field"], "Listing")

        if restricted_country and (
            current_country is None
            or current_country == "International Remote"
        ):
            location = (
                f"{restricted_country}; "
                f"{field_label} indicates location/work restriction"
            )
        elif "restriction" not in normalize_text(location):
            location = (
                f"{location}; "
                f"{field_label} indicates location/work restriction"
            ).strip("; ")

        return location or "Location restrictions unspecified", restricted_country

    return (
        location or "Location restrictions unspecified",
        current_country,
    )


def assess_location(location="", country="", title="", description=""):
    """
    Berikan skor dan status lokasi secara konsisten.
    Skor bukan jaminan bahwa kandidat Indonesia memenuhi syarat.
    """
    restriction = detect_restriction(title, location, description)

    if restriction["found"]:
        return {
            "location_score": 0,
            "eligibility_status": "location_restriction_review",
            "location_reason": (
                "Ada indikasi pembatasan negara atau izin kerja; "
                "perlu verifikasi"
            ),
        }

    location_text = normalize_text(location)
    country_text = normalize_text(country)

    if (
        not location_text
        or "location restrictions unspecified" in location_text
        or "location restrictions present but names unavailable"
        in location_text
    ):
        return {
            "location_score": 0,
            "eligibility_status": "review_required",
            "location_reason": "Informasi lokasi belum tersedia",
        }

    if (
        contains_phrase(location_text, "australia")
        or contains_phrase(country_text, "australia")
    ):
        return {
            "location_score": 60,
            "eligibility_status": "australia_listed",
            "location_reason": (
                "Australia tercantum; kelayakan kandidat Indonesia "
                "tetap perlu diverifikasi"
            ),
        }

    if (
        contains_phrase(location_text, "singapore")
        or contains_phrase(country_text, "singapore")
    ):
        return {
            "location_score": 60,
            "eligibility_status": "singapore_listed",
            "location_reason": (
                "Singapura tercantum; kelayakan kandidat Indonesia "
                "tetap perlu diverifikasi"
            ),
        }

    if (
        country_text == "international remote"
        or any(
            contains_phrase(location_text, term)
            for term in GLOBAL_TERMS
        )
    ):
        return {
            "location_score": 60,
            "eligibility_status": "worldwide_claimed",
            "location_reason": (
                "Cakupan global diklaim; penerimaan kandidat "
                "Indonesia belum terkonfirmasi"
            ),
        }

    if any(
        contains_phrase(location_text, term)
        for term in ("remote", "distributed team", "work from home")
    ):
        return {
            "location_score": 40,
            "eligibility_status": "remote_eligibility_unverified",
            "location_reason": (
                "Remote terdeteksi; negara yang diizinkan "
                "belum terverifikasi"
            ),
        }

    return {
        "location_score": 0,
        "eligibility_status": "location_not_matched",
        "location_reason": (
            "Lokasi belum cocok dengan preferensi yang ditetapkan"
        ),
    }
