const $input = document.getElementById("yt-input");
const $btnGen = document.getElementById("btn-generate");
const $btnPlay = document.getElementById("btn-play");
const $btnPause = document.getElementById("btn-pause");
const $btnDownload = document.getElementById("btn-download");
const $status = document.getElementById("status");
const $code = document.getElementById("code");
const $audio = document.getElementById("audio-player");

let segments = [];
let displayedIndex = -1;
let typing = false;
let syncTimer = null;

// ---------------------------------------------
// ✅ 가사 입력 모드 감지
// ---------------------------------------------
function getLyricsSource() {
  const mode = document.querySelector('input[name="lyrics-mode"]:checked').value;
  if (mode === "url") {
    return { mode, genius_url: document.getElementById("genius-url").value.trim() };
  } else if (mode === "manual") {
    return { mode, manual_lyrics: document.getElementById("manual-lyrics").value.trim() };
  }
  return { mode };
}

// ---------------------------------------------
// 🎨 입력창 표시 토글 (fade 전환 포함)
// ---------------------------------------------
document.querySelectorAll('input[name="lyrics-mode"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    const mode = radio.value;

    const urlBox = document.getElementById("genius-url");
    const manualBox = document.getElementById("manual-lyrics");

    // 모두 숨기고 선택된 것만 표시
    urlBox.style.display = "none";
    manualBox.style.display = "none";

    if (mode === "url") {
      urlBox.style.display = "block";
      urlBox.focus();
    } else if (mode === "manual") {
      manualBox.style.display = "block";
      manualBox.focus();
    }
  });
});

// ---------------------------------------------
// 🧩 상태 표시
// ---------------------------------------------
function setStatus(msg) {
  $status.textContent = msg;
}

// ---------------------------------------------
// 🎧 Fetch YouTube 오디오 + WhisperX/Genius 결과
// ---------------------------------------------
async function fetchLyricsAndAudio(videoUrl) {
  // 1️⃣ 오디오 스트림 정보 가져오기
  const audioRes = await fetch(`/api/audio?video=${encodeURIComponent(videoUrl)}`);
  const audioData = await audioRes.json();
  if (!audioRes.ok) throw new Error(audioData.error || "Failed to fetch audio");

  // 2️⃣ WhisperX + Genius 정렬 요청
  const { mode, genius_url, manual_lyrics } = getLyricsSource();

  const lyricsRes = await fetch("/api/lyrics_timed", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      video_url: videoUrl,
      title: audioData.title,
      artist: "unknown",
      mode,
      genius_url,
      manual_lyrics,
    }),
  });

  const lyricsData = await lyricsRes.json();
  if (!lyricsRes.ok) throw new Error(lyricsData.error || "Failed to fetch lyrics");

  return { audioData, lyricsData };
}

// ---------------------------------------------
// 💻 터미널 UI 관련 함수
// ---------------------------------------------
function clearTerminal() {
  $code.textContent = "";
  displayedIndex = -1;
}

function appendLine(text) {
  $code.textContent += text;
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function typeText(text, delay = 25) {
  typing = true;
  for (let ch of text) {
    appendLine(ch);
    await sleep(delay);
  }
  appendLine("\n");
  typing = false;
}

// ---------------------------------------------
// 🕒 오디오 싱크 타이핑
// ---------------------------------------------
function scheduleSync() {
  if (syncTimer) clearInterval(syncTimer);
  syncTimer = setInterval(async () => {
    if (!$audio || !$audio.currentTime) return;
    const t = $audio.currentTime;
    const nextIndex = displayedIndex + 1;
    if (typing || nextIndex >= segments.length) return;

    const seg = segments[nextIndex];
    if (t >= seg.start) {
      await typeText(`[${seg.start.toFixed(2)}s] ${seg.text}`);
      displayedIndex++;
    }
  }, 100);
}

// ---------------------------------------------
// 🧠 Generate 버튼
// ---------------------------------------------
$btnGen.addEventListener("click", async () => {
  try {
    setStatus("⏳ Fetching lyrics & audio...");
    clearTerminal();
    $btnPlay.disabled = true;
    $btnPause.disabled = true;
    $btnDownload.disabled = true;

    const input = $input.value.trim();
    if (!input) return setStatus("Please paste a YouTube link.");

    const { audioData, lyricsData } = await fetchLyricsAndAudio(input);

    segments = lyricsData.lyrics_timed;
    if (!segments.length) return setStatus("No lyrics found or transcription failed.");

    appendLine(`## Loaded: ${lyricsData.youtube_title}\n`);
    $audio.src = audioData.audio_url;
    $btnPlay.disabled = false;
    $btnPause.disabled = false;
    $btnDownload.disabled = false;
    setStatus("✅ Ready to play!");
  } catch (e) {
    console.error(e);
    setStatus("❌ Error: " + e.message);
  }
});

// ---------------------------------------------
// ▶ 재생 / ⏸️ 일시정지
// ---------------------------------------------
$btnPlay.addEventListener("click", () => {
  if (!$audio.src) return setStatus("No audio loaded.");
  $audio.play();
  scheduleSync();
  setStatus("▶ Playing...");
});

$btnPause.addEventListener("click", () => {
  $audio.pause();
  setStatus("⏸️ Paused.");
});

// ---------------------------------------------
// 💾 JSON 다운로드
// ---------------------------------------------
$btnDownload.addEventListener("click", () => {
  if (!segments.length) return setStatus("No lyrics to download.");

  const blob = new Blob([JSON.stringify(segments, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "lyrics_timed.json";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);

  setStatus("📄 Lyrics downloaded!");
});
