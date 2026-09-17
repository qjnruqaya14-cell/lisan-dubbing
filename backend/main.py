"""
main.py — سيرفر الباك إند الكامل لمنصة الدبلجة والترجمة.

يشغّل: رفع فيديو، معالجته بخط الأنابيب بخيط خلفي، تتبع تقدم كل مهمة،
تنزيل ومشاركة الفيديو الناتج عبر رابط عام.

تشغيل محلي:
    uvicorn main:app --host 0.0.0.0 --port 8000
"""

import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from languages import LANGUAGES, get_language
from pipeline import PipelineError, run_pipeline

load_dotenv()

BASE_DIR = Path(__file__).parent
STORAGE_DIR = BASE_DIR / "storage"
UPLOADS_DIR = STORAGE_DIR / "uploads"
OUTPUTS_DIR = STORAGE_DIR / "outputs"
JOBS_FILE = STORAGE_DIR / "jobs.json"
FRONTEND_DIR = BASE_DIR.parent / "frontend"

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY") or None
LIBRETRANSLATE_URL = os.getenv("LIBRETRANSLATE_URL") or None
DEFAULT_VOICE_ID = os.getenv("DEFAULT_VOICE_ID", "")
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "300"))

# معالجة مهمة وحدة بوقت واحد بالإصدار الافتراضي: تفريغ الصوت (Whisper) عملية
# ثقيلة على المعالج/الذاكرة، فتشغيل عدة مهام معًا يبطئها كلها بدل ما يسرّعها
executor = ThreadPoolExecutor(max_workers=1)

_jobs_lock = threading.Lock()
_jobs: dict = {}


def _load_jobs_from_disk() -> None:
    if JOBS_FILE.exists():
        try:
            with open(JOBS_FILE, "r", encoding="utf-8") as f:
                _jobs.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass


def _persist_jobs() -> None:
    with open(JOBS_FILE, "w", encoding="utf-8") as f:
        json.dump(_jobs, f, ensure_ascii=False, indent=2)


def _update_job(job_id: str, **fields) -> None:
    with _jobs_lock:
        _jobs[job_id].update(fields)
        _jobs[job_id]["updated_at"] = time.time()
        _persist_jobs()


_load_jobs_from_disk()

app = FastAPI(title="منصة الدبلجة والترجمة")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _process_job(job_id: str, input_path: str, target_lang: str, voice_id: str) -> None:
    output_path = str(OUTPUTS_DIR / job_id / "output.mp4")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    def on_progress(step: int, message: str) -> None:
        _update_job(job_id, status="processing", step=step, message=message)

    _update_job(job_id, status="processing", step=0, message="بدء المعالجة")
    try:
        run_pipeline(
            input_video_path=input_path,
            output_video_path=output_path,
            target_lang_code=target_lang,
            voice_id=voice_id or DEFAULT_VOICE_ID,
            elevenlabs_api_key=ELEVENLABS_API_KEY,
            deepl_api_key=DEEPL_API_KEY,
            libretranslate_url=LIBRETRANSLATE_URL,
            whisper_model_size=WHISPER_MODEL_SIZE,
            on_progress=on_progress,
        )
        _update_job(
            job_id, status="done", step=6, message="اكتملت الدبلجة",
            output_url=f"/api/download/{job_id}",
            share_url=f"/watch/{job_id}",
        )
    except PipelineError as exc:
        _update_job(job_id, status="error", message=str(exc))
    except Exception as exc:  # noqa: BLE001 — نعرض رسالة عامة، والتفاصيل بلوق السيرفر
        _update_job(job_id, status="error", message=f"خطأ غير متوقع أثناء المعالجة: {exc}")
    finally:
        # الملف المرفوع الأصلي ما نحتاجه بعد إنتاج الناتج
        try:
            os.remove(input_path)
        except OSError:
            pass


@app.get("/api/languages")
def list_languages():
    return {"languages": [{"code": l["code"], "label": l["label"]} for l in LANGUAGES]}


@app.post("/api/jobs")
async def create_job(
    video: UploadFile = File(...),
    target_lang: str = Form(...),
    voice_id: Optional[str] = Form(None),
):
    if get_language(target_lang) is None:
        raise HTTPException(400, f"لغة غير مدعومة: {target_lang}")

    if not (ELEVENLABS_API_KEY and (voice_id or DEFAULT_VOICE_ID)):
        raise HTTPException(
            503, "السيرفر غير مُعدّ بعد (مفتاح ElevenLabs أو الصوت الافتراضي مفقود)."
        )

    job_id = uuid.uuid4().hex[:12]
    job_dir = UPLOADS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(video.filename or "input.mp4").suffix or ".mp4"
    input_path = job_dir / f"input{suffix}"

    size = 0
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    with open(input_path, "wb") as out_file:
        while chunk := await video.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                out_file.close()
                os.remove(input_path)
                raise HTTPException(413, f"الفيديو أكبر من الحد المسموح ({MAX_UPLOAD_MB} ميجا).")
            out_file.write(chunk)

    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "step": 0,
            "message": "بانتظار المعالجة",
            "target_lang": target_lang,
            "original_filename": video.filename,
            "created_at": time.time(),
        }
        _persist_jobs()

    executor.submit(_process_job, job_id, str(input_path), target_lang, voice_id or "")

    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "المهمة غير موجودة.")
    return job


@app.get("/api/download/{job_id}")
def download_output(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None or job.get("status") != "done":
        raise HTTPException(404, "الفيديو غير جاهز أو غير موجود.")
    file_path = OUTPUTS_DIR / job_id / "output.mp4"
    if not file_path.exists():
        raise HTTPException(404, "ملف الفيديو غير موجود على السيرفر.")
    lang = get_language(job.get("target_lang", ""))
    lang_label = lang["code"] if lang else "dubbed"
    return FileResponse(
        file_path, media_type="video/mp4",
        filename=f"dubbed_{lang_label}_{job_id}.mp4",
    )


@app.get("/watch/{job_id}")
def watch_page(job_id: str):
    """صفحة مشاركة عامة: أي شخص يفتح هذا الرابط يشوف الفيديو الناتج مباشرة."""
    watch_html = FRONTEND_DIR / "watch.html"
    if not watch_html.exists():
        raise HTTPException(404, "صفحة العرض غير موجودة.")
    return FileResponse(watch_html)


# ملفات الواجهة الأمامية (index.html, app.html, واجهات ثابتة أخرى)
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
