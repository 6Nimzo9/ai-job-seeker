from db import get_connection


def save_job(job):
    """Simpan lowongan baru; abaikan URL yang sudah terdaftar."""

    query = """
        INSERT INTO jobs (
            title,
            company_id,
            location,
            country,
            employment_type,
            workplace_type,
            description,
            job_url,
            status
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)
    """

    with get_connection() as conn:
        cursor = conn.cursor()

        try:
            cursor.execute(
                query,
                (
                    job["title"],
                    job.get("company_id"),
                    job.get("location"),
                    job.get("country"),
                    job.get("employment_type"),
                    job.get("workplace_type"),
                    job.get("description"),
                    job["job_url"],
                    job.get("status", "new"),
                ),
            )

            job_id = cursor.lastrowid
            inserted = cursor.rowcount == 1

            conn.commit()

            return {
                "id": job_id,
                "inserted": inserted,
            }

        except Exception:
            conn.rollback()
            raise

        finally:
            cursor.close()


if __name__ == "__main__":
    # Data uji, bukan lowongan kerja sungguhan.
    test_job = {
        "title": "TEST - AI Automation Engineer",
        "location": "Remote",
        "country": "Australia",
        "employment_type": "Full-time",
        "workplace_type": "Remote",
        "description": "Data uji untuk memverifikasi penyimpanan MySQL.",
        "job_url": "https://example.com/test-ai-job-seeker",
    }

    result = save_job(test_job)

    print("ID lowongan:", result["id"])
    print(
        "Hasil:",
        "Lowongan baru disimpan"
        if result["inserted"]
        else "URL sudah terdaftar",
    )