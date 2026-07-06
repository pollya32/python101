let editMode = false;
let currentPartId = null;
let partDetailModal, replaceModal, historyModal, partEditModal;
let currentParts = [];
let currentEquipmentId = null;
const MASTER_EQUIPMENT_ID = 1;

const statusColor = { ok: "#22c55e", soon: "#f59e0b", overdue: "#ef4444", unknown: "#9ca3af" };
const statusBadge = { ok: "badge-ok", soon: "badge-soon", overdue: "badge-overdue", unknown: "badge-unknown" };
const statusLabel = { ok: "정상", soon: "교체 임박", overdue: "교체 필요", unknown: "미기록" };

const ICON_CHOICES = [
  "🔩", "⚙️", "🔧", "🛠️", "🪛", "🔨", "📦", "🖥️",
  "🖨️", "💻", "📡", "🎛️", "🚨", "💡", "🔌", "⚡",
  "🔋", "🌡️", "💧", "🧪", "🧯", "🧰", "🏭", "⚗️",
  "🌀", "🗜️", "🧲", "📊", "🛞", "🚿", "🔥", "❄️",
];

function renderIconPicker(containerId, inputId, current) {
  const container = document.getElementById(containerId);
  container.innerHTML = ICON_CHOICES.map(
    (ic) => `<button type="button" class="icon-choice ${ic === current ? "selected" : ""}" data-icon="${ic}">${ic}</button>`
  ).join("");
  container.querySelectorAll(".icon-choice").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById(inputId).value = btn.dataset.icon;
      container.querySelectorAll(".icon-choice").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
    });
  });
}

function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("로그인이 필요합니다");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "요청 처리 중 오류가 발생했습니다");
  }
  return res.status === 204 ? null : res.json();
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function linkifyText(text) {
  const escaped = escapeHtml(text);
  return escaped.replace(
    /(https?:\/\/[^\s<]+)/g,
    '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>'
  );
}

let lastNotesContent = "";

async function loadNotes() {
  const data = await fetchJson(`/api/units/${UNIT_ID}/notes`);
  lastNotesContent = data.content || "";
  renderNotesView(lastNotesContent);
  document.getElementById("notesEdit").value = lastNotesContent;
  document.getElementById("notesSavedAt").textContent = data.updated_at
    ? `최종 수정: ${data.updated_at}`
    : "";
}

function renderNotesView(content) {
  const view = document.getElementById("notesView");
  if (!content || !content.trim()) {
    view.innerHTML = "";
    view.classList.add("is-empty");
  } else {
    view.classList.remove("is-empty");
    view.innerHTML = linkifyText(content);
  }
}

function setNotesEditing(editing) {
  document.getElementById("notesView").classList.toggle("d-none", editing);
  document.getElementById("notesEdit").classList.toggle("d-none", !editing);
  document.getElementById("editNotesBtn").classList.toggle("d-none", editing);
  document.getElementById("saveNotesBtn").classList.toggle("d-none", !editing);
  document.getElementById("cancelNotesBtn").classList.toggle("d-none", !editing);
  if (editing) document.getElementById("notesEdit").focus();
}

async function loadUnitHeader() {
  try {
    const unit = await fetchJson(`/api/units/${UNIT_ID}`);
    currentEquipmentId = unit.equipment_id;
    document.getElementById("unitPageTitle").textContent = unit.name;
    document.getElementById("unitIcon").textContent = unit.icon;
    document.getElementById("unitName").textContent = unit.name;
    document.getElementById("unitShape").style.setProperty("--shape-color", unit.color);
    document.getElementById("backToEquipmentBtn").href = `/equipment/${unit.equipment_id}`;
    const isMaster = unit.equipment_id === MASTER_EQUIPMENT_ID;
    document.getElementById("masterHint").classList.toggle("d-none", !isMaster);
    document.getElementById("applyPartsBtn").classList.toggle("d-none", !isMaster);
  } catch (err) {
    alert("유닛 정보를 불러올 수 없습니다.");
    window.location.href = "/";
  }
}

async function loadParts() {
  currentParts = await fetchJson(`/api/units/${UNIT_ID}/parts`);
  renderPartsCanvas(currentParts);
}

function renderPartsCanvas(parts) {
  const canvas = document.getElementById("partsCanvas");
  canvas.innerHTML = parts.map(partShapeHtml).join("");
  parts.forEach((p) => {
    const card = canvas.querySelector(`[data-part-id="${p.id}"]`);
    card.style.left = `${p.pos_x}%`;
    card.style.top = `${p.pos_y}%`;
    card.style.width = `${p.width}px`;
    card.style.height = `${p.height}px`;

    card.querySelector(".edit-unit-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      openPartEditModal(p);
    });
    card.querySelector(".delete-unit-btn")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`"${p.name}" 부품을 삭제할까요? 교체 이력도 함께 삭제됩니다.`)) return;
      await fetchJson(`/api/parts/${p.id}`, { method: "DELETE" });
      loadParts();
    });
    card.querySelector(".copy-part-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      copyPart(p);
    });

    makeDraggable(card, p);
    makeResizable(card, p);
    makeResizableHorizontal(card, p);
  });
}

const MIN_SIZE_W = 90;
const MIN_SIZE_H = 80;
const MAX_SIZE_W = 320;
const MAX_SIZE_H = 260;

function makeResizable(card, part) {
  const handle = card.querySelector(".resize-handle");
  if (!handle) return;

  handle.addEventListener("mousedown", (e) => {
    if (!editMode) return;
    e.preventDefault();
    e.stopPropagation();

    const startX = e.clientX;
    const startY = e.clientY;
    const startWidth = card.offsetWidth;
    const startHeight = card.offsetHeight;

    card.classList.add("dragging");

    function onMove(ev) {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      const w = Math.min(Math.max(startWidth + dx, MIN_SIZE_W), MAX_SIZE_W);
      const h = Math.min(Math.max(startHeight + dy, MIN_SIZE_H), MAX_SIZE_H);
      card.style.width = `${w}px`;
      card.style.height = `${h}px`;
    }

    async function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      card.classList.remove("dragging");
      const width = card.offsetWidth;
      const height = card.offsetHeight;
      part.width = width;
      part.height = height;
      await fetchJson(`/api/parts/${part.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ width, height }),
      });
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

function makeResizableHorizontal(card, part) {
  const handle = card.querySelector(".resize-handle-h");
  if (!handle) return;

  handle.addEventListener("mousedown", (e) => {
    if (!editMode) return;
    e.preventDefault();
    e.stopPropagation();

    const startX = e.clientX;
    const startWidth = card.offsetWidth;

    card.classList.add("dragging");

    function onMove(ev) {
      const dx = ev.clientX - startX;
      const w = Math.min(Math.max(startWidth + dx, MIN_SIZE_W), MAX_SIZE_W);
      card.style.width = `${w}px`;
    }

    async function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      card.classList.remove("dragging");
      const width = card.offsetWidth;
      part.width = width;
      await fetchJson(`/api/parts/${part.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ width }),
      });
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

function makeDraggable(card, part) {
  card.addEventListener("mousedown", (e) => {
    if (!editMode) return;
    if (e.target.closest(".unit-edit-actions")) return;
    e.preventDefault();

    const canvas = document.getElementById("partsCanvas");
    const rect = canvas.getBoundingClientRect();
    const startX = e.clientX;
    const startY = e.clientY;
    const startLeft = (parseFloat(card.style.left) / 100) * rect.width;
    const startTop = (parseFloat(card.style.top) / 100) * rect.height;
    let moved = false;

    card.classList.add("dragging");

    function onMove(ev) {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved = true;
      const px = Math.min(Math.max(startLeft + dx, rect.width * 0.04), rect.width * 0.96);
      const py = Math.min(Math.max(startTop + dy, rect.height * 0.04), rect.height * 0.96);
      card.style.left = `${(px / rect.width) * 100}%`;
      card.style.top = `${(py / rect.height) * 100}%`;
    }

    async function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      card.classList.remove("dragging");
      if (moved) {
        const pos_x = parseFloat(card.style.left);
        const pos_y = parseFloat(card.style.top);
        part.pos_x = pos_x;
        part.pos_y = pos_y;
        await fetchJson(`/api/parts/${part.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pos_x, pos_y }),
        });
      } else {
        openPartDetailModal(part.id);
      }
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });

  card.addEventListener("click", (e) => {
    if (editMode) return;
    if (e.target.closest(".unit-edit-actions")) return;
    openPartDetailModal(part.id);
  });
}

function partShapeHtml(p) {
  const color = statusColor[p.status];
  return `
    <div class="unit-card ${editMode ? "edit-mode" : ""}" data-part-id="${p.id}" style="--uc:${color}">
      <span class="unit-status-dot dot-${p.status}"></span>
      <div class="unit-icon-wrap"><span class="unit-icon">${p.icon}</span></div>
      <div class="unit-name">${escapeHtml(p.name)}</div>
      <div class="unit-part-count">${statusLabel[p.status]}</div>
      <div class="unit-edit-actions">
        <button class="edit-unit-btn" title="편집"><i class="bi bi-pencil"></i></button>
        <button class="copy-part-btn" title="복사"><i class="bi bi-copy"></i></button>
        <button class="delete-unit-btn" title="삭제"><i class="bi bi-trash"></i></button>
      </div>
      <div class="resize-handle" title="크기 조절 (가로+세로)"></div>
      <div class="resize-handle-h" title="가로 크기 조절"></div>
    </div>`;
}

const PART_CLIPBOARD_KEY = "partClipboard";

function copyPart(part) {
  const clipboard = {
    name: part.name,
    spec: part.spec,
    cycle_days: part.cycle_days,
    cycle_unit: part.cycle_unit,
    cost: part.cost,
    note: part.note,
    icon: part.icon,
    width: part.width,
    height: part.height,
  };
  localStorage.setItem(PART_CLIPBOARD_KEY, JSON.stringify(clipboard));
  updatePartPasteButton();
  alert(`"${part.name}" 부품을 복사했습니다.\n"붙여넣기" 버튼으로 동일한 부품을 만들 수 있습니다.`);
}

function getPartClipboard() {
  const raw = localStorage.getItem(PART_CLIPBOARD_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function updatePartPasteButton() {
  const clipboard = getPartClipboard();
  const btn = document.getElementById("pastePartBtn");
  btn.classList.toggle("d-none", !editMode || !clipboard);
  if (clipboard) btn.title = `"${clipboard.name}" 붙여넣기`;
}

async function pastePart() {
  const clipboard = getPartClipboard();
  if (!clipboard) {
    alert("복사된 부품이 없습니다. 먼저 부품의 복사 아이콘을 눌러주세요.");
    return;
  }
  await fetchJson(`/api/units/${UNIT_ID}/parts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: `${clipboard.name} 복사본`,
      spec: clipboard.spec,
      cycle_days: clipboard.cycle_days,
      cycle_unit: clipboard.cycle_unit,
      cost: clipboard.cost,
      note: clipboard.note,
      icon: clipboard.icon,
      width: clipboard.width,
      height: clipboard.height,
    }),
  });
  loadParts();
}

function formatCycleDisplay(cycleDays, cycleUnit) {
  if (cycleUnit === "년") {
    const years = Math.round((cycleDays / 365) * 100) / 100;
    return `${years}년`;
  }
  return `${cycleDays}일`;
}

function cycleDaysToDisplayValue(cycleDays, cycleUnit) {
  if (cycleUnit === "년") {
    return Math.round((cycleDays / 365) * 100) / 100;
  }
  return cycleDays;
}

function formatCost(cost) {
  return `${Number(cost || 0).toLocaleString("ko-KR")}원`;
}

function openPartDetailModal(partId) {
  const p = currentParts.find((x) => x.id === partId);
  if (!p) return;
  currentPartId = partId;
  document.getElementById("partDetailTitle").textContent = p.name;
  const badge = statusBadge[p.status];
  const label = statusLabel[p.status];
  const lastText = p.last_replaced_date ? `최근 교체: ${p.last_replaced_date}` : "교체 이력 없음";
  const dueText = p.next_due
    ? `다음 교체 예정: ${p.next_due} (${p.days_left >= 0 ? p.days_left + "일 남음" : Math.abs(p.days_left) + "일 초과"})`
    : "";
  document.getElementById("partDetailBody").innerHTML = `
    <span class="badge ${badge} mb-2">${label}</span>
    ${p.spec ? `<div class="part-spec mb-1">규격: ${escapeHtml(p.spec)}</div>` : ""}
    <div class="small text-muted">
      교체 주기: ${formatCycleDisplay(p.cycle_days, p.cycle_unit)} &middot; 금액: ${formatCost(p.cost)} &middot; ${lastText}
      ${dueText ? `<br>${dueText}` : ""}
      ${p.note ? `<br>비고: ${escapeHtml(p.note)}` : ""}
    </div>`;
  partDetailModal.show();
}

function openReplaceModal() {
  document.getElementById("replaceDate").value = new Date().toISOString().slice(0, 10);
  document.getElementById("replaceCost").value = 0;
  document.getElementById("replaceNote").value = "";
  replaceModal.show();
}

async function openHistoryModal() {
  const p = currentParts.find((x) => x.id === currentPartId);
  const history = await fetchJson(`/api/parts/${currentPartId}/history`);
  const list = document.getElementById("historyList");
  document.querySelector("#historyModal .modal-title").textContent = `교체 이력 - ${p ? p.name : ""}`;
  if (history.length === 0) {
    list.innerHTML = `<p class="text-muted">교체 이력이 없습니다.</p>`;
  } else {
    list.innerHTML = history
      .map(
        (h) => `
      <div class="history-row d-flex justify-content-between">
        <div><strong>${h.replaced_date}</strong> &middot; ${formatCost(h.cost)} ${h.note ? " - " + escapeHtml(h.note) : ""}</div>
        <button class="btn btn-sm btn-link text-danger p-0 del-history-btn" data-id="${h.id}">삭제</button>
      </div>`
      )
      .join("");
    list.querySelectorAll(".del-history-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await fetchJson(`/api/history/${btn.dataset.id}`, { method: "DELETE" });
        loadParts().then(() => openHistoryModal());
      });
    });
  }
  historyModal.show();
}

function openPartEditModal(part) {
  document.getElementById("partEditTitle").textContent = part ? "부품 편집" : "부품 추가";
  document.getElementById("partEditId").value = part ? part.id : "";
  document.getElementById("partEditName").value = part ? part.name : "";
  document.getElementById("partEditSpec").value = part ? part.spec || "" : "";
  const icon = part ? part.icon : "🔩";
  document.getElementById("partEditIcon").value = icon;
  const cycleUnit = part ? part.cycle_unit || "일" : "일";
  document.getElementById("partEditCycleUnit").value = cycleUnit;
  document.getElementById("partEditCycle").value = part ? cycleDaysToDisplayValue(part.cycle_days, cycleUnit) : 90;
  document.getElementById("partEditCost").value = part ? part.cost || 0 : 0;
  document.getElementById("partEditNote").value = part ? part.note || "" : "";
  document.getElementById("partEditLastDate").value = "";
  document.getElementById("partEditLastDateWrap").classList.toggle("d-none", !!part);
  renderIconPicker("partIconPicker", "partEditIcon", icon);
  partEditModal.show();
}

document.addEventListener("DOMContentLoaded", () => {
  partDetailModal = new bootstrap.Modal(document.getElementById("partDetailModal"));
  replaceModal = new bootstrap.Modal(document.getElementById("replaceModal"));
  historyModal = new bootstrap.Modal(document.getElementById("historyModal"));
  partEditModal = new bootstrap.Modal(document.getElementById("partEditModal"));

  tick();
  setInterval(tick, 1000);
  loadUnitHeader();
  loadParts();
  loadNotes();

  document.getElementById("editModeBtn").addEventListener("click", (e) => {
    editMode = !editMode;
    e.currentTarget.classList.toggle("btn-outline-light", !editMode);
    e.currentTarget.classList.toggle("btn-warning", editMode);
    document.getElementById("addPartBtn").classList.toggle("d-none", !editMode);
    updatePartPasteButton();
    renderPartsCanvas(currentParts);
  });

  document.getElementById("addPartBtn").addEventListener("click", () => openPartEditModal(null));
  document.getElementById("pastePartBtn").addEventListener("click", pastePart);

  document.getElementById("editNotesBtn").addEventListener("click", () => setNotesEditing(true));
  document.getElementById("cancelNotesBtn").addEventListener("click", () => {
    document.getElementById("notesEdit").value = lastNotesContent;
    setNotesEditing(false);
  });
  document.getElementById("saveNotesBtn").addEventListener("click", async () => {
    const content = document.getElementById("notesEdit").value;
    try {
      const data = await fetchJson(`/api/units/${UNIT_ID}/notes`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      lastNotesContent = data.content || "";
      renderNotesView(lastNotesContent);
      document.getElementById("notesSavedAt").textContent = data.updated_at
        ? `최종 수정: ${data.updated_at}`
        : "";
      setNotesEditing(false);
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("partDetailReplaceBtn").addEventListener("click", () => {
    partDetailModal.hide();
    openReplaceModal();
  });
  document.getElementById("partDetailHistoryBtn").addEventListener("click", () => {
    partDetailModal.hide();
    openHistoryModal();
  });

  document.getElementById("replaceForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      replaced_date: document.getElementById("replaceDate").value,
      cost: parseFloat(document.getElementById("replaceCost").value) || 0,
      note: document.getElementById("replaceNote").value.trim(),
    };
    try {
      await fetchJson(`/api/parts/${currentPartId}/replace`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      replaceModal.hide();
      loadParts();
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("partEditForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = document.getElementById("partEditId").value;
    const cycleUnit = document.getElementById("partEditCycleUnit").value;
    const cycleValue = parseFloat(document.getElementById("partEditCycle").value);
    const cycleDays = cycleUnit === "년" ? Math.round(cycleValue * 365) : Math.round(cycleValue);
    const payload = {
      name: document.getElementById("partEditName").value.trim(),
      spec: document.getElementById("partEditSpec").value.trim(),
      icon: document.getElementById("partEditIcon").value.trim(),
      cycle_days: cycleDays,
      cycle_unit: cycleUnit,
      cost: parseFloat(document.getElementById("partEditCost").value) || 0,
      note: document.getElementById("partEditNote").value.trim(),
    };
    try {
      if (id) {
        await fetchJson(`/api/parts/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } else {
        payload.last_replaced_date = document.getElementById("partEditLastDate").value || null;
        await fetchJson(`/api/units/${UNIT_ID}/parts`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      partEditModal.hide();
      loadParts();
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("applyPartsBtn").addEventListener("click", async () => {
    const ok = confirm(
      "현재 이 유닛의 부품 구성을 동일한 이름의 유닛을 가진 나머지 설비 전체에 적용합니다.\n" +
      "- 이름이 같은 부품은 규격/교체주기/비고/아이콘/위치/크기가 이 구성대로 갱신됩니다.\n" +
      "- 여기 없는 이름의 부품은 각 설비에서 삭제되며, 등록된 교체 이력도 함께 삭제됩니다.\n\n" +
      "계속하시겠습니까?"
    );
    if (!ok) return;
    try {
      const result = await fetchJson(`/api/units/${UNIT_ID}/apply-parts`, { method: "POST" });
      alert(`설비 ${result.equipment_count}대에 부품 구성(${result.part_count}개)을 적용했습니다.`);
    } catch (err) {
      alert(err.message);
    }
  });
});
