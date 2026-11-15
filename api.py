
import hmac
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

API_TOKEN = os.getenv("JOBSEEKER_API_TOKEN", "")
app = Flask(__name__)

# One shared lock across both n8n workflows (manual and scheduled).
# /discover acquires it and /score releases it, preventing overlapping pipelines
# from racing while they read/write the same MySQL jobs and job_matches tables.
_PIPELINE_LOCK = threading.Lock()
_PIPELINE_STATE_GUARD = threading.Lock()
_PIPELINE_STATE = None
_PIPELINE_STALE_AFTER_SECONDS = 900


def _expire_stale_pipeline_locked():
    """Release a lock left behind when a workflow stops before calling /score."""
    global _PIPELINE_STATE
    if _PIPELINE_STATE and (time.monotonic() - _PIPELINE_STATE["updated_at"]) > _PIPELINE_STALE_AFTER_SECONDS:
        _PIPELINE_STATE = None
        if _PIPELINE_LOCK.locked():
            _PIPELINE_LOCK.release()


def _begin_discovery_pipeline():
    global _PIPELINE_STATE
    with _PIPELINE_STATE_GUARD:
        _expire_stale_pipeline_locked()
        if _PIPELINE_STATE is not None or not _PIPELINE_LOCK.acquire(blocking=False):
            return False
        _PIPELINE_STATE = {"stage": "discovering", "updated_at": time.monotonic()}
        return True


def _finish_discovery_pipeline(success):
    global _PIPELINE_STATE
    with _PIPELINE_STATE_GUARD:
        if not _PIPELINE_STATE or _PIPELINE_STATE.get("stage") != "discovering":
            return
        if success:
            _PIPELINE_STATE = {"stage": "awaiting_score", "updated_at": time.monotonic()}
        else:
            _PIPELINE_STATE = None
            if _PIPELINE_LOCK.locked():
                _PIPELINE_LOCK.release()


def _begin_scoring():
    global _PIPELINE_STATE
    with _PIPELINE_STATE_GUARD:
        _expire_stale_pipeline_locked()
        if _PIPELINE_STATE is not None:
            if _PIPELINE_STATE.get("stage") != "awaiting_score":
                return False
            _PIPELINE_STATE = {"stage": "scoring", "updated_at": time.monotonic()}
            return True
        # Permit a standalone /score call, but do not let it overlap discovery.
        if not _PIPELINE_LOCK.acquire(blocking=False):
            return False
        _PIPELINE_STATE = {"stage": "scoring_standalone", "updated_at": time.monotonic()}
        return True


def _finish_scoring():
    global _PIPELINE_STATE
    with _PIPELINE_STATE_GUARD:
        if _PIPELINE_STATE and _PIPELINE_STATE.get("stage") in {"scoring", "scoring_standalone"}:
            _PIPELINE_STATE = None
            if _PIPELINE_LOCK.locked():
                _PIPELINE_LOCK.release()


@app.before_request
def authenticate():
    if not API_TOKEN:
        return jsonify({"error": "API token belum dikonfigurasi"}), 503

    supplied = request.headers.get("X-API-Key", "")
    if not hmac.compare_digest(supplied, API_TOKEN):
        return jsonify({"error": "Unauthorized"}), 401


def run_script(filename):
    result = subprocess.run(
        [sys.executable, str(BASE_DIR / filename)],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8:backslashreplace"},
    )

    return {
        "success": result.returncode == 0,
        "output": result.stdout[-5000:],
        "error": result.stderr[-2000:],
    }, (200 if result.returncode == 0 else 500)


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "AI Job Seeker"})


@app.post("/discover")
def discover():
    if not _begin_discovery_pipeline():
        return jsonify({
            "success": False,
            "error": "Discovery/scoring pipeline lain masih berjalan. Tunggu sampai workflow sebelumnya selesai.",
        }), 409

    try:
        result, status = run_script("discover_jobs.py")
        _finish_discovery_pipeline(success=(status == 200))
        return jsonify(result), status
    except Exception:
        _finish_discovery_pipeline(success=False)
        raise


@app.post("/score")
def score():
    if not _begin_scoring():
        return jsonify({
            "success": False,
            "error": "Pipeline discovery lain sedang berjalan atau tahap scoring belum siap. Coba setelah workflow aktif selesai.",
        }), 409

    try:
        result, status = run_script("score_jobs_v2.py")
        return jsonify(result), status
    finally:
        _finish_scoring()

# --- AI Job Seeker email auto-apply endpoint (installed by install_auto_apply.py) ---
@app.route("/auto-apply", methods=["POST"])
def ai_job_seeker_auto_apply_route():
    import hmac
    import os
    from flask import jsonify, request
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    expected = os.getenv("JOBSEEKER_API_TOKEN", "")
    supplied = request.headers.get("X-API-Key", "")
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        return jsonify({"ok": False, "error": "Unauthorized: X-API-Key is invalid or missing."}), 401
    from auto_apply_email import run_auto_apply
    result = run_auto_apply()
    return jsonify(result), (200 if result.get("ok") else 500)
# --- end AI Job Seeker email auto-apply endpoint ---


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
