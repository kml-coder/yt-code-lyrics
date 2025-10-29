(function () {
  const urlParams = new URLSearchParams(window.location.search);
  const video_id = urlParams.get("video_id");
  const audioEl = document.getElementById("editor-audio");
  const linesWrap = document.getElementById("lines");
  const saveBtn = document.getElementById("btn-save");
  const resetBtn = document.getElementById("btn-reset");
  const saveStatus = document.getElementById("save-status");

  const metaTitle = document.getElementById("meta-title");
  const metaArtist = document.getElementById("meta-artist");
  const metaVideo = document.getElementById("meta-video");

  if (!video_id) {
    alert("Missing video_id");
    window.location.href = "/";
    return;
  }

  // 1) 오디오 url 가져와서 플레이어에 연결
  fetch(`/api/audio?video=${encodeURIComponent(video_id)}`)
    .then(r => r.json())
    .then(data => {
      if (data.error) throw new Error(data.error);
      audioEl.src = data.audio_url;
    })
    .catch(e => {
      console.error(e);
      saveStatus.textContent = "⚠️ Failed to load audio";
    });

  // 2) 정렬 결과 로드
  let state = { video_id, meta: {}, lines: [] };

  fetch(`/api/alignment?video_id=${encodeURIComponent(video_id)}`)
    .then(r => r.json())
    .then(data => {
      if (data.error) throw new Error(data.error);
      state.video_id = data.video_id || video_id;
      state.meta = data.meta || {};
      state.lines = data.lines || [];

      metaTitle.textContent = `Title: ${state.meta.title || "-"}`;
      metaArtist.textContent = `Artist: ${state.meta.artist || "-"}`;
      metaVideo.textContent = `Video ID: ${state.video_id}`;

      renderLines();
    })
    .catch(e => {
      console.error(e);
      saveStatus.textContent = "⚠️ Failed to load alignment";
    });

  function renderLines() {
    linesWrap.innerHTML = "";
    state.lines.forEach((ln, idx) => {
      const row = document.createElement("div");
      row.className = "line-row";

      // index
      const idxDiv = document.createElement("div");
      idxDiv.textContent = String(idx + 1).padStart(2, "0");
      row.appendChild(idxDiv);

      // text input + 보간 태그
      const textCell = document.createElement("div");
      const textInput = document.createElement("input");
      textInput.type = "text";
      textInput.value = ln.text || "";
      textInput.oninput = (e) => { ln.text = e.target.value; };
      textCell.appendChild(textInput);

      if (ln.interpolated) {
        const tag = document.createElement("span");
        tag.className = "tag warn";
        tag.title = "Interpolated — please review";
        tag.style.marginLeft = "8px";
        tag.textContent = "🔧 interpolated";
        textCell.appendChild(tag);
      }
      row.appendChild(textCell);

      // start
      const startCell = document.createElement("div");
      const startInput = document.createElement("input");
      startInput.type = "number"; startInput.step = "0.001";
      startInput.value = ln.start != null ? ln.start : "";
      startInput.oninput = (e) => { ln.start = parseFloat(e.target.value); };
      startCell.appendChild(startInput);
      row.appendChild(startCell);

      // end
      const endCell = document.createElement("div");
      const endInput = document.createElement("input");
      endInput.type = "number"; endInput.step = "0.001";
      endInput.value = ln.end != null ? ln.end : "";
      endInput.oninput = (e) => { ln.end = parseFloat(e.target.value); };
      endCell.appendChild(endInput);
      row.appendChild(endCell);

      // action
      const actionCell = document.createElement("div");
      const playBtn = document.createElement("button");
      playBtn.className = "btn";
      playBtn.textContent = "▶ play";
      playBtn.onclick = () => {
        if (ln.start != null && !Number.isNaN(ln.start)) {
          audioEl.currentTime = Math.max(0, ln.start - 0.05); // 살짝 앞에서 시작
          audioEl.play();
        }
      };
      actionCell.appendChild(playBtn);
      row.appendChild(actionCell);

      linesWrap.appendChild(row);
    });
  }

  // 3) 저장 (POST /api/alignment)
  saveBtn.onclick = () => {
    saveBtn.disabled = true;
    saveStatus.textContent = "Saving...";
    fetch("/api/alignment", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        video_id: state.video_id,
        meta: state.meta,
        lines: state.lines
      })
    })
      .then(r => r.json())
      .then(resp => {
        if (!resp.ok) throw new Error("Save failed");
        saveStatus.textContent = "✅ Saved";
        localStorage.setItem("edited_alignment", JSON.stringify({
          video_id: state.video_id,
          meta: state.meta,
          lines: state.lines
        }));
        localStorage.setItem("return_from_edit", "true");

        // ✅ index 페이지로 이동
        window.location.href = "/";
      })
      .catch(e => {
        console.error(e);
        saveStatus.textContent = "⚠️ Save failed";
      })
      .finally(() => {
        saveBtn.disabled = false;
        setTimeout(() => (saveStatus.textContent = ""), 1500);
      });
  };
})();

// ---------------------------------------------
// ♻️ Reset 버튼 — 서버에서 원본 다시 불러오기
// ---------------------------------------------
resetBtn.onclick = async () => {
  if (!confirm("Reset to original alignment? All unsaved changes will be lost.")) return;

  saveStatus.textContent = "🔄 Restoring original...";
  resetBtn.disabled = true;
  try {
    const res = await fetch(`/api/alignment?video_id=${encodeURIComponent(state.video_id)}`);
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || "Failed to reload");

    state.lines = data.lines;
    renderLines(); // ✅ 화면 다시 그림
    saveStatus.textContent = "✅ Restored to original JSON";
  } catch (err) {
    console.error(err);
    saveStatus.textContent = "⚠️ Reset failed";
  } finally {
    resetBtn.disabled = false;
    setTimeout(() => (saveStatus.textContent = ""), 2000);
  }
};
