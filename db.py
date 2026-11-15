import os
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

# Load konfigurasi dari folder project
load_dotenv(Path(__file__).resolve().parent / ".env")


def get_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        database=os.getenv("DB_NAME", "jobseeker"),
        user=os.getenv("DB_USER", "jobseeker_app"),
        password=os.getenv("DB_PASSWORD"),
        connection_timeout=5,
    )


if __name__ == "__main__":
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT VERSION(), DATABASE(), CURRENT_USER()"
            )
            version, database, user = cursor.fetchone()

            print("Koneksi MySQL berhasil!")
            print(f"Versi MySQL : {version}")
            print(f"Database    : {database}")
            print(f"User        : {user}")

            cursor.close()

    except mysql.connector.Error as exc:
        print(f"Koneksi MySQL gagal: {exc}")