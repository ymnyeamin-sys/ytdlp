
import os
import re
import base64
import shutil
import tempfile
import subprocess
import logging

from pathlib import Path
from importlib.metadata import version, PackageNotFoundError

import yt_dlp

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from waitress import serve


# =====================================================
# CONFIGURATION
# =====================================================

app = Flask(__name__)
CORS(app)

APP_VERSION = "v13-audio-formats-size-estimates"

PORT = int(os.environ.get("PORT", 8080))

MAX_DOWNLOAD_MB = int(os.environ.get("MAX_DOWNLOAD_MB", 500))
MAX_DOWNLOAD_BYTES = MAX_DOWNLOAD_MB * 1024 * 1024

PLAYER_CLIENTS = [
    "web_safari",
    "android_vr",
    "tv_simply",
    "ios"
]

# Extra clients are used only for metadata discovery. The download client
# sequence above remains unchanged, preserving the tested download behavior.
FORMAT_FALLBACK_CLIENTS = ["web", "mweb"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("youtube-api")


# =====================================================
# COOKIES MANAGEMENT
# =====================================================

def get_cookies_path():

    custom_path = os.environ.get("YOUTUBE_COOKIES_PATH")

    if custom_path and os.path.isfile(custom_path):
        return custom_path

    cookies_b64 = os.environ.get("YOUTUBE_COOKIES_B64")

    if cookies_b64:

        try:
            cookies_data = base64.b64decode(
                cookies_b64,
                validate=True
            )

            path = "/tmp/youtube_cookies.txt"

            with open(path, "wb") as file:
                file.write(cookies_data)

            return path

        except Exception as error:
            logger.error("Cookies decode error: %s", error)

    default_path = "/app/cookies.txt"

    if os.path.isfile(default_path):
        return default_path

    return None


# =====================================================
# SYSTEM INFORMATION
# =====================================================

def get_program_info(program):

    path = shutil.which(program)
    program_version = None

    if path:

        try:
            output = subprocess.check_output(
                [program, "--version"],
                stderr=subprocess.STDOUT,
                text=True,
                timeout=10
            )

            program_version = output.strip()

        except Exception as error:
            program_version = str(error)

    return {
        "path": path,
        "version": program_version
    }


def get_yt_dlp_version():

    try:
        return version("yt-dlp")

    except Exception:
        return "unknown"


def is_ejs_installed():

    try:
        version("yt-dlp-ejs")
        return True

    except PackageNotFoundError:
        return False

    except Exception:
        return False


# =====================================================
# REQUEST VALIDATION
# =====================================================

def get_video_url():

    data = request.get_json(silent=True) or {}

    url = data.get("url") or request.args.get("url")

    if not url:
        return None

    url = str(url).strip()

    pattern = (
        r"^https?://"
        r"(www\.)?(youtube\.com|youtu\.be|m\.youtube\.com)"
        r"/"
    )

    if not re.match(pattern, url, re.IGNORECASE):
        return None

    return url


# =====================================================
# YT-DLP LOGGER
# =====================================================

class YTDLPLogger:

    def __init__(self):
        self.messages = []

    def debug(self, message):
        pass

    def info(self, message):
        pass

    def warning(self, message):
        self.messages.append(str(message))

    def error(self, message):
        self.messages.append(str(message))


# =====================================================
# YT-DLP OPTIONS
# =====================================================

def make_ydl_options(client, download=False, resolution=1080, audio_only=False, use_cookies=True):

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
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

    cookies = get_cookies_path() if use_cookies else None

    if cookies:
        options["cookiefile"] = cookies

    if download:

        options.update({
            "format": "bestaudio/best" if audio_only else (
                f"bestvideo[height<={resolution}]+bestaudio/"
                f"best[height<={resolution}]/best"
            ),
            "max_filesize": MAX_DOWNLOAD_BYTES,
            "outtmpl": os.path.join(
                tempfile.gettempdir(),
                "%(id)s_%(title).100B.%(ext)s"
            )
        })

        if not audio_only:
            options["merge_output_format"] = "mp4"
        else:
            options["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192"
            }]

    return options


# =====================================================
# FORMAT HELPERS
# =====================================================

def is_video_format(fmt):

    vcodec = fmt.get("vcodec")

    return bool(
        vcodec
        and vcodec != "none"
        and fmt.get("height")
    )


def format_details(fmt, duration=None):

    # Prefer yt-dlp's real or approximate size when available.
    known_size = fmt.get("filesize") or fmt.get("filesize_approx")
    size = known_size
    size_source = "filesize" if fmt.get("filesize") else (
        "filesize_approx" if fmt.get("filesize_approx") else None
    )
    size_is_estimate = bool(fmt.get("filesize_approx") and not fmt.get("filesize"))

    # HLS/DASH formats often do not expose a size. Estimate it from the
    # bitrate and duration, and clearly mark it as an estimate.
    if not size and duration:
        bitrate_kbps = fmt.get("tbr")

        if not bitrate_kbps:
            video_bitrate = fmt.get("vbr") or 0
            audio_bitrate = fmt.get("abr") or 0
            bitrate_kbps = video_bitrate + audio_bitrate

        try:
            if bitrate_kbps and float(bitrate_kbps) > 0:
                size = int(float(duration) * float(bitrate_kbps) * 1000 / 8)
                size_source = "bitrate_duration_estimate"
                size_is_estimate = True
        except (TypeError, ValueError, OverflowError):
            size = None
            size_source = None
            size_is_estimate = False

    return {
        "format_id": fmt.get("format_id"),
        "height": fmt.get("height"),
        "width": fmt.get("width"),
        "resolution": fmt.get("resolution"),
        "ext": fmt.get("ext"),
        "fps": fmt.get("fps"),
        "tbr": fmt.get("tbr"),
        "abr": fmt.get("abr"),

        "filesize": size,
        "filesize_mb": round(size / (1024 * 1024), 2) if size else None,
        "filesize_gb": round(size / (1024 * 1024 * 1024), 3) if size else None,
        "filesize_is_estimate": size_is_estimate,
        "filesize_source": size_source,

        "video": bool(fmt.get("vcodec") and fmt.get("vcodec") != "none"),
        "vcodec": fmt.get("vcodec"),
        "acodec": fmt.get("acodec"),

        "audio": bool(
            fmt.get("acodec")
            and fmt.get("acodec") != "none"
        ),

        "protocol": fmt.get("protocol"),
        "client": fmt.get("_client")
    }


# =====================================================
# MULTI-CLIENT EXTRACTION
# =====================================================

def extract_all_clients(url):

    all_formats = []
    attempts = []

    video_info = None
    seen = set()

    metadata_clients = PLAYER_CLIENTS + FORMAT_FALLBACK_CLIENTS

    for client in metadata_clients:

        ydl_logger = YTDLPLogger()
        use_cookies = (client == "web_safari")

        real_formats = 0

        try:

            options = make_ydl_options(client, use_cookies=use_cookies)

            options["logger"] = ydl_logger

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

            for fmt in formats:

                has_video = bool(fmt.get("vcodec") and fmt.get("vcodec") != "none")
                has_audio = bool(fmt.get("acodec") and fmt.get("acodec") != "none")

                # Keep video formats and audio-only formats; ignore unusable entries.
                if not has_video and not has_audio:
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

                "cookies": bool(get_cookies_path()) if use_cookies else False,

                "real_formats": real_formats,

                "messages": ydl_logger.messages

            })

        except Exception as error:

            messages = list(ydl_logger.messages)
            messages.append(str(error))

            attempts.append({

                "clients": [client],

                "cookies": bool(get_cookies_path()) if use_cookies else False,

                "real_formats": 0,

                "messages": messages

            })

    return video_info, all_formats, attempts


# =====================================================
# FORMAT SELECTION
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

        key=lambda fmt: (

            int(fmt.get("height") or 0),

            int(fmt.get("fps") or 0),

            int(fmt.get("tbr") or 0)

        ),

        reverse=True

    )

    selected_video = video_formats[0]

    selected_audio = None

    has_audio = (
        selected_video.get("acodec")
        and selected_video.get("acodec") != "none"
    )

    if not has_audio:

        audio_formats = [

            fmt for fmt in formats

            if fmt.get("acodec")
            and fmt.get("acodec") != "none"

            and fmt.get("vcodec") == "none"

        ]

        if audio_formats:

            audio_formats.sort(

                key=lambda fmt: (

                    int(fmt.get("abr") or 0),

                    int(fmt.get("asr") or 0)

                ),

                reverse=True

            )

            selected_audio = audio_formats[0]

    return selected_video, selected_audio


# =====================================================
# HOME
# =====================================================

@app.route("/", methods=["GET"])
def home():

    return jsonify({

        "app_version": APP_VERSION,

        "status": "API is running successfully!"

    })


# =====================================================
# HEALTH
# =====================================================

@app.route("/health", methods=["GET"])
def health():

    return jsonify({

        "status": "healthy"

    })


# =====================================================
# DEBUG
# =====================================================

@app.route("/debug", methods=["GET"])
def debug():

    cookies = get_cookies_path()

    deno = get_program_info("deno")
    ffmpeg = get_program_info("ffmpeg")
    ffprobe = get_program_info("ffprobe")
    node = get_program_info("node")

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

        "yt_dlp_ejs_installed": is_ejs_installed(),

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

    duration = info.get("duration") if info else None

    details = [

        format_details(fmt, duration=duration)

        for fmt in formats

    ]

    progressive = [
        item for item in details
        if item["video"] and item["audio"]
    ]

    audio_formats = [
        item for item in details
        if item["audio"] and not item["video"]
    ]

    return jsonify({

        "title": info.get("title") if info else None,

        "video_id": info.get("id") if info else None,

        "all": resolutions,

        "progressive": progressive,

        "audio_formats": audio_formats,

        "audio_formats_count": len(audio_formats),

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
# DOWNLOAD ENGINE
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

        temp_dir = tempfile.mkdtemp(
            prefix="ytdlp_"
        )

        ydl_logger = YTDLPLogger()

        try:

            options = make_ydl_options(

                client,

                download=True,

                resolution=resolution

            )

            options["outtmpl"] = os.path.join(

                temp_dir,

                "%(id)s_%(title).100B.%(ext)s"

            )

            options["logger"] = ydl_logger

            with yt_dlp.YoutubeDL(options) as ydl:

                ydl.extract_info(

                    url,

                    download=True

                )

            candidates = [

                path for path in Path(temp_dir).glob("*")

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

            file_path = max(

                candidates,

                key=lambda path: path.stat().st_size

            )

            file_size = file_path.stat().st_size

            if file_size > MAX_DOWNLOAD_BYTES:

                raise Exception(
                    "File exceeds maximum download size"
                )

            response = send_file(

                str(file_path),

                as_attachment=True,

                download_name=file_path.name,

                mimetype="application/octet-stream"

            )

            response.call_on_close(

                lambda: shutil.rmtree(

                    temp_dir,

                    ignore_errors=True

                )

            )

            return response

        except Exception as error:

            messages = list(ydl_logger.messages)

            messages.append(str(error))

            errors.append({

                "client": client,

                "messages": messages

            })

            shutil.rmtree(

                temp_dir,

                ignore_errors=True

            )

    return jsonify({

        "error": "Download failed with all available clients",

        "attempts": errors

    }), 400


# =====================================================
# AUDIO DOWNLOAD ENGINE (MP3)
# =====================================================

def download_audio():

    url = get_video_url()

    if not url:
        return jsonify({"error": "Valid YouTube URL is required"}), 400

    if not shutil.which("ffmpeg"):
        return jsonify({"error": "FFmpeg is required for MP3 audio conversion"}), 500

    errors = []

    for client in PLAYER_CLIENTS:
        temp_dir = tempfile.mkdtemp(prefix="ytdlp_audio_")
        ydl_logger = YTDLPLogger()

        try:
            options = make_ydl_options(
                client,
                download=True,
                audio_only=True
            )
            options["outtmpl"] = os.path.join(
                temp_dir,
                "%(id)s_%(title).100B.%(ext)s"
            )
            options["logger"] = ydl_logger

            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.extract_info(url, download=True)

            candidates = [
                path for path in Path(temp_dir).glob("*")
                if path.is_file() and path.suffix.lower() == ".mp3"
            ]

            if not candidates:
                raise Exception("Converted MP3 file was not found")

            file_path = max(candidates, key=lambda path: path.stat().st_size)
            file_size = file_path.stat().st_size

            if file_size > MAX_DOWNLOAD_BYTES:
                raise Exception("Audio file exceeds maximum download size")

            response = send_file(
                str(file_path),
                as_attachment=True,
                download_name=file_path.name,
                mimetype="audio/mpeg"
            )
            response.call_on_close(
                lambda: shutil.rmtree(temp_dir, ignore_errors=True)
            )
            return response

        except Exception as error:
            messages = list(ydl_logger.messages)
            messages.append(str(error))
            errors.append({"client": client, "messages": messages})
            shutil.rmtree(temp_dir, ignore_errors=True)

    return jsonify({
        "error": "Audio download failed with all available clients",
        "attempts": errors
    }), 400


# =====================================================
# DOWNLOAD ROUTES
# =====================================================

@app.route("/download/<int:resolution>", methods=["POST"])
def download_route(resolution):

    return download_video(resolution)


@app.route("/download_file/<int:resolution>", methods=["POST"])
def download_file_route(resolution):

    return download_video(resolution)


@app.route("/download_audio", methods=["POST"])
def download_audio_route():

    return download_audio()


# =====================================================
# ERROR HANDLERS
# =====================================================

@app.errorhandler(404)
def not_found(error):

    return jsonify({

        "error": "Endpoint not found"

    }), 404


@app.errorhandler(405)
def method_not_allowed(error):

    return jsonify({

        "error": "Method not allowed"

    }), 405


@app.errorhandler(500)
def internal_error(error):

    logger.exception("Internal server error")

    return jsonify({

        "error": "Internal server error"

    }), 500


# =====================================================
# START SERVER
# =====================================================

if __name__ == "__main__":

    print("=" * 45)

    print("YouTube API Starting")

    print("Version:", APP_VERSION)

    print("yt-dlp:", get_yt_dlp_version())

    print("Deno:", shutil.which("deno"))

    print("FFmpeg:", shutil.which("ffmpeg"))

    print("FFprobe:", shutil.which("ffprobe"))

    print("Cookies:", bool(get_cookies_path()))

    print("EJS:", is_ejs_installed())

    print("=" * 45)

    serve(

        app,

        host="0.0.0.0",

        port=PORT,

        threads=8

    )
