"""
pipeline.py — خط أنابيب دبلجة الفيديو، مستقل عن أي إطار ويب.

يأخذ فيديو ولغة هدف ويرجع مسار الفيديو المدبلج. يبلّغ عن تقدمه عبر
دالة on_progress(step:int, message:str) اختيارية، عشان الواجهة تقدر
تعرض حالة حية للمستخدم.
"""

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import requests

from languages import get_language

DEEPL_TRANSLATE_URL = "https://api-free.deepl.com/v2/translate"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

# نموذج Whisper المحلي (لا يحتاج حساب) — يُحمَّل مرة واحدة ويُعاد استخدامه
_whisper_model = None
_whisper_model_size = None


class PipelineError(Exception):
    """خطأ متوقع (بيانات ناقصة، لغة غير مدعومة...) يُعرض للمستخدم كما هو."""


@dataclass
class Segment:
    start: float
    end: float
    text: str
    translated_text: str = ""
    audio_path: str = ""


def _noop_progress(step: int, message: str) -> None:
    pass


def extract_audio(video_path: str, out_path: str) -> str:
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
         "-ar", "16000", "-ac", "1", out_path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return out_path


def transcribe(audio_path: str, model_size: str = "small") -> List[Segment]:
    global _whisper_model, _whisper_model_size
    import whisper  # استيراد مؤجل: يفشل بوضوح فقط عند الاستخدام الفعلي

    if _whisper_model is None or _whisper_model_size != model_size:
        _whisper_model = whisper.load_model(model_size)
        _whisper_model_size = model_size

    result = _whisper_model.transcribe(audio_path, verbose=False)
    return [
        Segment(start=s["start"], end=s["end"], text=s["text"].strip())
        for s in result.get("segments", [])
        if s["text"].strip()
    ]


def _translate_via_deepl(text: str, deepl_code: str, api_key: str) -> str:
    resp = requests.post(
        DEEPL_TRANSLATE_URL,
        headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
        data={"text": text, "target_lang": deepl_code},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["translations"][0]["text"]


def _translate_via_libre(text: str, libre_code: str, base_url: str) -> str:
    resp = requests.post(
        f"{base_url.rstrip('/')}/translate",
        json={"q": text, "source": "auto", "target": libre_code, "format": "text"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["translatedText"]


def translate_segments(
    segments: List[Segment],
    target_lang_code: str,
    deepl_api_key: Optional[str],
    libretranslate_url: Optional[str],
) -> List[Segment]:
    lang = get_language(target_lang_code)
    if lang is None:
        raise PipelineError(f"لغة غير مدعومة: {target_lang_code}")

    use_deepl = bool(lang["deepl"] and deepl_api_key)
    use_libre = bool(libretranslate_url)

    if not use_deepl and not use_libre:
        raise PipelineError(
            f"لا توجد خدمة ترجمة متاحة للغة '{lang['label']}'. "
            "هذي اللغة تحتاج LIBRETRANSLATE_URL مُعرَّف، لأن DeepL لا يدعمها."
        )

    for seg in segments:
        try:
            if use_deepl:
                seg.translated_text = _translate_via_deepl(
                    seg.text, lang["deepl"], deepl_api_key
                )
            else:
                seg.translated_text = _translate_via_libre(
                    seg.text, lang["libre"], libretranslate_url
                )
        except requests.HTTPError as exc:
            if use_deepl and use_libre:
                # فشل DeepL (رصيد منتهي، خطأ مفتاح) وعندنا بديل: نجرب Libre
                seg.translated_text = _translate_via_libre(
                    seg.text, lang["libre"], libretranslate_url
                )
            else:
                raise PipelineError(f"فشلت الترجمة: {exc}") from exc
    return segments


def synthesize_segment(text: str, voice_id: str, api_key: str, out_path: str) -> str:
    url = ELEVENLABS_TTS_URL.format(voice_id=voice_id)
    resp = requests.post(
        url,
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.45, "similarity_boost": 0.8},
        },
        timeout=120,
    )
    resp.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(resp.content)
    return out_path


def synthesize_all(
    segments: List[Segment], voice_id: str, api_key: str, workdir: str,
    on_progress: Callable[[int, str], None],
) -> List[Segment]:
    for i, seg in enumerate(segments):
        out_path = os.path.join(workdir, f"seg_{i:04d}.mp3")
        synthesize_segment(seg.translated_text, voice_id, api_key, out_path)
        seg.audio_path = out_path
        on_progress(4, f"توليد الصوت المدبلج — {i + 1}/{len(segments)}")
    return segments


def get_audio_duration(path: str) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path]
    )
    return float(out.decode().strip())


def fit_segment_to_timing(seg: Segment, workdir: str, idx: int) -> str:
    target_duration = max(seg.end - seg.start, 0.3)
    actual_duration = get_audio_duration(seg.audio_path)
    tempo = min(max(actual_duration / target_duration, 0.7), 1.6)

    fitted_path = os.path.join(workdir, f"fit_{idx:04d}.mp3")
    subprocess.run(
        ["ffmpeg", "-y", "-i", seg.audio_path, "-filter:a", f"atempo={tempo:.3f}",
         fitted_path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return fitted_path


def assemble_audio_track(segments: List[Segment], workdir: str) -> str:
    silence_dir = os.path.join(workdir, "silence")
    os.makedirs(silence_dir, exist_ok=True)

    list_file = os.path.join(workdir, "concat_list.txt")
    cursor = 0.0

    with open(list_file, "w") as lf:
        for i, seg in enumerate(segments):
            gap = seg.start - cursor
            if gap > 0.05:
                silence_path = os.path.join(silence_dir, f"sil_{i:04d}.mp3")
                subprocess.run(
                    ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                     "-t", f"{gap:.3f}", silence_path],
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                lf.write(f"file '{silence_path}'\n")

            fitted = fit_segment_to_timing(seg, workdir, i)
            lf.write(f"file '{fitted}'\n")
            cursor = seg.end

    final_audio = os.path.join(workdir, "dubbed_audio.mp3")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
         "-c", "copy", final_audio],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return final_audio


def mux_video(video_path: str, dubbed_audio_path: str, output_path: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-i", dubbed_audio_path,
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
         "-shortest", output_path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def run_pipeline(
    input_video_path: str,
    output_video_path: str,
    target_lang_code: str,
    voice_id: str,
    *,
    elevenlabs_api_key: str,
    deepl_api_key: Optional[str] = None,
    libretranslate_url: Optional[str] = None,
    whisper_model_size: str = "small",
    on_progress: Optional[Callable[[int, str], None]] = None,
) -> str:
    """ينفّذ خط الأنابيب كاملاً ويرجع مسار الفيديو الناتج."""
    progress = on_progress or _noop_progress

    if not elevenlabs_api_key:
        raise PipelineError("مفتاح ElevenLabs غير مُعرَّف على السيرفر.")

    with tempfile.TemporaryDirectory() as workdir:
        progress(1, "استخراج الصوت من الفيديو")
        audio_path = extract_audio(
            input_video_path, os.path.join(workdir, "source_audio.wav")
        )

        progress(2, f"تفريغ الحوار (نموذج Whisper: {whisper_model_size})")
        segments = transcribe(audio_path, whisper_model_size)
        if not segments:
            raise PipelineError("لم يتم استخراج أي حوار من الفيديو.")

        lang = get_language(target_lang_code)
        progress(3, f"ترجمة {len(segments)} جملة إلى {lang['label'] if lang else target_lang_code}")
        segments = translate_segments(
            segments, target_lang_code, deepl_api_key, libretranslate_url
        )

        progress(4, "توليد الصوت المدبلج")
        segments = synthesize_all(segments, voice_id, elevenlabs_api_key, workdir, progress)

        progress(5, "ضبط التوقيت وتجميع المسار الصوتي")
        dubbed_audio = assemble_audio_track(segments, workdir)

        progress(6, "دمج الصوت المدبلج مع الفيديو")
        mux_video(input_video_path, dubbed_audio, output_video_path)

    return output_video_path
