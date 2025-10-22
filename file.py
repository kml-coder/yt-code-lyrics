import subprocess, os

AUDIO_PATH = "downloads/VoEsEC2CLgE.wav"
OUTPUT_DIR = "separated"

# Demucs 실행 (보컬만 추출)
subprocess.run([
    "demucs", "--two-stems=vocals", AUDIO_PATH, "-o", OUTPUT_DIR
])

VOCAL_PATH = os.path.join(OUTPUT_DIR, "htdemucs", "song", "vocals.wav")
