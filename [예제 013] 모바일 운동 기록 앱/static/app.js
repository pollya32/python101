/* ===== 상태 관리 ===== */
const state = {
  exercises: [],
  todaySets: [],
  selectedExercise: null,
  currentCategory: "전체",
  searchQuery: "",
  todayDate: new Date().toISOString().split("T")[0],
};

/* ===== 유틸 ===== */
function fmt(date) {
  const d = new Date(date + "T00:00:00");
  const days = ["일", "월", "화", "수", "목", "금", "토"];
  return `${d.getFullYear()}년 ${d.getMonth() + 1}월 ${d.getDate()}일 (${days[d.getDay()]})`;
}

function showToast(msg, isError = false) {
  document.querySelectorAll(".toast").forEach((t) => t.remove());
  const t = document.createElement("div");
  t.className = "toast" + (isError ? " error" : "");
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2100);
}

async function api(method, path, body) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || "서버 오류");
  }
  return res.json();
}

/* ===== 초기화 ===== */
async function init() {
  document.getElementById("headerDate").textContent = fmt(state.todayDate);
  await loadExercises();
  await loadTodaySets();
  setupTabs();
  setupModal();
  setupHistoryModal();
}

/* ===== 운동 목록 로드 ===== */
async function loadExercises() {
  state.exercises = await api("GET", "/api/exercises");
  renderCategories();
  renderExerciseList();
}

function getCategories() {
  const cats = [...new Set(state.exercises.map((e) => e.category))].sort();
  return ["전체", ...cats];
}

function renderCategories() {
  const container = document.getElementById("categoryTabs");
  container.innerHTML = getCategories()
    .map(
      (cat) =>
        `<button class="cat-btn ${cat === state.currentCategory ? "active" : ""}"
                 data-cat="${cat}">${cat}</button>`
    )
    .join("");
  container.querySelectorAll(".cat-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.currentCategory = btn.dataset.cat;
      renderCategories();
      renderExerciseList();
    });
  });
}

function renderExerciseList() {
  const q = state.searchQuery.toLowerCase();
  const filtered = state.exercises.filter((e) => {
    const matchCat =
      state.currentCategory === "전체" || e.category === state.currentCategory;
    const matchQ = !q || e.name.toLowerCase().includes(q);
    return matchCat && matchQ;
  });

  const container = document.getElementById("exerciseList");
  container.innerHTML =
    filtered
      .map(
        (e) =>
          `<div class="exercise-item ${state.selectedExercise?.id === e.id ? "selected" : ""}"
                data-id="${e.id}">
            <span class="exercise-item-name">${e.name}</span>
            <span class="exercise-item-cat">${e.category}</span>
          </div>`
      )
      .join("") +
    `<button class="add-exercise-btn" id="openAddExercise">+ 운동 직접 추가</button>`;

  container.querySelectorAll(".exercise-item").forEach((el) => {
    el.addEventListener("click", () => selectExercise(Number(el.dataset.id)));
  });
  document
    .getElementById("openAddExercise")
    .addEventListener("click", () =>
      document.getElementById("addExerciseModal").classList.remove("hidden")
    );
}

/* ===== 운동 선택 ===== */
async function selectExercise(id) {
  state.selectedExercise = state.exercises.find((e) => e.id === id);
  renderExerciseList();
  document
    .getElementById("activeExerciseSection")
    .classList.remove("hidden");
  document.getElementById("activeExerciseName").textContent =
    state.selectedExercise.name;

  // PR 확인
  try {
    const pr = await api("GET", `/api/personal_records/${id}`);
    const badge = document.getElementById("prBadge");
    if (pr.max_weight) {
      badge.textContent = `🏆 최고 ${pr.max_weight}kg`;
      badge.classList.remove("hidden");
    } else {
      badge.classList.add("hidden");
    }
  } catch {}

  renderTodaySets();

  // 스크롤
  document
    .getElementById("activeExerciseSection")
    .scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ===== 오늘 세트 로드 / 렌더 ===== */
async function loadTodaySets() {
  state.todaySets = await api("GET", `/api/sets/${state.todayDate}`);
  renderTodaySummary();
}

function renderTodaySets() {
  if (!state.selectedExercise) return;
  const sets = state.todaySets.filter(
    (s) => s.exercise_id === state.selectedExercise.id
  );
  const container = document.getElementById("todaySetsList");

  if (!sets.length) {
    container.innerHTML =
      '<p style="color:var(--text2);font-size:13px;text-align:center;padding:10px">아직 세트가 없어요</p>';
    return;
  }

  container.innerHTML = sets
    .map((s) => {
      const detail =
        s.weight != null && s.reps != null
          ? `${s.weight}kg × ${s.reps}회`
          : s.reps != null
          ? `${s.reps}회`
          : s.duration_sec != null
          ? `${s.duration_sec}초`
          : "-";
      return `<div class="set-card">
        <div class="set-num">${s.set_number}</div>
        <div class="set-info">
          <div class="set-main">${detail}</div>
          ${s.note ? `<div class="set-note">${s.note}</div>` : ""}
        </div>
        <button class="set-delete" data-id="${s.id}">✕</button>
      </div>`;
    })
    .join("");

  container.querySelectorAll(".set-delete").forEach((btn) => {
    btn.addEventListener("click", () => deleteSet(Number(btn.dataset.id)));
  });
}

function renderTodaySummary() {
  const container = document.getElementById("todaySummary");
  const empty = document.getElementById("emptyState");

  if (!state.todaySets.length) {
    container.innerHTML = "";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");

  // 운동별 그룹
  const groups = {};
  state.todaySets.forEach((s) => {
    if (!groups[s.exercise_name]) groups[s.exercise_name] = [];
    groups[s.exercise_name].push(s);
  });

  container.innerHTML = Object.entries(groups)
    .map(
      ([name, sets]) => `
      <div class="summary-exercise-card">
        <div class="summary-exercise-name">${name}</div>
        <div class="summary-sets">
          ${sets
            .map((s) => {
              const detail =
                s.weight != null && s.reps != null
                  ? `<span class="chip-num">${s.weight}kg×${s.reps}</span>`
                  : s.reps != null
                  ? `<span class="chip-num">${s.reps}회</span>`
                  : `<span class="chip-num">${s.duration_sec}초</span>`;
              return `<span class="summary-set-chip">세트${s.set_number} ${detail}</span>`;
            })
            .join("")}
        </div>
      </div>`
    )
    .join("");
}

/* ===== 세트 추가 ===== */
document.getElementById("addSetBtn").addEventListener("click", async () => {
  if (!state.selectedExercise) return;
  const weight = document.getElementById("inputWeight").value;
  const reps = document.getElementById("inputReps").value;
  const note = document.getElementById("inputNote").value.trim();

  if (!weight && !reps) {
    showToast("무게 또는 횟수를 입력하세요", true);
    return;
  }

  try {
    const newSet = await api("POST", "/api/sets", {
      exercise_id: state.selectedExercise.id,
      workout_date: state.todayDate,
      weight: weight ? parseFloat(weight) : null,
      reps: reps ? parseInt(reps) : null,
      note,
    });
    state.todaySets.push(newSet);
    document.getElementById("inputWeight").value = "";
    document.getElementById("inputReps").value = "";
    document.getElementById("inputNote").value = "";
    renderTodaySets();
    renderTodaySummary();
    showToast(`세트 ${newSet.set_number} 기록 완료!`);
  } catch (e) {
    showToast(e.message, true);
  }
});

/* ===== 세트 삭제 ===== */
async function deleteSet(id) {
  try {
    await api("DELETE", `/api/sets/${id}`);
    state.todaySets = state.todaySets.filter((s) => s.id !== id);
    renderTodaySets();
    renderTodaySummary();
    showToast("세트 삭제됨");
  } catch (e) {
    showToast(e.message, true);
  }
}

/* ===== 검색 ===== */
document.getElementById("searchExercise").addEventListener("input", (e) => {
  state.searchQuery = e.target.value;
  renderExerciseList();
});

/* ===== 탭 전환 ===== */
function setupTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      document
        .querySelectorAll(".tab-btn")
        .forEach((b) => b.classList.remove("active"));
      document
        .querySelectorAll(".tab-content")
        .forEach((c) => c.classList.remove("active"));
      btn.classList.add("active");
      const tabId = "tab-" + btn.dataset.tab;
      document.getElementById(tabId).classList.add("active");
      if (btn.dataset.tab === "history") await loadHistory();
    });
  });
}

/* ===== 히스토리 탭 ===== */
async function loadHistory() {
  const data = await api("GET", "/api/history");
  const container = document.getElementById("historyList");
  const empty = document.getElementById("historyEmpty");

  if (!data.length) {
    container.innerHTML = "";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");

  container.innerHTML = data
    .map(
      (d) => `
    <div class="history-card" data-date="${d.workout_date}">
      <div class="history-card-header">
        <span class="history-date">${fmt(d.workout_date)}</span>
        <div class="history-stats">
          <span class="history-stat">🏋️ ${d.exercise_count}종목</span>
          <span class="history-stat">📊 ${d.total_sets}세트</span>
        </div>
      </div>
      <div class="history-exercises">${d.exercises}</div>
    </div>`
    )
    .join("");

  container.querySelectorAll(".history-card").forEach((card) => {
    card.addEventListener("click", () =>
      showHistoryDetail(card.dataset.date)
    );
  });
}

/* ===== 히스토리 상세 모달 ===== */
function setupHistoryModal() {
  document
    .getElementById("historyModalBackdrop")
    .addEventListener("click", closeHistoryModal);
  document
    .getElementById("closeHistoryDetail")
    .addEventListener("click", closeHistoryModal);
}

async function showHistoryDetail(date) {
  const sets = await api("GET", `/api/sets/${date}`);
  document.getElementById("historyDetailDate").textContent = fmt(date);

  const groups = {};
  sets.forEach((s) => {
    if (!groups[s.exercise_name]) groups[s.exercise_name] = [];
    groups[s.exercise_name].push(s);
  });

  document.getElementById("historyDetailContent").innerHTML = Object.entries(
    groups
  )
    .map(
      ([name, sArr]) => `
    <div class="detail-exercise-card">
      <div class="detail-exercise-name">${name}</div>
      ${sArr
        .map((s) => {
          const v =
            s.weight != null && s.reps != null
              ? `${s.weight}kg × ${s.reps}회`
              : s.reps != null
              ? `${s.reps}회`
              : `${s.duration_sec}초`;
          return `<div class="detail-set-row">
            <span class="detail-set-label">세트 ${s.set_number}</span>
            <span class="detail-set-value">${v}${s.note ? ` · ${s.note}` : ""}</span>
          </div>`;
        })
        .join("")}
    </div>`
    )
    .join("");

  document.getElementById("historyDetailModal").classList.remove("hidden");
}

function closeHistoryModal() {
  document.getElementById("historyDetailModal").classList.add("hidden");
}

/* ===== 운동 추가 모달 ===== */
function setupModal() {
  document
    .getElementById("modalBackdrop")
    .addEventListener("click", closeAddModal);
  document
    .getElementById("cancelAddExercise")
    .addEventListener("click", closeAddModal);
  document
    .getElementById("confirmAddExercise")
    .addEventListener("click", confirmAddExercise);
}

function closeAddModal() {
  document.getElementById("addExerciseModal").classList.add("hidden");
  document.getElementById("newExerciseName").value = "";
}

async function confirmAddExercise() {
  const name = document.getElementById("newExerciseName").value.trim();
  const category = document.getElementById("newExerciseCategory").value;
  if (!name) {
    showToast("운동 이름을 입력하세요", true);
    return;
  }
  try {
    const ex = await api("POST", "/api/exercises", { name, category });
    state.exercises.push(ex);
    closeAddModal();
    state.currentCategory = category;
    renderCategories();
    renderExerciseList();
    selectExercise(ex.id);
    showToast(`'${name}' 추가됨!`);
  } catch (e) {
    showToast(e.message, true);
  }
}

/* ===== 실행 ===== */
init();
