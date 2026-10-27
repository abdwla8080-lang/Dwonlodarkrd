from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Ensure Windows terminal doesn't crash when printing non-ASCII / Kurdish characters
if sys.platform.startswith("win"):
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from flask import Flask, jsonify, request, send_file
import yt_dlp

app = Flask(__name__)
BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

SUPPORTED_SITES = [
    "TikTok",
    "YouTube",
    "Facebook",
    "Instagram",
    "Pinterest",
]


def clean_title(title: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]+', '', title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")[:90]
    return cleaned or "downloaded-video"


def get_ffmpeg_path() -> str | None:
    # 1. System PATH
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg

    # 2. imageio-ffmpeg bundled binary
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:
        pass

    return None


def quality_format(target_quality: str, has_ffmpeg: bool = True) -> str:
    quality = (target_quality or "1080p").lower()
    if has_ffmpeg:
        quality_map = {
            "best": "bestvideo+bestaudio/bestvideo+best/best",
            "1080p": "bestvideo[height<=1080]+bestaudio/bestvideo[height<=1080]/best[height<=1080]/best",
            "720p": "bestvideo[height<=720]+bestaudio/bestvideo[height<=720]/best[height<=720]/best",
            "480p": "bestvideo[height<=480]+bestaudio/bestvideo[height<=480]/best[height<=480]/best",
        }
    else:
        # Fallback to single-stream formats if ffmpeg is unavailable
        quality_map = {
            "best": "best[ext=mp4]/best",
            "1080p": "best[height<=1080][ext=mp4]/best[height<=1080]/best",
            "720p": "best[height<=720][ext=mp4]/best[height<=720]/best",
            "480p": "best[height<=480][ext=mp4]/best[height<=480]/best",
        }
    return quality_map.get(quality, quality_map["1080p" if has_ffmpeg else "best"])


def make_ytdlp_options(
    output_template: str,
    quality: str,
    extractor_mode: str | None = None,
    media_format: str = "mp4",
) -> dict:
    ffmpeg_bin = get_ffmpeg_path()
    has_ffmpeg = bool(ffmpeg_bin)

    base = {
        "outtmpl": output_template,
        "format": "bestaudio/best" if media_format == "mp3" else quality_format(quality, has_ffmpeg=has_ffmpeg),
        "noplaylist": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "quiet": True,
        "no_color": True,
        "paths": {"home": str(DOWNLOAD_DIR)},
    }

    if ffmpeg_bin:
        base["ffmpeg_location"] = ffmpeg_bin
        if media_format == "mp3":
            base["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        else:
            base["merge_output_format"] = "mp4"
    elif media_format == "mp3":
        raise RuntimeError("بۆ گۆڕینی دەنگ بۆ MP3 پێویستت بە FFmpeg هەیە.")

    if extractor_mode:
        base["extractor_args"] = {"tiktok": {"extractor": extractor_mode}}

    return base


def try_extract_video(url: str, quality: str, media_format: str = "mp4"):
    modes = [None, "browser", "web"]
    last_error = None

    for mode in modes:
        try:
            file_name = clean_title(f"download-{abs(hash(url))}")
            output_template = f"{file_name}.%(ext)s"
            ydl_opts = make_ytdlp_options(output_template, quality, mode, media_format)

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

            if not isinstance(info, dict):
                raise ValueError("The URL could not be resolved as a valid video.")

            title = info.get("title") or "downloaded-video"

            # Locate downloaded file
            target_file = None
            if info.get("requested_downloads"):
                for req in info["requested_downloads"]:
                    fp = req.get("filepath")
                    if fp and Path(fp).exists():
                        target_file = Path(fp)
                        break

            if not target_file or not target_file.exists():
                prep = Path(DOWNLOAD_DIR / f"{file_name}.{media_format}")
                if prep.exists():
                    target_file = prep
                else:
                    matches = list(DOWNLOAD_DIR.glob(f"{file_name}.*"))
                    if matches:
                        target_file = sorted(matches, key=lambda p: p.stat().st_size, reverse=True)[0]

            if not target_file or not target_file.exists():
                raise FileNotFoundError("Video was not created after download.")

            # Rename to a friendly filename if possible
            final_title_name = clean_title(title)
            safe_ascii = re.sub(r'[^A-Za-z0-9_\-]+', '_', final_title_name).strip('_')[:80]
            safe_name = safe_ascii or file_name
            final_dest = DOWNLOAD_DIR / f"{safe_name}{target_file.suffix}"
            if final_dest != target_file:
                if final_dest.exists():
                    final_dest = DOWNLOAD_DIR / f"{safe_name}_{abs(hash(url)) % 100000}{target_file.suffix}"
                try:
                    target_file.rename(final_dest)
                    target_file = final_dest
                except Exception as err:
                    app.logger.warning(f"Could not rename file {target_file} to {final_dest}: {err}")

            return target_file, title

        except Exception as exc:
            last_error = exc
            continue

    raise last_error or RuntimeError("Unable to fetch the media from this URL.")


@app.route("/")
def index():
    return send_file(BASE_DIR / "index.html")


@app.route("/api/download", methods=["POST"])
def api_download():
    payload = request.get_json(silent=True) or {}
    url = (payload.get("url") or "").strip()
    quality = (payload.get("quality") or "1080p").strip()
    media_format = (payload.get("format") or "mp4").strip().lower()

    if not url:
        return jsonify({"error": "تکایە بەستەری ڤیدیۆکە لێرە دابنێ."}), 400
    if media_format not in {"mp4", "mp3"}:
        return jsonify({"error": "فۆرماتی هەڵبژێردراو پشتگیری ناکرێت."}), 400

    try:
        selected, title = try_extract_video(url, quality, media_format)
        download_name = selected.name
        return jsonify({
            "title": title,
            "download_url": f"/download/{download_name}",
            "quality": quality,
            "status": "success",
        })

    except Exception as exc:
        app.logger.exception("Download failed")
        if isinstance(exc, RuntimeError) and str(exc).startswith("بۆ گۆڕینی دەنگ"):
            message = str(exc)
        else:
            message = "داگرتن سەرکەوتوو نەبوو. تکایە بەستەرەکە و پەیوەندی ئینتەرنێتەکەت بپشکنە."
        return jsonify({"error": message}), 400


@app.route("/download/<path:filename>")
def serve_download(filename: str):
    file_path = DOWNLOAD_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        return jsonify({"error": "فایلەکە نەدۆزرایەوە."}), 404
    return send_file(file_path, as_attachment=True)


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    app.run(host=host, port=port, debug=False)
