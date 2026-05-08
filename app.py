from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import os, re, json, statistics, difflib
import numpy as np

import requests
from bs4 import BeautifulSoup
import yt_dlp
import whisperx
import torch

import demucs.separate
import shlex

# ----------------------------------------
# 🔧 기본 설정
# ----------------------------------------
app = Flask(__name__)
CORS(app)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ✅ WhisperX 전역 모델 캐시
device = "cuda" if torch.cuda.is_available() else "cpu"
compute_type = "float16" if device == "cuda" else "int8"
print(f"🧠 Initializing WhisperX on {device}...")

asr_model = whisperx.load_model("small", device, compute_type=compute_type)
align_model, align_meta = None, None


# ----------------------------------------
# 🎵 1. YouTube 오디오 다운로드
# ----------------------------------------

def extract_video_id(url_or_id: str) -> str:
    if re.fullmatch(r"[0-9A-Za-z_-]{11}", url_or_id):
        return url_or_id
    YOUTUBE_ID_REGEX = re.compile(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*")
    m = YOUTUBE_ID_REGEX.search(url_or_id)
    if m:
        return m.group(1)
    if "youtu.be/" in url_or_id:
        return url_or_id.split("youtu.be/")[-1][:11]
    raise ValueError("Invalid YouTube video link or ID.")

def download_audio(youtube_url, output_dir=DOWNLOAD_DIR):
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": f"{output_dir}/%(id)s.%(ext)s",
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
        "noplaylist": True,
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=True)
        file_path = os.path.join(output_dir, f"{info['id']}.wav")
        return file_path, info.get("title", "Unknown Title"), info.get("uploader", ""), info["id"]


def seperate_audio(audio_path: str):

    # 출력 폴더 지정 (자동 생성)
    output_dir = "demucs_wav"
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(audio_path))[0]

    # CLI 명령 구성
    cmd = f'-n htdemucs --two-stems vocals -o "{output_dir}" "{audio_path}"'

    # 실행 (shlex.split으로 CLI 문자열을 파싱)
    demucs.separate.main(shlex.split(cmd))

    vocal_path = os.path.join(output_dir,"htdemucs",base_name,"vocals.wav")
    print(f"✅ 분리 완료! 결과 파일의 주소는 '{vocal_path}' 입니다.")

    return vocal_path



# ----------------------------------------
# 🧠 2. WhisperX 타임스탬프 추출
# ----------------------------------------
def transcribe_with_whisperx(audio_path: str):
    global align_model, align_meta
    print("🎙️ WhisperX: transcribing...")
    audio = whisperx.load_audio(audio_path)
    result = asr_model.transcribe(audio)

    # 정렬 모델 캐시
    if align_model is None:
        align_model, align_meta = whisperx.load_align_model(
            language_code=result["language"], device=device
        )

    aligned = whisperx.align(result["segments"], align_model, align_meta, audio, device)

    # ✅ 결과 JSON 저장
    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    json_path = os.path.join(DOWNLOAD_DIR, f"{base_name}_whisperx.json")

    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(aligned, f, indent=2, ensure_ascii=False)
        print(f"💾 WhisperX alignment saved → {json_path}")
    except Exception as e:
        print(f"⚠️ Failed to save WhisperX JSON: {e}")

    return aligned["segments"]  # 각 segment에 words 필드 포함


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

def fetch_genius_by_url(url: str):

    page = requests.get(url, timeout=20)            
    soup = BeautifulSoup(page.text, "html.parser")
    lines = soup.select('div[data-testid="lyrics.lyricLine"]')
    if not lines:
        print("No lyrics found")
        return None
    lyrics = []
    for line in lines:
        text  = line.get_text(strip=True)
        if text:
            lyrics.append(text)
    return "\n".join(lyrics).strip()


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
            results.append({"text": line, "start": None, "end": None, "interpolated": False})
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
                    "score": round(best_score, 3),
                    "interpolated": False
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
                    "score": round(best_score, 3),
                    "interpolated": False
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
                r["interpolated"] = True
                print(f"🔧 Interpolated [Line {idx}] '{r['text']}' → {r['start']}~{r['end']}")
            elif prevs:
                prev_end = prevs[-1][2]
                r["start"], r["end"] = round(prev_end + 1.0, 3), round(prev_end + 3.5, 3)
                r["interpolated"] = True
                print(f"🔧 Interpolated [Line {idx}] (after prev) '{r['text']}' → {r['start']}~{r['end']}")

    return results

# # TODO need to add 보간
# def align_lyrics_lines_v2(lyrics_lines, whisper_words, base_threshold=0.4, drop_margin=0.15, window=15, debug= True):
#     """
#     개선된 가사-Whisper 동기화 알고리즘
#     -------------------------------------------------
#     • 단어 단위 슬라이딩 윈도우 유사도 기반
#     • adaptive threshold: whisper confidence에 따라 자동 보정
#     • 반복 가사 대응 (rollback)
#     • silence-aware interpolation
#     -------------------------------------------------
#     whisper_words: [{"word": str, "start": float, "end": float}, ...]
#     """

#     # ✅ 단어 리스트
#     ww = [w["word"] for w in whisper_words]
#     n = len(ww)
#     results = []

#     # ✅ adaptive threshold 계산 (confidence 평균 기반)
#     avg_conf = np.mean([w.get("score", 0.9) for w in whisper_words]) if whisper_words else 0.9
#     threshold =  0.3 # base_threshold * (0.8 + avg_conf)  # e.g. avg_conf=0.85 → 0.68
#     if debug:
#         print(f"\n🧠 Adaptive threshold = {threshold:.3f} (base={base_threshold}, avg_conf={avg_conf:.2f})")

#     used_idx = 0

#     for line_idx, line in enumerate(lyrics_lines):
#         lyric_words = [normalize_word(x) for x in line.split() if normalize_word(x)]
#         if not lyric_words:
#             results.append({"text": line, "start": None, "end": None, "score": 0.0})
#             continue

#         lyric_join = " ".join(lyric_words)
#         best = {"score": 0, "start": None, "end": None, "start_idx": None, "end_idx": None}

#         # 🔁 탐색 구간: used_idx ~ used_idx + window (rollback 5단어 허용)
#         start_search = max(0, used_idx - 5)
#         end_search = min(n, n)

#         for i in range(start_search, end_search):
#             # 슬라이딩 윈도우로 비교
#             for j in range(i + 3, min(i + window, n)):
#                 segment = " ".join(ww[i:j])
#                 ratio = difflib.SequenceMatcher(None, segment, lyric_join).ratio()

#                 if ratio > best["score"]:
#                     best = {
#                         "score": ratio,
#                         "start": whisper_words[i]["start"],
#                         "end": whisper_words[j - 1]["end"],
#                         "start_idx": i,
#                         "end_idx": j - 1,
#                     }

#         # ✅ 결과 반영
#         if best["score"] >= threshold:
#             results.append({
#                 "text": line,
#                 "start": round(best["start"], 3),
#                 "end": round(best["end"], 3),
#                 "score": round(best["score"], 3)
#             })
#             used_idx = best["end_idx"] + 1
#             if debug:
#                 print(f"✅ [Line {line_idx}] {line[:40]}... ({best['start']:.2f}–{best['end']:.2f}, score={best['score']:.3f})")
#         else:
#             # 일치 안 하면 placeholder + 나중에 보간
#             results.append({"text": line, "start": None, "end": None, "score": best["score"]})
#             if debug:
#                 print(f"⚠️ [Line {line_idx}] No match (max={best['score']:.3f})")

#     # ✅ silence-aware interpolation
#     known = [(i, r["start"], r["end"]) for i, r in enumerate(results) if r["start"] is not None]
#     for idx, r in enumerate(results):
#         if r["start"] is None and known:
#             prevs = [(k, s, e) for k, s, e in known if k < idx]
#             nexts = [(k, s, e) for k, s, e in known if k > idx]

#             if prevs and nexts:
#                 prev_end = prevs[-1][2]
#                 next_start = nexts[0][1]
#                 gap = max(1.0, (next_start - prev_end) / 2)
#                 mid = round(prev_end + gap / 2, 3)
#                 r["start"], r["end"] = mid, round(mid + gap, 3)
#             elif prevs:
#                 prev_end = prevs[-1][2]
#                 r["start"], r["end"] = round(prev_end + 1.0, 3), round(prev_end + 3.5, 3)

#     return results


# ----------------------------------------
# 🌐 Flask 엔드포인트
# ----------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/edit")
def edit_page():
    return render_template("edit.html")

@app.route("/api/alignment", methods=["GET"])
def get_alignment():
    video_id = request.args.get("video_id", "").strip()
    if not video_id:
        return jsonify({"error": "Missing 'video_id'"}), 400

    path = os.path.join(DOWNLOAD_DIR, f"{video_id}_aligned.json")
    if not os.path.exists(path):
        return jsonify({"error": "Alignment not found"}), 404

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return jsonify(data)


@app.route("/api/alignment", methods=["POST"])
def save_alignment():
    payload = request.get_json(silent=True) or {}
    video_id = payload.get("video_id")
    lines = payload.get("lines")
    meta = payload.get("meta", {})

    if not video_id or not isinstance(lines, list):
        return jsonify({"error": "Invalid payload"}), 400

    path = os.path.join(DOWNLOAD_DIR, f"{video_id}_aligned.json")
    data = {
        "video_id": video_id,
        "meta": meta,
        "lines": lines
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return jsonify({"ok": True})



@app.route("/api/audio", methods=["GET"])
def get_audio():
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
    
    if mode == "url" and not genius_url:
        return jsonify({"error": "URL is empty."}), 400

    if mode == "manual" and not manual_lyrics:
        return jsonify({"error": "No Manual lyrics provided."}), 400

    try:
        # 1) 다운로드 & 음성 인식
        audio_path, yt_title, yt_uploader, video_id = download_audio(video_url)
        print(f"✅ Downloaded: {yt_title} ({audio_path})")
        seperated_vocal_path = seperate_audio(audio_path)
        whisper_segments = transcribe_with_whisperx(seperated_vocal_path)
        whisper_words = flatten_whisper_words(whisper_segments)
        if not whisper_words:
            print("⚠️ No word-level timestamps from WhisperX")

        # 2) 가사 소스 결정
        if mode == "manual" and manual_lyrics:
            lines = [line.strip() for line in manual_lyrics.splitlines() if line.strip()]
            genius_lyrics = "\n".join(lines).strip()
        elif mode == "url" and genius_url:
            genius_lyrics = fetch_genius_by_url(genius_url)
        # else:
        #     genius_lyrics = get_lyrics_from_genius(title or yt_title, artist or yt_uploader)

        # 3) 정제 전/후 로깅
        print("\n📝 [Raw Genius Lyrics Preview]")
        if genius_lyrics:
            for i, line in enumerate(genius_lyrics.split("\n")):
                print(f"{i+1:02d}. {line}")
        else:
            print("(No lyrics retrieved from Genius)")

        if not genius_lyrics:
            print("⚠️ No lyrics found — returning Whisper transcription only.")
            return jsonify({
                "youtube_title": yt_title,
                "artist": artist or yt_uploader,
                "lyrics_timed": whisper_segments,  # fallback: WhisperX segment 원본
            })

        lyrics_lines = clean_lyrics_text(genius_lyrics)

        print("\n🧹 [Cleaned Lyrics Preview]")
        for i, line in enumerate(lyrics_lines[:20]):
            print(f"{i+1:02d}. {line}")
        print(f"🧾 Total lines after cleaning: {len(lyrics_lines)}\n")

        aligned = align_lyrics_lines(lyrics_lines, whisper_words)

        save_path = os.path.join(DOWNLOAD_DIR, f"{video_id}_aligned.json")
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump({
                "video_id": video_id,
                "meta": {"title": yt_title, "artist": artist or yt_uploader, "mode": mode},
                "lines": aligned
            }, f, indent=2, ensure_ascii=False)
        print(f"💾 Alignment saved → {save_path}")


        return jsonify({
            "youtube_title": yt_title,
            "artist": artist or yt_uploader,
            "video_id": video_id,
            "lyrics_timed": aligned,  # ✅ 단어별 포함
        })


    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ----------------------------------------
# 🚀 실행
# ----------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000, debug=True)