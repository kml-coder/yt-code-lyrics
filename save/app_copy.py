from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import os, re, json, statistics, difflib, socket, requests, tempfile

import yt_dlp
import whisperx
import torch
import lyricsgenius

# ----------------------------------------
# 🔧 기본 설정
# ----------------------------------------
app = Flask(__name__)
CORS(app)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

GENTLE_API = "http://localhost:8765/transcriptions?async=false"

# ✅ Genius API 토큰
GENIUS_API_TOKEN = "IF6KUc4TvVcqtGrKWDvxpFxDTENM7Vfx36QeUFDkaBXCUmu1_YdEyoZyMAzbTwM5"
genius = lyricsgenius.Genius(
    GENIUS_API_TOKEN,
    skip_non_songs=True,
    remove_section_headers=True,  # 그래도 HTML 파싱으로 다시 처리함
)

# # ✅ WhisperX 전역 모델 캐시
# device = "cuda" if torch.cuda.is_available() else "cpu"
# compute_type = "float16" if device == "cuda" else "int8"
# print(f"🧠 Initializing WhisperX on {device}...")

# asr_model = whisperx.load_model("small", device, compute_type=compute_type)
# align_model, align_meta = None, None

# ✅ YouTube ID 정규식
YOUTUBE_ID_REGEX = re.compile(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*")


def is_port_open(host="localhost", port=8765):
    """Gentle 서버가 실행 중인지 확인"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0
    
def extract_video_id(url_or_id: str) -> str:
    if re.fullmatch(r"[0-9A-Za-z_-]{11}", url_or_id):
        return url_or_id
    m = YOUTUBE_ID_REGEX.search(url_or_id)
    if m:
        return m.group(1)
    if "youtu.be/" in url_or_id:
        return url_or_id.split("youtu.be/")[-1][:11]
    raise ValueError("Invalid YouTube video link or ID.")


# ----------------------------------------
# 🎵 1. YouTube 오디오 다운로드
# ----------------------------------------
def download_audio(youtube_url, output_dir=DOWNLOAD_DIR):
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": f"{output_dir}/%(id)s.%(ext)s",
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
        "noplaylist": True,
        "quiet": True,
        "http_headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/118.0.0.0 Safari/537.36"
    }
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=True)
        file_path = os.path.join(output_dir, f"{info['id']}.wav")
        return file_path, info.get("title", "Unknown Title"), info.get("uploader", "")


# ----------------------------------------
# 🧠 2. WhisperX 타임스탬프 추출
# ----------------------------------------
# def transcribe_with_whisperx(audio_path: str):
#     global align_model, align_meta
#     print("🎙️ WhisperX: transcribing...")
#     audio = whisperx.load_audio(audio_path)
#     result = asr_model.transcribe(audio)

#     # 정렬 모델 캐시
#     if align_model is None:
#         align_model, align_meta = whisperx.load_align_model(
#             language_code=result["language"], device=device
#         )

#     aligned = whisperx.align(result["segments"], align_model, align_meta, audio, device)
#     return aligned["segments"]  # 각 segment에 words 필드 포함

def transcribe_with_gentle(audio_path: str, lyrics_text: str):
    """Gentle 서버에 오디오 + 가사를 보내 단어별 타임스탬프를 받음"""
    if not is_port_open("localhost", 8765):
        raise RuntimeError("❌ Gentle 서버가 실행 중이 아닙니다. 먼저 `docker run -p 8765:8765 lowerquality/gentle` 실행하세요.")

    print("🎙️ Gentle: aligning with forced alignment...")

    tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
    tmp.write(lyrics_text.encode("utf-8"))
    tmp.close()

    files = {
        "audio": open(audio_path, "rb"),
        "transcript": open(tmp.name, "rb"),
    }

    try:
        resp = requests.post(GENTLE_API, files=files, timeout=300)
        if resp.status_code != 200:
            raise Exception(f"Gentle alignment failed: {resp.text}")
        data = resp.json()
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Gentle 서버에 연결할 수 없습니다. Docker 컨테이너가 실행 중인지 확인하세요.")
    except requests.exceptions.ReadTimeout:
        raise RuntimeError("Gentle alignment 요청이 너무 오래 걸렸습니다. 오디오를 줄이거나 재시도하세요.")
    finally:
        try:
            os.remove(tmp.name)
        except:
            pass

    words = []
    for w in data.get("words", []):
        word_case = w.get("case")
        base_word = normalize_word(w.get("alignedWord", ""))

        if not base_word:
            continue

        # ✅ case별 처리
        if word_case == "success":
            words.append({
                "word": base_word,
                "start": round(w.get("start", 0.0), 3),
                "end": round(w.get("end", 0.0), 3),
                "found": True
            })
        elif word_case == "not-found-in-audio":
            # 직전 단어의 끝 기준으로 임시 시간 추정
            prev_end = words[-1]["end"] if words else 0.0
            est_start = round(prev_end + 0.15, 3)
            est_end = round(est_start + 0.35, 3)

            words.append({
                "word": base_word,
                "start": est_start,
                "end": est_end,
                "found": False   # 오디오에서 실제 감지되지 않음
            })


    print(f"✅ Gentle aligned {len(words)} words successfully.")
    return words


def flatten_whisper_words(segments):
    """
    WhisperX segments -> 단어 리스트 [{"word","start","end"}, ...]
    """
    words = []
    for seg in segments:
        for w in seg.get("words", []):
            # 일부 모델은 word에 공백/구두점 섞여 있을 수 있음
            txt = w.get("word", "")
            if not txt:
                continue
            words.append({
                "word": normalize_word(txt),  # 매칭용 정규화 단어
                "start": float(w.get("start", seg.get("start", 0.0))),
                "end": float(w.get("end", seg.get("end", 0.0))),
            })
    # 시작시간 기준 정렬 (안전)
    words.sort(key=lambda x: (x["start"], x["end"]))
    return words


# ----------------------------------------
# 🎤 3. Genius 가사 가져오기 (HTML 직접 파싱)
# ----------------------------------------
def get_lyrics_from_genius(title, artist=None):
    import requests
    from bs4 import BeautifulSoup

    try:
        print(f"🔎 Searching Genius for: {artist} - {title}")
        song = genius.search_song(title, artist)
        if not song:
            return None

        # Genius 실제 페이지 요청
        page = requests.get(song.url, timeout=20)
        soup = BeautifulSoup(page.text, "html.parser")

        # ✅ 모든 가사 컨테이너 선택
        containers = soup.select("div[class^='Lyrics__Container']")
        if not containers:
            print("⚠️ No Lyrics__Container found — page layout changed?")
            return None

        lines = []
        for c in containers:
            buffer = []
            for elem in c.descendants:
                # 줄바꿈 처리
                if getattr(elem, "name", None) == "br":
                    if buffer:
                        line = " ".join(buffer).strip()
                        if line:
                            lines.append(line)
                        buffer = []
                elif getattr(elem, "name", None) is None:
                    # NavigableString: 실제 텍스트
                    text = str(elem).strip()
                    if text:
                        buffer.append(text)
            # 마지막 버퍼 플러시
            if buffer:
                line = " ".join(buffer).strip()
                if line:
                    lines.append(line)

        lyrics_text = "\n".join(lines)
        lyrics_text = re.sub(r"\n{2,}", "\n", lyrics_text).strip()

        # 🔍 로깅 (원시 텍스트 미리보기)
        print("\n📝 [Raw Genius Lyrics Preview]")
        for i, line in enumerate(lyrics_text.split("\n")[:20]):
            print(f"{i+1:02d}. {line}")

        print(f"✅ Parsed {len(lines)} lines from Genius HTML\n")
        return lyrics_text

    except Exception as e:
        print("⚠️ Genius scraping error:", e)
        return None


# ----------------------------------------
# ⚙️ 4. 정규화/정렬/매칭 유틸
# ----------------------------------------
def normalize_word(w: str) -> str:
    # (대소문자/구두점/이상문자 제거) + 축약부호는 살림
    return re.sub(r"[^a-z0-9']+", "", w.lower())


def clean_lyrics_text(lyrics_text: str):
    """
    정제 규칙:
    - <br> -> \n
    - \r, \u200b 제거
    - [Chorus], [Verse 1] 등 섹션 헤더 제거 (줄 안에 있어도 삭제)
    - 'lyrics', 'contributors', 'credits', 'embed' 등 메타 제거
    - 공백 라인 제거
    """
    lyrics_text = re.sub(r"<br\s*/?>", "\n", lyrics_text)
    lyrics_text = lyrics_text.replace("\r", "\n").replace("\u200b", "")

    cleaned = []
    for line in lyrics_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # ✅ 섹션 헤더 제거: 줄 안에 포함된 [Verse], [Chorus] 도 삭제
        line = re.sub(r"\[.*?\]", "", line).strip()
        if not line:
            continue
        low = line.lower()
        if any(bad in low for bad in ["lyrics", "contributors", "credits", "embed"]):
            continue
        cleaned.append(line)

    return cleaned


def align_lyrics_lines(lyrics_lines, whisper_words, drop_threshold=0.15, min_start_score=0.4):
    """
    개선된 동적 누적 매칭 기반 가사-Whisper 정렬 (디버깅 포함)
    """
    results = []
    ww = [w["word"] for w in whisper_words]
    n = len(ww)
    used_idx = 0

    print("\n🧩 [ALIGN DEBUG START - Dynamic Progressive Matching] ---------------------------")

    for line_idx, line in enumerate(lyrics_lines):
        lyric_str = " ".join([normalize_word(x) for x in line.split() if normalize_word(x)])
        if not lyric_str:
            print(f"⚪️ [Line {line_idx}] Empty line, skipped.")
            results.append({"text": line, "start": None, "end": None})
            continue

        best_score = 0.0
        last_high_idx = None
        start_time = None
        line_done = False

        print(f"\n🎵 [Line {line_idx}] '{line}'")
        for i in range(used_idx, n):
            # 현재까지 누적 단어 구간
            segment = " ".join(ww[used_idx:i+1])
            ratio = difflib.SequenceMatcher(None, segment, lyric_str).ratio()

            # 디버깅용 로그
            print(f"   [{i:03d}] ratio={ratio:.3f} | segment='{segment[-60:]}'", end="\r")

            # 첫 시작 시점: 최소 일정 유사도 이상이면 start_time 설정
            if start_time is None and ratio >= min_start_score:
                start_time = whisper_words[used_idx]["start"]

            # 최고점 갱신
            if ratio > best_score:
                best_score = ratio
                last_high_idx = i

            # 급격한 하락 감지 → 종료
            elif best_score - ratio > drop_threshold and last_high_idx is not None:
                end_time = whisper_words[last_high_idx]["end"]
                start_val = start_time if start_time is not None else whisper_words[used_idx]["start"]
                results.append({
                    "text": line,
                    "start": round(start_val, 3),
                    "end": round(end_time, 3),
                    "score": round(best_score, 3)
                })
                print(f"\n✅ [Line {line_idx}] Done | best_score={best_score:.3f} | "
                    f"range={used_idx}-{last_high_idx} | start={start_val:.3f}, end={end_time:.3f}")
                print(f"   ↳ Matched segment: {' '.join(ww[used_idx:last_high_idx+1])}")
                used_idx = last_high_idx + 1
                line_done = True
                break


        # 루프가 끝났는데도 종료 안 됐으면 강제 종료
        if not line_done:
            if last_high_idx is not None:
                start_t = start_time or whisper_words[used_idx]["start"]
                end_t = whisper_words[last_high_idx]["end"]
                results.append({
                    "text": line,
                    "start": round(start_t, 3),
                    "end": round(end_t, 3),
                    "score": round(best_score, 3)
                })
                print(f"\n⚙️ [Line {line_idx}] Auto-finish | best_score={best_score:.3f} | "
                      f"range={used_idx}-{last_high_idx} | start={start_t:.3f}, end={end_t:.3f}")
                used_idx = last_high_idx + 1
            else:
                print(f"⚠️ [Line {line_idx}] No valid match (max ratio={best_score:.3f})")
                results.append({"text": line, "start": None, "end": None})

    print("\n🧾 [ALIGN DEBUG END] ---------------------------\n")

    # ✅ 후처리: 비어있는 라인 보간
    known = [(k, r["start"], r["end"]) for k, r in enumerate(results) if r["start"] is not None]
    for idx, r in enumerate(results):
        if r["start"] is None and known:
            prevs = [(k, s, e) for k, s, e in known if k < idx]
            nexts = [(k, s, e) for k, s, e in known if k > idx]
            if prevs and nexts:
                prev_end = prevs[-1][2]
                next_start = nexts[0][1]
                mid = round(statistics.mean([prev_end, next_start]), 3)
                r["start"], r["end"] = mid, round(mid + 2.5, 3)
                print(f"🔧 Interpolated [Line {idx}] '{r['text']}' → {r['start']}~{r['end']}")
            elif prevs:
                prev_end = prevs[-1][2]
                r["start"], r["end"] = round(prev_end + 1.0, 3), round(prev_end + 3.5, 3)
                print(f"🔧 Interpolated [Line {idx}] (after prev) '{r['text']}' → {r['start']}~{r['end']}")

    return results


# ----------------------------------------
# 🌐 Flask 엔드포인트
# ----------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/audio", methods=["GET"])
def get_audio():
    video = request.args.get("video", "").strip()
    if not video:
        return jsonify({"error": "Missing 'video' parameter"}), 400
    try:
        vid = extract_video_id(video)
        ydl_opts = {"quiet": True, "format": "bestaudio/best", "noplaylist": True, "http_headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/118.0.0.0 Safari/537.36"
    }}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
            return jsonify({
                "audio_url": info["url"],
                "title": info.get("title", "Unknown Title"),
            })
    except Exception as e:
        return jsonify({"error": f"Audio fetch failed: {str(e)}"}), 500


@app.route("/api/lyrics_timed", methods=["POST"])
def lyrics_timed():
    data = request.get_json()
    video_url = data.get("video_url")
    title = data.get("title")
    artist = data.get("artist")
    mode = data.get("mode", "auto")
    genius_url = data.get("genius_url")
    manual_lyrics = data.get("manual_lyrics")

    if not video_url:
        return jsonify({"error": "Missing 'video_url'"}), 400

    try:
        # 1) 다운로드 & 음성 인식
        audio_path, yt_title, yt_uploader = download_audio(video_url)
        print(f"✅ Downloaded: {yt_title} ({audio_path})")

        # 2) 가사 소스 결정
        if mode == "manual" and manual_lyrics:
            genius_lyrics = manual_lyrics
        elif mode == "url" and genius_url:
            # URL 직접 파싱 (위의 robust 파서 재사용)
            def fetch_genius_by_url(url: str):
                import requests
                from bs4 import BeautifulSoup
                page = requests.get(url, timeout=20)
                soup = BeautifulSoup(page.text, "html.parser")
                containers = soup.select("div[class^='Lyrics__Container']")
                if not containers:
                    return None
                lines = []
                for c in containers:
                    buf = []
                    for elem in c.descendants:
                        if getattr(elem, "name", None) == "br":
                            if buf:
                                line = " ".join(buf).strip()
                                if line:
                                    lines.append(line)
                                buf = []
                        elif getattr(elem, "name", None) is None:
                            t = str(elem).strip()
                            if t:
                                buf.append(t)
                    if buf:
                        line = " ".join(buf).strip()
                        if line:
                            lines.append(line)
                return "\n".join(lines).strip()

            genius_lyrics = fetch_genius_by_url(genius_url)
        else:
            genius_lyrics = get_lyrics_from_genius(title or yt_title, artist or yt_uploader)

        # 3) 정제 전/후 로깅
        print("\n📝 [Raw Genius Lyrics Preview]")
        if genius_lyrics:
            for i, line in enumerate(genius_lyrics.split("\n")[:20]):
                print(f"{i+1:02d}. {line}")
        else:
            print("(No lyrics retrieved from Genius)")


        lyrics_lines = clean_lyrics_text(genius_lyrics)

        whisper_words = transcribe_with_gentle("../separated/htdemucs/VoEsEC2CLgE/vocals.wav", "\n".join(lyrics_lines))
        if not whisper_words:
            print("⚠️ No word-level timestamps from WhisperX")

        print("\n🧹 [Cleaned Lyrics Preview]")
        for i, line in enumerate(lyrics_lines[:20]):
            print(f"{i+1:02d}. {line}")
        print(f"🧾 Total lines after cleaning: {len(lyrics_lines)}\n")

        # 4) 정렬
        aligned = align_lyrics_lines(lyrics_lines, whisper_words)

        # 5) 결과
        return jsonify({
            "youtube_title": yt_title,
            "artist": artist or yt_uploader,
            "lyrics_timed": aligned,  # [{line,start,end}...]
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ----------------------------------------
# 🚀 실행
# ----------------------------------------
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=3000, debug=True)