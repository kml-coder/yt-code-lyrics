import demucs.separate
import shlex
import os

def seperate_audio(audio_path: str):

    # 출력 폴더 지정 (자동 생성)
    output_dir = "separated_tracks"
    os.makedirs(output_dir, exist_ok=True)

    # CLI 명령 구성
    cmd = f'-n htdemucs --two-stems vocals -o "{output_dir}" "{audio_path}"'

    # 실행 (shlex.split으로 CLI 문자열을 파싱)
    demucs.separate.main(shlex.split(cmd))

    print(f"✅ 분리 완료! 결과 파일은 '{output_dir}' 안에 저장됩니다.")

if __name__ == "__main__":
    seperate_audio("downloads/VoEsEC2CLgE.wav")