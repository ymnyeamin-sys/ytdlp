
import os
import re
import shutil
import tempfile
import importlib.util
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp


# =========================================================
# APPLICATION CONFIGURATION
# =========================================================

app = Flask(__name__)
CORS(app)

APP_VERSION = "v10-merged-multi-client"

BASE_DIR = Path(__file__).resolve().parent

COOKIE_PATH = Path(
    os.environ.get(
        "YOUTUBE_COOKIES_PATH",
        str(BASE_DIR / "cookies.txt")
    )
)

MAX_DOWNLOAD_MB = int(
    os.environ.get("MAX_DOWNLOAD_MB", "500")
)

SOCKET_TIMEOUT = 25

ATTEMPTS = [
    {
        "clients": ["web_safari"],
        "cookies": True
    },
    {
        "clients": ["android_vr"],
        "cookies": False
    },
    {
        "clients": ["tv_simply"],
        "cookies": False
    },
    {
        "clients": ["ios"],
        "cookies": False
    }
]


# =========================================================
# LOGGER
# =========================================================

class CaptureLogger:

    KEYWORDS = (
        "javascript",
        "runtime",
        "quickjs",
        "qjs",
        "ejs",
        "challenge",
        "deno",
        "node"
    )

    def __init__(self):
        self.messages = []
        self.debug_msgs = []

    def debug(self, msg):

        value = str(msg)

        if any(
            keyword in value.lower()
            for keyword in self.KEYWORDS
        ):

            if len(self.debug_msgs) < 30:
                self.debug_msgs.append(value[:300])

    def info(self, msg):
        pass

    def warning(self, msg):

        if len(self.messages) < 30:
            self.messages.append(str(msg)[:500])

    def error(self, msg):

        if len(self.messages) < 30:
            self.messages.append(str(msg)[:500])


# =========================================================
# URL VALIDATION
# =========================================================

def is_valid_youtube_url(url):

    if not isinstance(url, str):
        return False

    try:

        parsed = urlparse(url.strip())

        host = (parsed.hostname or "").lower()

        allowed_hosts = (
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "music.youtube.com",
            "youtu.be",
            "www.youtu.be"
        )

        if host not in allowed_hosts:
            return False

        if parsed.scheme not in ("http", "https"):
            return False

        if host.endswith("youtu.be"):
            return bool(parsed.path.strip("/"))

        if parsed.path == "/watch":
            return bool(
                parsed.query
                and re.search(
                    r"(?:^|&)v=[\w-]+",
                    parsed.query
                )
            )

        if re.match(
            r"^/(shorts|live|embed)/[\w-]+",
            parsed.path
        ):
            return True

        return False

    except Exception:
        return False


def request_url():

    data = request.get_json(silent=True) or {}

    url = data.get("url")

    if not is_valid_youtube_url(url):
        return None

    return url.strip()


# =========================================================
# YT-DLP OPTIONS
# =========================================================

def get_ydl_opts(
    extra_opts=None,
    clients=None,
    use_cookies=True,
    logger=None
):

    opts = {

        "quiet": True,
        "no_warnings": True,

        "ignore_no_formats_error": True,
        "check_formats": False,

        "noplaylist": True,

        "socket_timeout": SOCKET_TIMEOUT,

        "retries": 2,
        "fragment_retries": 2,

        "logger": logger or CaptureLogger(),

    }

    if clients:

        opts["extractor_args"] = {

            "youtube": {

                "player_client": clients

            }

        }

    if use_cookies and COOKIE_PATH.is_file():

        opts["cookiefile"] = str(COOKIE_PATH)

    if extra_opts:

        opts.update(extra_opts)

    return opts


# =========================================================
# FORMAT HELPERS
# =========================================================

def real_formats(info):

    return [

        f for f in (info.get("formats") or [])

        if (
            f.get("vcodec") not in (None, "none")
            or f.get("acodec") not in (None, "none")
        )

    ]


def is_video_format(f):

    return (
        f.get("vcodec") not in (None, "none")
        and bool(f.get("height"))
    )


def has_audio(f):

    return f.get("acodec") not in (None, "none")


# =========================================================
# SINGLE CLIENT FALLBACK EXTRACTION
# =========================================================

def extract_with_fallback(url, extra_opts=None):

    body = request.get_json(silent=True) or {}

    if body.get("clients"):

        attempts = [

            {
                "clients": body["clients"],
                "cookies": bool(
                    body.get("use_cookies", False)
                )
            }

        ]

    else:

        attempts = ATTEMPTS

    logs = []

    for attempt in attempts:

        logger = CaptureLogger()

        opts = get_ydl_opts(
            extra_opts=extra_opts,
            clients=attempt["clients"],
            use_cookies=attempt["cookies"],
            logger=logger
        )

        entry = {

            "clients": attempt["clients"],
            "cookies": attempt["cookies"]

        }

        try:

            with yt_dlp.YoutubeDL(opts) as ydl:

                info = ydl.extract_info(
                    url,
                    download=False
                )

            formats = real_formats(info)

            entry["real_formats"] = len(formats)

            entry["messages"] = logger.messages

            entry["js_debug"] = logger.debug_msgs

            logs.append(entry)

            if formats:

                return info, formats, logs

        except Exception as exc:

            entry["error"] = str(exc)[:700]

            entry["messages"] = logger.messages

            logs.append(entry)

    return None, [], logs


# =========================================================
# MULTI CLIENT EXTRACTION
# =========================================================

def extract_all_clients(url):

    merged_formats = {}

    logs = []

    first_info = None

    successful_clients = []

    for attempt in ATTEMPTS:

        logger = CaptureLogger()

        opts = get_ydl_opts(

            clients=attempt["clients"],

            use_cookies=attempt["cookies"],

            logger=logger

        )

        entry = {

            "clients": attempt["clients"],
            "cookies": attempt["cookies"]

        }

        try:

            with yt_dlp.YoutubeDL(opts) as ydl:

                info = ydl.extract_info(
                    url,
                    download=False
                )

            if first_info is None:
                first_info = info

            formats = info.get("formats") or []

            video_formats = [

                f for f in formats

                if is_video_format(f)

            ]

            entry["total_formats"] = len(formats)

            entry["video_formats"] = len(video_formats)

            entry["messages"] = logger.messages

            entry["js_debug"] = logger.debug_msgs

            if video_formats:

                successful_clients.append(
                    attempt["clients"][0]
                )

            for f in video_formats:

                key = (

                    f.get("format_id"),

                    f.get("height"),

                    f.get("ext"),

                    f.get("vcodec"),

                    f.get("acodec"),

                    f.get("protocol"),

                    f.get("url")

                )

                merged_formats[key] = f

        except Exception as exc:

            entry["error"] = str(exc)[:700]

            entry["messages"] = logger.messages

        logs.append(entry)

    return (

        first_info,

        list(merged_formats.values()),

        logs,

        successful_clients

    )


# =========================================================
# RESOLUTION
# =========================================================

def resolution_height(resolution):

    if resolution == "highest":
        return None

    value = str(resolution).lower().removesuffix("p")

    if not value.isdigit():

        raise ValueError("Invalid resolution.")

    height = int(value)

    if not 144 <= height <= 4320:

        raise ValueError(
            "Resolution must be between 144p and 4320p."
        )

    return height


def format_selector(height):

    if height is None:

        return (

            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"

            "bestvideo+bestaudio/best"

        )

    return (

        f"bestvideo[height<={height}][ext=mp4]+"

        "bestaudio[ext=m4a]/"

        f"bestvideo[height<={height}]+bestaudio/"

        f"best[height<={height}]"

    )


# =========================================================
# FILE INFORMATION
# =========================================================

def _file_info(f):

    return {

        "url": f.get("url"),

        "height": f.get("height"),

        "ext": f.get("ext"),

        "vcodec": f.get("vcodec"),

        "acodec": f.get("acodec"),

        "bitrate_kbps": (
            f.get("tbr") or f.get("abr")
        ),

        "filesize": (
            f.get("filesize")
            or f.get("filesize_approx")
        ),

        "format_id": f.get("format_id")

    }


# =========================================================
# HOME
# =========================================================

@app.get("/")
def home():

    return jsonify({

        "status": "API is running successfully!",

        "app_version": APP_VERSION

    })


# =========================================================
# HEALTH
# =========================================================

@app.get("/health")
def health():

    return jsonify({

        "status": "healthy"

    }), 200


# =========================================================
# DEBUG
# =========================================================

@app.get("/debug")
def debug():

    return jsonify({

        "app_version": APP_VERSION,

        "yt_dlp_version": yt_dlp.version.__version__,

        "ffmpeg": shutil.which("ffmpeg"),

        "ffprobe": shutil.which("ffprobe"),

        "node": shutil.which("node"),

        "deno": shutil.which("deno"),

        "yt_dlp_ejs_installed": (
            importlib.util.find_spec("yt_dlp_ejs")
            is not None
        ),

        "cookies_exists": COOKIE_PATH.is_file(),

        "cookies_size": (

            COOKIE_PATH.stat().st_size

            if COOKIE_PATH.is_file()

            else None

        ),

        "max_download_mb": MAX_DOWNLOAD_MB,

        "clients": ATTEMPTS

    })


# =========================================================
# VIDEO INFORMATION
# =========================================================

@app.post("/video_info")
def video_info():

    url = request_url()

    if not url:

        return jsonify({

            "error": "Invalid or missing YouTube URL."

        }), 400

    try:

        info, formats, logs = extract_with_fallback(url)

        if not info:

            return jsonify({

                "error": "Could not retrieve video information.",

                "attempts": logs

            }), 502

        return jsonify({

            "title": info.get("title"),

            "author": (
                info.get("uploader")
                or info.get("channel")
            ),

            "length": info.get("duration"),

            "views": info.get("view_count"),

            "thumbnail": info.get("thumbnail"),

            "description": info.get("description"),

            "publish_date": info.get("upload_date")

        })

    except Exception as exc:

        return jsonify({

            "error": str(exc)[:1000]

        }), 500


# =========================================================
# AVAILABLE RESOLUTIONS - MULTI CLIENT
# =========================================================

@app.post("/available_resolutions")
def available_resolutions():

    url = request_url()

    if not url:

        return jsonify({

            "error": "Invalid or missing YouTube URL."

        }), 400

    try:

        (
            info,
            formats,
            logs,
            successful_clients

        ) = extract_all_clients(url)

        if not formats:

            return jsonify({

                "error": "No video formats were returned.",

                "attempts": logs,

                "progressive": [],

                "all": [],

                "details": []

            }), 502

        all_res = set()

        progressive_res = set()

        details = []

        seen_details = set()

        for f in formats:

            height = f.get("height")

            if not height:
                continue

            label = f"{height}p"

            all_res.add(label)

            audio = has_audio(f)

            if audio:

                progressive_res.add(label)

            detail_key = (

                height,

                f.get("ext"),

                f.get("protocol"),

                audio

            )

            if detail_key in seen_details:
                continue

            seen_details.add(detail_key)

            details.append({

                "height": height,

                "resolution": label,

                "ext": f.get("ext"),

                "protocol": f.get("protocol"),

                "video": True,

                "audio": audio

            })

        all_sorted = sorted(

            all_res,

            key=lambda x: int(x[:-1])

        )

        progressive_sorted = sorted(

            progressive_res,

            key=lambda x: int(x[:-1])

        )

        details.sort(

            key=lambda x: (

                x["height"],

                x["audio"]

            ),

            reverse=True

        )

        return jsonify({

            "title": (
                info.get("title") if info else None
            ),

            "progressive": progressive_sorted,

            "all": all_sorted,

            "max_resolution": (

                all_sorted[-1] if all_sorted else None

            ),

            "worked_with": successful_clients,

            "client_count": len(successful_clients),

            "details": details,

            "attempts": logs

        })

    except Exception as exc:

        return jsonify({

            "error": str(exc)[:1000]

        }), 500


# =========================================================
# DIRECT DOWNLOAD LINK
# =========================================================

@app.post("/download/<resolution>")
def download_link(resolution):

    url = request_url()

    if not url:

        return jsonify({

            "error": "Invalid or missing YouTube URL."

        }), 400

    try:

        height = resolution_height(resolution)

    except ValueError as exc:

        return jsonify({

            "error": str(exc)

        }), 400

    selector = format_selector(height)

    try:

        info, formats, logs = extract_with_fallback(

            url,

            {"format": selector}

        )

        if not info:

            return jsonify({

                "error": "Could not select a downloadable format.",

                "attempts": logs

            }), 502

        direct_url = info.get("url")

        if not direct_url:

            requested = info.get("requested_formats") or []

            if requested:

                direct_url = requested[0].get("url")

        if not direct_url:

            return jsonify({

                "error": (
                    "Selected format may contain separate streams. "
                    "Use the merged MP4 endpoint."
                ),

                "title": info.get("title")

            }), 409

        return jsonify({

            "message": "Download link generated successfully.",

            "title": info.get("title"),

            "download_url": direct_url,

            "worked_with": (
                logs[-1]["clients"] if logs else None
            ),

            "selected": {

                "height": info.get("height"),

                "ext": info.get("ext"),

                "protocol": info.get("protocol"),

                "video": info.get("vcodec") != "none",

                "audio": info.get("acodec") != "none",

                "format_id": info.get("format_id")

            }

        })

    except Exception as exc:

        return jsonify({

            "error": str(exc)[:1000]

        }), 500


# =========================================================
# MERGED MP4 DOWNLOAD
# =========================================================

@app.post("/download_file/<resolution>")
def download_merged_mp4(resolution):

    url = request_url()

    if not url:

        return jsonify({

            "error": "Invalid or missing YouTube URL."

        }), 400

    if not shutil.which("ffmpeg"):

        return jsonify({

            "error": "FFmpeg is not installed on the server."

        }), 503

    try:

        height = resolution_height(resolution)

    except ValueError as exc:

        return jsonify({

            "error": str(exc)

        }), 400

    temp_dir = Path(

        tempfile.mkdtemp(prefix="ytdlp_")

    )

    selector = format_selector(height)

    logs = []

    try:

        downloaded_info = None

        for attempt in ATTEMPTS:

            logger = CaptureLogger()

            opts = get_ydl_opts(

                extra_opts={

                    "format": selector,

                    "outtmpl": str(

                        temp_dir
                        / "%(title).120B [%(id)s].%(ext)s"

                    ),

                    "merge_output_format": "mp4",

                    "noplaylist": True,

                    "continuedl": False,

                    "overwrites": True,

                    "max_filesize": (
                        MAX_DOWNLOAD_MB * 1024 * 1024
                    )

                },

                clients=attempt["clients"],

                use_cookies=attempt["cookies"],

                logger=logger

            )

            entry = {

                "clients": attempt["clients"],

                "cookies": attempt["cookies"]

            }

            try:

                with yt_dlp.YoutubeDL(opts) as ydl:

                    downloaded_info = ydl.extract_info(

                        url,

                        download=True

                    )

                entry["messages"] = logger.messages

                logs.append(entry)

                break

            except Exception as exc:

                entry["error"] = str(exc)[:700]

                entry["messages"] = logger.messages

                logs.append(entry)

                downloaded_info = None

                # Remove incomplete files before next attempt.

                for p in temp_dir.iterdir():

                    if p.is_file():

                        try:
                            p.unlink()
                        except OSError:
                            pass

        candidates = [

            p for p in temp_dir.iterdir()

            if p.is_file()

            and p.suffix.lower() == ".mp4"

        ]

        if not candidates:

            candidates = [

                p for p in temp_dir.iterdir()

                if p.is_file()

                and p.suffix.lower() not in (

                    ".part",

                    ".ytdl"

                )

            ]

        if not candidates:

            shutil.rmtree(

                temp_dir,

                ignore_errors=True

            )

            return jsonify({

                "error": "Download or merge failed.",

                "attempts": logs

            }), 502

        output = max(

            candidates,

            key=lambda p: p.stat().st_size

        )

        if output.stat().st_size > (

            MAX_DOWNLOAD_MB * 1024 * 1024

        ):

            shutil.rmtree(

                temp_dir,

                ignore_errors=True

            )

            return jsonify({

                "error": (
                    f"File exceeds {MAX_DOWNLOAD_MB} MB limit."
                )

            }), 413

        title = (

            (downloaded_info or {}).get("title")

            or output.stem

        )

        safe_title = re.sub(

            r'[\\/:*?"<>|]+',

            "_",

            title

        ).strip(" .")[:120] or "video"

        filename = (

            f"{safe_title}.mp4"

            if output.suffix.lower() == ".mp4"

            else output.name

        )

        response = send_file(

            output,

            as_attachment=True,

            download_name=filename,

            mimetype=(

                "video/mp4"

                if output.suffix.lower() == ".mp4"

                else "application/octet-stream"

            ),

            conditional=True

        )

        response.call_on_close(

            lambda: shutil.rmtree(

                temp_dir,

                ignore_errors=True

            )

        )

        return response

    except Exception as exc:

        shutil.rmtree(

            temp_dir,

            ignore_errors=True

        )

        return jsonify({

            "error": str(exc)[:1000],

            "attempts": logs

        }), 500


# =========================================================
# STREAMS API
# =========================================================

@app.post("/streams/<resolution>")
def streams(resolution):

    url = request_url()

    if not url:

        return jsonify({

            "error": "Invalid or missing YouTube URL."

        }), 400

    try:

        target = resolution_height(resolution)

        info, formats, logs = extract_with_fallback(url)

        if not formats:

            return jsonify({

                "error": "No formats from any client.",

                "attempts": logs

            }), 502

        direct = [

            f for f in formats

            if f.get("protocol") == "https"

            and f.get("url")

        ]

        def within(f):

            return (

                target is None

                or (f.get("height") or 0) <= target

            )

        def vkey(f):

            return (

                f.get("height") or 0,

                f.get("ext") == "mp4",

                f.get("tbr") or 0

            )

        video_only = [

            f for f in direct

            if f.get("vcodec") not in (None, "none")

            and f.get("acodec") in (None, "none")

            and f.get("height")

            and within(f)

        ]

        progressive = [

            f for f in direct

            if f.get("vcodec") not in (None, "none")

            and f.get("acodec") not in (None, "none")

            and f.get("height")

            and within(f)

        ]

        audio_only = [

            f for f in direct

            if f.get("acodec") not in (None, "none")

            and f.get("vcodec") in (None, "none")

        ]

        best_video = (

            max(video_only, key=vkey)

            if video_only else None

        )

        best_prog = (

            max(progressive, key=vkey)

            if progressive else None

        )

        best_audio = (

            max(

                audio_only,

                key=lambda f: (

                    f.get("ext") == "m4a",

                    f.get("abr") or 0

                )

            )

            if audio_only else None

        )

        if not best_video and not best_prog:

            return jsonify({

                "error": "No direct video format found.",

                "attempts": logs

            }), 404

        merge_required = bool(

            best_video

            and best_audio

            and (

                not best_prog

                or (

                    (best_video.get("height") or 0)

                    > (best_prog.get("height") or 0)

                )

            )

        )

        return jsonify({

            "title": info.get("title"),

            "requested": resolution,

            "merge_required": merge_required,

            "video": (

                _file_info(best_video)

                if best_video else None

            ),

            "audio": (

                _file_info(best_audio)

                if best_audio else None

            ),

            "progressive": (

                _file_info(best_prog)

                if best_prog else None

            ),

            "worked_with": (
                logs[-1]["clients"] if logs else None
            )

        })

    except ValueError as exc:

        return jsonify({

            "error": str(exc)

        }), 400

    except Exception as exc:

        return jsonify({

            "error": str(exc)[:1000]

        }), 500


# =========================================================
# START SERVER
# =========================================================

if __name__ == "__main__":

    from waitress import serve

    serve(

        app,

        host="0.0.0.0",

        port=int(

            os.environ.get("PORT", "8080")

        )

    )
