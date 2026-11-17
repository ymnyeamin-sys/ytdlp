from flask import Flask, request, jsonify
from flask_cors import CORS
import yt_dlp
import re

app = Flask(__name__)
CORS(app)

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

    ydl_opts = {'format': 'best', 'skip_download': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            # সরাসরি আসল ভিডিও স্ট্রিম বা ডাউনলোড লিংক রিটার্ন করা হলো
            return jsonify({
                "message": "Download link generated successfully.",
                "download_url": info.get('url')
            }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
