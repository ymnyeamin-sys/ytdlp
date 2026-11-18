from flask import Flask, request, jsonify
from flask_cors import CORS
import yt_dlp
import re
import os
import shutil

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_SRC = os.path.join(BASE_DIR, 'cookies.txt')
APP_VERSION = "v6-js-debug"

# Ekta ekta kore try hobe. Prothom je real format dibe seta-i use hobe.
# web_safari + cookies age (bot-check paar hoy, kintu JS runtime lage).
# android_vr/tv_simply/ios cookies support kore na, tai cookies chhara try kora hoy.
ATTEMPTS = [
    {"clients": ["web_safari"], "cookies": True},
    {"clients": ["android_vr"], "cookies": False},
    {"clients": ["tv_simply"], "cookies": False},
    {"clients": ["ios"], "cookies": False},
]

# JS runtime: repo-r bin/qjs (QuickJS-NG, Linux x86_64) thakle use hobe.
QJS_SRC = os.path.join(BASE_DIR, 'bin', 'qjs')
QJS_TMP_DIR = '/tmp/jsrt'


def setup_js_runtime():
    """bin/qjs ke /tmp te copy kore executable banay, PATH e boshay. Path return kore, na thakle None."""
    if not os.path.exists(QJS_SRC):
        return None
    try:
        os.makedirs(QJS_TMP_DIR, exist_ok=True)
        dst = os.path.join(QJS_TMP_DIR, 'qjs')
        if not os.path.exists(dst):
            shutil.copy(QJS_SRC, dst)
            os.chmod(dst, 0o755)
        if QJS_TMP_DIR not in os.environ.get('PATH', ''):
            os.environ['PATH'] = QJS_TMP_DIR + os.pathsep + os.environ.get('PATH', '')
        return dst
    except Exception:
        return None


QJS_PATH = setup_js_runtime()


class CaptureLogger:
    """yt-dlp-er warning/error message jome rakhe, jate response-e dekhano jay."""

    KEYWORDS = ('js ', 'javascript', 'runtime', 'quickjs', 'qjs', 'ejs', 'solver', 'challenge', 'deno', 'node')

    def __init__(self):
        self.messages = []
        self.debug_msgs = []

    def debug(self, msg):
        m = str(msg)
        if any(k in m.lower() for k in self.KEYWORDS) and len(self.debug_msgs) < 40:
            self.debug_msgs.append(m[:300])

    def info(self, msg):
        pass

    def warning(self, msg):
        self.messages.append(str(msg))

    def error(self, msg):
        self.messages.append(str(msg))


def is_valid_youtube_url(url):
    pattern = r"^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)[\w-]+(\S*)?$"
    return re.match(pattern, url) is not None


def get_ydl_opts(extra_opts=None, clients=None, use_cookies=True):
    opts = {
        'extract_flat': False,
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
        # format na pele-o video info return korbe
        'ignore_no_formats_error': True,
        'check_formats': False,
        'verbose': True,  # JS runtime-er debug log paoar jonno (CaptureLogger filter kore)
        'logger': CaptureLogger(),
    }

    if QJS_PATH:
        opts['js_runtimes'] = {'quickjs': {'path': QJS_PATH}}

    if clients:
        opts['extractor_args'] = {'youtube': {'player_client': clients}}

    if use_cookies and os.path.exists(COOKIE_SRC):
        tmp_cookie = '/tmp/cookies.txt'
        shutil.copy(COOKIE_SRC, tmp_cookie)
        opts['cookiefile'] = tmp_cookie

    if extra_opts:
        opts.update(extra_opts)
    return opts


def real_formats(info):
    """storyboard (thumbnail) bad diye shudhu asol video/audio format."""
    return [
        f for f in (info.get('formats') or [])
        if f.get('vcodec') != 'none' or f.get('acodec') != 'none'
    ]


def extract_with_fallback(url, extra_opts=None):
    """Attempt-gulo ekta ekta kore chalay. Return: (info, formats, log)."""
    body = request.get_json(silent=True) or {}

    # Request body te "clients" pathale shudhu oigulo try hobe (test-er jonno)
    if body.get('clients'):
        attempts = [{
            "clients": body['clients'],
            "cookies": bool(body.get('use_cookies', False)),
        }]
    else:
        attempts = ATTEMPTS

    log = []
    for att in attempts:
        opts = get_ydl_opts(extra_opts, att['clients'], att['cookies'])
        entry = {"clients": att['clients'], "cookies": att['cookies']}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            formats = real_formats(info)
            entry["messages"] = opts['logger'].messages
            entry["js_debug"] = opts['logger'].debug_msgs
            entry["real_formats"] = len(formats)
            log.append(entry)
            if formats:
                return info, formats, log
        except Exception as e:
            entry["error"] = str(e)
            entry["messages"] = opts['logger'].messages
            log.append(entry)

    return None, [], log


@app.route('/', methods=['GET'])
def home():
    return jsonify({"status": "API is running successfully!"}), 200


@app.route('/debug', methods=['GET'])
def debug():
    return jsonify({
        "app_version": APP_VERSION,
        "yt_dlp_version": yt_dlp.version.__version__,
        "cookies_exists": os.path.exists(COOKIE_SRC),
        "cookies_size": os.path.getsize(COOKIE_SRC) if os.path.exists(COOKIE_SRC) else None,
        "node": shutil.which('node'),
        "deno": shutil.which('deno'),
        "qjs_in_repo": os.path.exists(QJS_SRC),
        "qjs_ready": QJS_PATH,
        "ejs_installed": __import__('importlib.util').util.find_spec('yt_dlp_ejs') is not None,
        "files": os.listdir(BASE_DIR),
    }), 200


@app.route('/debug_js', methods=['GET'])
def debug_js():
    """qjs binary sotti ei server-e chole kina test kore."""
    import subprocess
    info = {"qjs_path": QJS_PATH, "path_env": os.environ.get('PATH', '')[:200]}
    if QJS_PATH:
        try:
            info["size_bytes"] = os.path.getsize(QJS_PATH)
            with open(QJS_PATH, 'rb') as f:
                info["first_bytes_hex"] = f.read(4).hex()  # Linux binary hole 7f454c46
            p = subprocess.run([QJS_PATH, '-e', 'console.log(1+1)'],
                               capture_output=True, text=True, timeout=10)
            info["returncode"] = p.returncode
            info["stdout"] = p.stdout[-300:]
            info["stderr"] = p.stderr[-300:]
            p2 = subprocess.run([QJS_PATH, '--help'], capture_output=True, text=True, timeout=10)
            info["help_first_line"] = (p2.stdout or p2.stderr).splitlines()[:2]
        except Exception as e:
            info["error"] = repr(e)
    return jsonify(info), 200


@app.route('/video_info', methods=['POST'])
def video_info():
    data = request.get_json(silent=True) or {}
    url = data.get('url')

    if not url or not is_valid_youtube_url(url):
        return jsonify({"error": "Invalid or missing YouTube URL."}), 400

    try:
        # yt-dlp-er default client + cookies (ager moto, ei ta kaj korechhilo)
        with yt_dlp.YoutubeDL(get_ydl_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
            video_data = {
                "title": info.get('title'),
                "author": info.get('uploader') or info.get('channel'),
                "length": info.get('duration'),
                "views": info.get('view_count'),
                "thumbnail": info.get('thumbnail'),
                "description": info.get('description'),
                "publish_date": info.get('upload_date'),
            }
            return jsonify(video_data), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/available_resolutions', methods=['POST'])
def available_resolutions():
    data = request.get_json(silent=True) or {}
    url = data.get('url')

    if not url or not is_valid_youtube_url(url):
        return jsonify({"error": "Invalid or missing YouTube URL."}), 400

    try:
        info, formats, log = extract_with_fallback(url)

        if not formats:
            return jsonify({
                "error": "No real video/audio formats from any client. "
                         "Likely IP block or missing JS runtime (node/deno).",
                "attempts": log,
                "progressive": [],
                "all": []
            }), 502

        progressive_res = set()
        all_res = set()

        for f in formats:
            if f.get('vcodec') != 'none' and f.get('height'):
                res_str = f"{f.get('height')}p"
                all_res.add(res_str)
                # progressive = video ar audio ekshathe
                if f.get('acodec') != 'none':
                    progressive_res.add(res_str)

        sorted_prog = sorted(progressive_res, key=lambda x: int(x.replace('p', '')))
        sorted_all = sorted(all_res, key=lambda x: int(x.replace('p', '')))

        return jsonify({
            "progressive": sorted_prog,
            "all": sorted_all,
            "worked_with": log[-1]["clients"]
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/download/<resolution>', methods=['POST'])
def download_by_resolution(resolution):
    data = request.get_json(silent=True) or {}
    url = data.get('url')

    if not url or not is_valid_youtube_url(url):
        return jsonify({"error": "Invalid or missing YouTube URL."}), 400

    # format selection ar fallback
    if resolution == "audio":
        format_str = "bestaudio[ext=m4a]/bestaudio/best"
    elif resolution == "highest":
        format_str = "best[ext=mp4]/best"
    else:
        res_height = resolution.replace("p", "")
        if not res_height.isdigit():
            return jsonify({"error": "Invalid resolution."}), 400
        format_str = f"best[height<={res_height}][ext=mp4]/best[height<={res_height}]/best"

    try:
        info, formats, log = extract_with_fallback(url, {'format': format_str})

        if not formats:
            return jsonify({
                "error": "No downloadable format from any client.",
                "attempts": log
            }), 502

        download_url = info.get('url')
        if not download_url:
            requested = info.get('requested_formats') or []
            if requested:
                download_url = requested[0].get('url')

        if not download_url:
            return jsonify({
                "error": "Formats found but no direct URL for this selection.",
                "attempts": log
            }), 404

        return jsonify({
            "message": "Download link generated successfully.",
            "title": info.get('title'),
            "download_url": download_url,
            "worked_with": log[-1]["clients"]
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True)
