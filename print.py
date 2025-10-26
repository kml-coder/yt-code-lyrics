import time
import json
import threading
from pydub import AudioSegment
from pydub.playback import play

# ✅ 파일 경로 설정
AUDIO_PATH = "downloads/CgjknaWVChY.wav"
SEGMENT_PATH = "lyrics_timed(20).json"

# ✅ JSON 파일로부터 segments 불러오기
with open(SEGMENT_PATH, "r", encoding="utf-8") as f:
    segments = json.load(f)

def type_text(text, delay):
    """문자를 한 글자씩 출력"""
    for ch in text:
        print(ch, end='', flush=True)
        time.sleep(delay)
    print()

def lyric_player():
    """가사 출력 (JSON 기반)"""
    start_time = time.time()
    for seg in segments:
        # 오디오 타이밍 맞추기
        wait_time = seg["start"] - (time.time() - start_time)
        if wait_time > 0:
            time.sleep(wait_time)

        # 구간 길이에 따라 출력 속도 조절
        duration = seg["end"] - seg["start"]
        delay = max(0.01, min(0.1, duration / max(len(seg["text"]), 1)))

        type_text(f"{seg['text']}", delay)

def play_audio():
    """오디오 재생"""
    sound = AudioSegment.from_file(AUDIO_PATH)
    play(sound)

if __name__ == "__main__":
    # 🎵 오디오와 가사 동시 실행
    audio_thread = threading.Thread(target=play_audio)
    lyric_thread = threading.Thread(target=lyric_player)

    audio_thread.start()
    lyric_thread.start()

    audio_thread.join()
    lyric_thread.join()
