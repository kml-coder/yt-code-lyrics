from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
# from youtube_transcript_api import YouTubeTranscriptApi, _errors  # 🔕 transcript 관련 임시 비활성화
import yt_dlp, whisper, re, os, allin1

app = Flask(__name__)
CORS(app)

YOUTUBE_ID_REGEX = re.compile(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*")


def extract_video_id(url_or_id: str) -> str:
    """Extract 11-character YouTube video ID from a URL or return the ID if given directly."""
    if re.fullmatch(r"[0-9A-Za-z_-]{11}", url_or_id):
        return url_or_id
    m = YOUTUBE_ID_REGEX.search(url_or_id)
    if m:
        return m.group(1)
    if "youtu.be/" in url_or_id:
        return url_or_id.split("youtu.be/")[-1][:11]
    raise ValueError("Invalid YouTube video link or ID.")


@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------
# 🚫 Transcript API (임시 비활성화)
# ---------------------------------------------------------------------
"""
@app.route("/api/transcript", methods=["GET"])
def get_transcript():
    # Fetch English transcript for a given YouTube video.
    video = request.args.get("video", "").strip()
    langs = request.args.get("langs", "en").split(",")

    if not video:
        return jsonify({"error": "Missing 'video' parameter"}), 400

    try:
        vid = extract_video_id(video)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        raw_data = YouTubeTranscriptApi.get_transcript(vid, languages=langs)
        return jsonify({"videoId": vid, "segments": raw_data})
    except _errors.TranscriptsDisabled:
        return jsonify({"error": "Captions are disabled for this video."}), 403
    except _errors.NoTranscriptFound:
        return jsonify({"error": "No transcript found for this video."}), 404
    except _errors.VideoUnavailable:
        return jsonify({"error": "Video is unavailable or restricted."}), 404
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
"""


# ---------------------------------------------------------------------
# 🎵 Audio Fetch (yt_dlp)
# ---------------------------------------------------------------------
@app.route("/api/audio", methods=["GET"])
def get_audio():
    """Fetch direct audio stream URL using yt_dlp."""
    video = request.args.get("video", "").strip()
    if not video:
        return jsonify({"error": "Missing 'video' parameter"}), 400
    try:
        vid = extract_video_id(video)
        ydl_opts = {"quiet": True, "format": "bestaudio/best", "noplaylist": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
            return jsonify({
                "audio_url": info["url"],
                "title": info.get("title", "Unknown Title")
            })
    except Exception as e:
        return jsonify({"error": f"Audio fetch failed: {str(e)}"}), 500


# ---------------------------------------------------------------------
# 🧠 Whisper + All-In-One 구조 분석기 통합
# ---------------------------------------------------------------------
def download_audio(youtube_url, output_dir="downloads"):
    os.makedirs(output_dir, exist_ok=True)
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": f"{output_dir}/%(id)s.%(ext)s",
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=True)
        return os.path.join(output_dir, f"{info['id']}.wav"), info["id"]


def transcribe_audio(audio_path):
    model = whisper.load_model("small")
    result = model.transcribe(audio_path, verbose=False)
    return result["segments"]


def analyze_music_structure(audio_path):
    """Use All-In-One model for full structure analysis."""
    result = allin1.analyze(audio_path)
    return result.segments  # List of Segment(start, end, label)


def align_segments(lyrics_segments, music_segments):
    """Align Whisper transcript segments with musical structure."""
    aligned = []
    for seg in lyrics_segments:
        start = seg["start"]
        label = None
        for mseg in music_segments:
            if mseg.start <= start < mseg.end:
                label = mseg.label
                break
        aligned.append({
            "section": label or "unknown",
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"],
        })
    return aligned


@app.route("/api/lyrics_structure", methods=["GET"])
def get_lyrics_structure():
    """Full lyrics + structure analysis."""
    url = request.args.get("video", "").strip()
    if not url:
        return jsonify({"error": "Missing YouTube URL"}), 400

    try:
        # Step 1: download
        audio_path, vid = download_audio(url)

        # Step 2: whisper transcription
        whisper_segments = transcribe_audio(audio_path)

        # Step 3: all-in-one music structure
        structure_segments = analyze_music_structure(audio_path)

        # Step 4: align
        aligned = align_segments(whisper_segments, structure_segments)

        # Step 5: respond
        return jsonify({
            "videoId": vid,
            "lyrics_with_structure": aligned,
            "structure_summary": [vars(s) for s in structure_segments],
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=3000, debug=True)
