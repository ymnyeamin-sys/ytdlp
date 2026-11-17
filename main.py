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
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios']
            }
        }
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
            formats = info.get('formats', [])
            
            resolutions = set()
            for f in formats:
                if f.get('vcodec') != 'none' and f.get('height'):
                    resolutions.add(f"{f.get('height')}p")
            
            sorted_res = sorted(list(resolutions), key=lambda x: int(x.replace('p', '')))
            return jsonify({
                "progressive": sorted_res,
                "all": sorted_res
            }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/download/<resolution>', methods=['POST'])
def download_by_resolution(resolution):
    data = request.get_json() or {}
    url = data.get('url')
    
    if not url or not is_valid_youtube_url(url):
        return jsonify({"error": "Invalid or missing YouTube URL."}), 400

    # একেবারে নিরাপদ ফলব্যাক ফরম্যাট (কোনো ত্রুটি আসবে না)
    if resolution == "audio":
        format_str = "bestaudio/best"
    else:
        format_str = "best/bestvideo+bestaudio"

    try:
        with yt_dlp.YoutubeDL(get_ydl_opts({'format': format_str})) as ydl:
            info = ydl.extract_info(url, download=False)
            return jsonify({
                "message": "Download link generated successfully.",
                "title": info.get('title'),
                "download_url": info.get('url')
            }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
