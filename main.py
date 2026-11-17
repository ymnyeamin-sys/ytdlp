from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp
import os
import re

app = Flask(__name__)
CORS(app)

DOWNLOAD_FOLDER = "./downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

def is_valid_youtube_url(url):
    pattern = r"^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w-]+(\S*)?$"
    return re.match(pattern, url) is not None

@app.route('/video_info', methods=['POST'])
def video_info():
    data = request.get_json()
    url = data.get('url')
    
    if not url or not is_valid_youtube_url(url):
        return jsonify({"error": "Invalid or missing YouTube URL."}), 400

    ydl_opts = {'extract_flat': False, 'skip_download': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            video_data = {
                "title": info.get('title'),
                "author": info.get('uploader') or info.get('channel'),
                "length": info.get('duration'),
                "views": info.get('view_count'),
                "description": info.get('description'),
                "publish_date": info.get('upload_date'),
            }
            return jsonify(video_data), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/available_resolutions', methods=['POST'])
def available_resolutions():
    data = request.get_json()
    url = data.get('url')
    
    if not url or not is_valid_youtube_url(url):
        return jsonify({"error": "Invalid or missing YouTube URL."}), 400

    ydl_opts = {'extract_flat': False, 'skip_download': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get('formats', [])
            
            resolutions = set()
            for f in formats:
                if f.get('vcodec') != 'none' and f.get('resolution'):
                    resolutions.add(f.get('resolution'))
                    
            return jsonify({
                "progressive": sorted(list(resolutions)),
                "all": sorted(list(resolutions))
            }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/download/<resolution>', methods=['POST'])
def download_by_resolution(resolution):
    data = request.get_json()
    url = data.get('url')
    
    if not url or not is_valid_youtube_url(url):
        return jsonify({"error": "Invalid or missing YouTube URL."}), 400

    video_id = info_id = url.split("v=")[-1].split("&")[0] if "v=" in url else url.split("/")[-1]
    output_template = os.path.join(DOWNLOAD_FOLDER, f"{video_id}_%(resolution)s.%(ext)s")

    # yt-dlp format mapping
    if resolution == "highest":
        format_str = "bestvideo+bestaudio/best"
    elif resolution == "audio":
        format_str = "bestaudio/best"
    else:
        # যেমন 720p বা 360p এর জন্য
        res_height = resolution.replace("p", "")
        format_str = f"bestvideo[height<={res_height}]+bestaudio/best[height<={res_height}]"

    ydl_opts = {
        'format': format_str,
        'outtmpl': output_template,
        'merge_output_format': 'mp4',
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if not filename.endswith('.mp4'):
                filename = os.path.splitext(filename)[0] + '.mp4'
            
            if os.path.exists(filename):
                return send_file(filename, as_attachment=True)
            else:
                return jsonify({"error": "Downloaded file not found."}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
