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
APP_VERSION = "v3-clients-logger"


class CaptureLogger:
    """yt-dlp-er warning/error message jome rakhe, jate response-e dekhano jay."""

    def __init__(self):
        self.messages = []

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        self.messages.append(str(msg))

    def error(self, msg):
        self.messages.append(str(msg))


def is_valid_youtube_url(url):
    pattern = r"^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)[\w-]+(\S*)?$"
    return re.match(pattern, url) is not None


def get_ydl_opts(extra_opts=None):
    opts = {
        'extract_flat': False,
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
        # format na pele-o video info return korbe
        'ignore_no_formats_error': True,
        'check_formats': False,
        'logger': CaptureLogger(),
    }

    # JS runtime (node/deno) chhara kaj kore emon client gulo age try kore.
    # Request body te "clients": ["android_vr", "tv_simply"] pathiye redeploy chhara test kora jay.
    body = request.get_json(silent=True) or {}
    clients = body.get('clients') or ['android_vr', 'tv_simply']
    opts['extractor_args'] = {'youtube': {'player_client': clients}}

    if os.path.exists(COOKIE_SRC):
        tmp_cookie = '/tmp/cookies.txt'
        shutil.copy(COOKIE_SRC, tmp_cookie)
        opts['cookiefile'] = tmp_cookie

    if extra_opts:
        opts.update(extra_opts)
    return opts


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
        "files": os.listdir(BASE_DIR),
    }), 200


@app.route('/video_info', methods=['POST'])
def video_info():
    data = request.get_json() or {}
    url = data.get('url')

    if not url or not is_valid_youtube_url(url):
        return jsonify({"error": "Invalid or missing YouTube URL."}), 400

    try:
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
    data = request.get_json() or {}
    url = data.get('url')

    if not url or not is_valid_youtube_url(url):
        return jsonify({"error": "Invalid or missing YouTube URL."}), 400

    try:
        opts = get_ydl_opts()
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            # storyboard (thumbnail) format gulo bad, shudhu asol video/audio
            formats = [
                f for f in (info.get('formats') or [])
                if f.get('vcodec') != 'none' or f.get('acodec') != 'none'
            ]

            if not formats:
                return jsonify({
                    "error": "YouTube returned no real video/audio formats for this server. "
                             "Likely IP block, invalid cookies, or missing JS runtime (node/deno).",
                    "clients_used": opts['extractor_args']['youtube']['player_client'],
                    "yt_dlp_messages": opts['logger'].messages,
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
                "all": sorted_all
            }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/download/<resolution>', methods=['POST'])
def download_by_resolution(resolution):
    data = request.get_json() or {}
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
        with yt_dlp.YoutubeDL(get_ydl_opts({'format': format_str})) as ydl:
            info = ydl.extract_info(url, download=False)

            download_url = info.get('url')
            if not download_url:
                requested = info.get('requested_formats') or []
                if requested:
                    download_url = requested[0].get('url')

            if not download_url:
                return jsonify({
                    "error": "No downloadable format found. Check /debug for cookies and JS runtime."
                }), 404

            return jsonify({
                "message": "Download link generated successfully.",
                "title": info.get('title'),
                "download_url": download_url
            }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True)
