from flask import Flask, request, jsonify
from flask_cors import CORS
import yt_dlp
import re
import os
import shutil

app = Flask(__name__)
CORS(app)


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
        # player_client force kora hoyni, yt-dlp nijei best client bachbe
    }

    if os.path.exists('cookies.txt'):
        tmp_cookie = '/tmp/cookies.txt'
        shutil.copy('cookies.txt', tmp_cookie)
        opts['cookiefile'] = tmp_cookie

    if extra_opts:
        opts.update(extra_opts)
    return opts


@app.route('/', methods=['GET'])
def home():
    return jsonify({"status": "API is running successfully!"}), 200


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
        with yt_dlp.YoutubeDL(get_ydl_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get('formats') or []

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
                return jsonify({"error": "No downloadable format found for this video."}), 404

            return jsonify({
                "message": "Download link generated successfully.",
                "title": info.get('title'),
                "download_url": download_url
            }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True)
