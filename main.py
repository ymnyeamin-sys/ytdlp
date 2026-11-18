import os
import re
import json
import base64
import shutil
import tempfile
import subprocess
from pathlib import Path

import yt_dlp

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from waitress import serve


# =====================================================
# APP CONFIG
# =====================================================

app = Flask(__name__)
CORS(app)

APP_VERSION = "v10-multi-client"

PORT = int(os.environ.get("PORT", 8080))

MAX_DOWNLOAD_MB = int(os.environ.get("MAX_DOWNLOAD_MB", 500))
MAX_DOWNLOAD_BYTES = MAX_DOWNLOAD_MB * 1024 * 1024

PLAYER_CLIENTS = [
    "web_safari",
    "android_vr",
    "tv_simply",
    "ios"
]


# =====================================================
# COOKIES
# =====================================================

def get_cookies_path():

    custom_path = os.environ.get("YOUTUBE_COOKIES_PATH")

    if custom_path and os.path.isfile(custom_path):
        return custom_path

    cookies_b64 = os.environ.get("YOUTUBE_COOKIES_B64")

    if cookies_b64:

        try:
            cookies_data = base64.b64decode(cookies_b64)

            path = "/tmp/youtube_cookies.txt"

            with open(path, "wb") as file:
                file.write(cookies_data)

            return path

        except Exception:
            pass

    default_path = "/app/cookies.txt"

    if os.path.isfile(default_path):
        return default_path

    return None


# =====================================================
# SYSTEM DEBUG
# =====================================================

def get_program_info(program):

    path = shutil.which(program)

    version = None

    if path:

        try:
            output = subprocess.check_output(
                [program, "--version"],
                stderr=subprocess.STDOUT,
                text=True,
                timeout=10
            )

            version = output.strip()

        except Exception:
            pass

    return {
        "path": path,
        "version": version
    }


def get_yt_dlp_version():

    try:
        return yt_dlp.version.__version__
    except Exception:
        return "unknown"


# =====================================================
# REQUEST HELPERS
# =====================================================

def get_video_url():

    data = request.get_json(silent=True) or {}

    url = data.get("url") or request.args.get("url")

    if not url:
        return None

    url = str(url).strip()

    if not re.match(
        r"^https?://(www\.)?(youtube\.com|youtu\.be|m\.youtube\.com)/",
        url,
        re.IGNORECASE
    ):
        return None

    return url


def make_ydl_options(client, download=False):

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "ignoreerrors": False,
        "socket_timeout": 25,
        "retries": 2,
        "fragment_retries": 2,
        "extractor_retries": 2,
        "skip_download": not download,
        "extractor_args": {
            "youtube": {
                "player_client": [client]
            }
        }
    }

    cookies = get_cookies_path()

    if cookies:
        options["cookiefile"] = cookies

    if download:

        options.update({
            "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
            "merge_output_format": "mp4",
            "outtmpl": "%(title).150B.%(ext)s",
            "max_filesize": MAX_DOWNLOAD_BYTES
        })

    return options


# =====================================================
# FORMAT HELPERS
# =====================================================

def is_video_format(fmt):

    return (
        fmt.get("vcodec")
        and fmt.get("vcodec") != "none"
        and fmt.get("height")
    )


def format_details(fmt):

    return {
        "format_id": fmt.get("format_id"),
        "height": fmt.get("height"),
        "width": fmt.get("width"),
        "resolution": fmt.get("resolution"),
        "ext": fmt.get("ext"),
        "fps": fmt.get("fps"),
        "filesize": fmt.get("filesize") or fmt.get("filesize_approx"),
        "vcodec": fmt.get("vcodec"),
        "acodec": fmt.get("acodec"),
        "audio": bool(
            fmt.get("acodec")
            and fmt.get("acodec") != "none"
        ),
        "protocol": fmt.get("protocol")
    }


# =====================================================
# MULTI CLIENT EXTRACTION
# =====================================================

def extract_all_clients(url):

    all_formats = []
    attempts = []
    video_info = None

    seen = set()

    for client in PLAYER_CLIENTS:

        messages = []

        try:

            options = make_ydl_options(client)

            with yt_dlp.YoutubeDL(options) as ydl:

                info = ydl.extract_info(
                    url,
                    download=False
                )

            if not info:
                raise Exception("No video information returned")

            if not video_info:
                video_info = info

            formats = info.get("formats", [])

            real_formats = 0

            for fmt in formats:

                if not is_video_format(fmt):
                    continue

                real_formats += 1

                key = (
                    str(fmt.get("format_id")),
                    str(fmt.get("height")),
                    str(fmt.get("ext")),
                    str(fmt.get("vcodec")),
                    str(fmt.get("acodec")),
                    str(fmt.get("protocol"))
                )

                if key in seen:
                    continue

                seen.add(key)

                item = dict(fmt)

                item["_client"] = client

                all_formats.append(item)

            attempts.append({
                "clients": [client],
                "cookies": bool(get_cookies_path()),
                "real_formats": real_formats,
                "messages": messages
            })

        except Exception as error:

            error_message = str(error)

            messages.append(error_message)

            attempts.append({
                "clients": [client],
                "cookies": bool(get_cookies_path()),
                "real_formats": 0,
                "messages": messages
            })

    return video_info, all_formats, attempts


# =====================================================
# SELECT FORMAT
# =====================================================

def select_formats(formats, resolution):

    video_formats = [
        fmt for fmt in formats
        if is_video_format(fmt)
        and int(fmt.get("height") or 0) <= resolution
    ]

    if not video_formats:
        return None, None

    video_formats.sort(
        key=lambda x: (
            int(x.get("height") or 0),
            int(x.get("fps") or 0)
        ),
        reverse=True
    )

    selected_video = video_formats[0]

    progressive = [
        fmt for fmt in video_formats
        if fmt.get("acodec")
        and fmt.get("acodec") != "none"
    ]

    selected_audio = None

    if not (
        selected_video.get("acodec")
        and selected_video.get("acodec") != "none"
    ):

        audio_formats = [
            fmt for fmt in formats
            if fmt.get("acodec")
            and fmt.get("acodec") != "none"
            and fmt.get("vcodec") == "none"
        ]

        if audio_formats:

            audio_formats.sort(
                key=lambda x: (
                    int(x.get("abr") or 0),
                    int(x.get("asr") or 0)
                ),
                reverse=True
            )

            selected_audio = audio_formats[0]

    return selected_video, selected_audio


# =====================================================
# BASIC ROUTES
# =====================================================

@app.route("/", methods=["GET"])
def home():

    return jsonify({
        "app_version": APP_VERSION,
        "status": "API is running successfully!"
    })


@app.route("/health", methods=["GET"])
def health():

    return jsonify({
        "status": "healthy"
    })


@app.route("/debug", methods=["GET"])
def debug():

    cookies = get_cookies_path()

    deno = get_program_info("deno")
    ffmpeg = get_program_info("ffmpeg")
    ffprobe = get_program_info("ffprobe")
    node = get_program_info("node")

    try:
        import yt_dlp_ejs
        ejs_installed = True
    except Exception:
        ejs_installed = False

    return jsonify({
        "app_version": APP_VERSION,
        "cookies_exists": bool(cookies),
        "cookies_size": (
            os.path.getsize(cookies)
            if cookies else 0
        ),
        "deno": deno["path"],
        "deno_version": deno["version"],
        "ffmpeg": ffmpeg["path"],
        "ffprobe": ffprobe["path"],
        "node": node["path"],
        "yt_dlp_ejs_installed": ejs_installed,
        "yt_dlp_version": get_yt_dlp_version()
    })


# =====================================================
# VIDEO INFO
# =====================================================

@app.route("/video_info", methods=["POST"])
def video_info_route():

    url = get_video_url()

    if not url:
        return jsonify({
            "error": "Valid YouTube URL is required"
        }), 400

    info, formats, attempts = extract_all_clients(url)

    if not info:

        return jsonify({
            "error": "Could not extract video information",
            "attempts": attempts
        }), 400

    return jsonify({
        "title": info.get("title"),
        "id": info.get("id"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
        "view_count": info.get("view_count"),
        "description": info.get("description"),
        "formats_count": len(formats)
    })


# =====================================================
# AVAILABLE RESOLUTIONS
# =====================================================

@app.route("/available_resolutions", methods=["POST"])
def available_resolutions():

    url = get_video_url()

    if not url:
        return jsonify({
            "error": "Valid YouTube URL is required"
        }), 400

    info, formats, attempts = extract_all_clients(url)

    if not formats:

        return jsonify({
            "error": "No video formats were returned.",
            "all": [],
            "progressive": [],
            "attempts": attempts
        }), 400

    resolutions = sorted(
        {
            int(fmt["height"])
            for fmt in formats
            if fmt.get("height")
        },
        reverse=True
    )

    details = [
        format_details(fmt)
        for fmt in formats
    ]

    progressive = [
        item for item in details
        if item["audio"]
    ]

    return jsonify({
        "title": info.get("title") if info else None,
        "video_id": info.get("id") if info else None,
        "all": resolutions,
        "progressive": progressive,
        "formats": details,
        "attempts": attempts
    })


# =====================================================
# STREAMS
# =====================================================

@app.route("/streams/<int:resolution>", methods=["POST"])
def streams(resolution):

    url = get_video_url()

    if not url:
        return jsonify({
            "error": "Valid YouTube URL is required"
        }), 400

    info, formats, attempts = extract_all_clients(url)

    video, audio = select_formats(formats, resolution)

    if not video:

        return jsonify({
            "error": "Requested resolution is not available",
            "attempts": attempts
        }), 404

    return jsonify({
        "title": info.get("title") if info else None,
        "resolution": video.get("height"),
        "video": {
            "format_id": video.get("format_id"),
            "url": video.get("url"),
            "ext": video.get("ext"),
            "filesize": video.get("filesize")
        },
        "audio": (
            {
                "format_id": audio.get("format_id"),
                "url": audio.get("url"),
                "ext": audio.get("ext")
            }
            if audio else None
        )
    })


# =====================================================
# DOWNLOAD
# =====================================================

def download_video(resolution):

    url = get_video_url()

    if not url:
        return jsonify({
            "error": "Valid YouTube URL is required"
        }), 400

    if resolution < 1 or resolution > 4320:
        return jsonify({
            "error": "Invalid resolution"
        }), 400

    errors = []

    for client in PLAYER_CLIENTS:

        temp_dir = tempfile.mkdtemp(prefix="ytdlp_")

        try:

            options = make_ydl_options(
                client,
                download=True
            )

            options["outtmpl"] = os.path.join(
                temp_dir,
                "%(title).150B.%(ext)s"
            )

            with yt_dlp.YoutubeDL(options) as ydl:

                info = ydl.extract_info(
                    url,
                    download=True
                )

                downloaded = ydl.prepare_filename(info)

            candidates = list(Path(temp_dir).glob("*"))

            candidates = [
                str(path)
                for path in candidates
                if path.is_file()
                and path.suffix.lower() in (
                    ".mp4",
                    ".mkv",
                    ".webm",
                    ".mov"
                )
            ]

            if not candidates:
                raise Exception(
                    "Downloaded file was not found"
                )

            file_path = candidates[0]

            if os.path.getsize(file_path) > MAX_DOWNLOAD_BYTES:
                raise Exception(
                    "File exceeds maximum download size"
                )

            response = send_file(
                file_path,
                as_attachment=True,
                download_name=os.path.basename(file_path),
                mimetype="video/mp4"
            )

            response.call_on_close(
                lambda: shutil.rmtree(
                    temp_dir,
                    ignore_errors=True
                )
            )

            return response

        except Exception as error:

            errors.append({
                "client": client,
                "error": str(error)
            })

            shutil.rmtree(
                temp_dir,
                ignore_errors=True
            )

    return jsonify({
        "error": "Download failed with all available clients",
        "attempts": errors
    }), 400


@app.route("/download/<int:resolution>", methods=["POST"])
def download_route(resolution):

    return download_video(resolution)


@app.route("/download_file/<int:resolution>", methods=["POST"])
def download_file_route(resolution):

    return download_video(resolution)


# =====================================================
# ERROR HANDLERS
# =====================================================

@app.errorhandler(404)
def not_found(error):

    return jsonify({
        "error": "Endpoint not found"
    }), 404


@app.errorhandler(500)
def internal_error(error):

    return jsonify({
        "error": "Internal server error"
    }), 500


# =====================================================
# START SERVER
# =====================================================

if __name__ == "__main__":

    print("Starting YouTube API...")
    print("Version:", APP_VERSION)
    print("yt-dlp:", get_yt_dlp_version())
    print("Deno:", shutil.which("deno"))
    print("FFmpeg:", shutil.which("ffmpeg"))
    print("Cookies:", bool(get_cookies_path()))

    serve(
        app,
        host="0.0.0.0",
        port=PORT,
        threads=8
    )
