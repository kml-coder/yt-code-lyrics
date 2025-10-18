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

function setStatus(msg) {
  $status.textContent = msg;
}

async function fetchTranscript(videoOrUrl) {
  const res = await fetch(`/api/lyrics_structure?video=${encodeURIComponent(videoOrUrl)}`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Failed to fetch lyrics/structure");
  return { segments: data.lyrics_with_structure };
}


async function fetchAudio(videoOrUrl) {
  const res = await fetch(`/api/audio?video=${encodeURIComponent(videoOrUrl)}`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Failed to fetch audio");
  return data;
}

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

$btnGen.addEventListener("click", async () => {
  try {
    setStatus("Fetching transcript + audio...");
    clearTerminal();
    $btnPlay.disabled = true;
    $btnPause.disabled = true;
    $btnDownload.disabled = true;

    const input = $input.value.trim();
    if (!input) return setStatus("Please paste a YouTube link or video ID.");

    const [tData, aData] = await Promise.all([
      fetchTranscript(input),
      fetchAudio(input),
    ]);

    segments = tData.segments;
    if (!segments.length) return setStatus("No transcript found.");

    appendLine(`## Loaded: ${aData.title}\n`);
    $audio.src = aData.audio_url;
    $btnPlay.disabled = false;
    $btnPause.disabled = false;
    $btnDownload.disabled = false;
    setStatus("Ready to play!");
  } catch (e) {
    console.error(e);
    setStatus("Error: " + e.message);
  }
});

$btnPlay.addEventListener("click", () => {
  if (!$audio.src) return setStatus("No audio loaded.");
  $audio.play();
  scheduleSync();
  setStatus("▶ Playing...");
});

$btnPause.addEventListener("click", () => {
  $audio.pause();
  setStatus("Paused.");
});

$btnDownload.addEventListener("click", () => {
  if (!segments.length) return setStatus("No transcript to download.");

  const blob = new Blob([JSON.stringify(segments, null, 2)], {
    type: "application/json",
  });

  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "transcript.json";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);

  setStatus("📄 Transcript downloaded!");
});
