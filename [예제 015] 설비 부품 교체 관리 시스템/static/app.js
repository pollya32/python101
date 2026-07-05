let editMode = false;
let currentUnitId = null;
let currentPartId = null;
let unitModal, replaceModal, historyModal, unitEditModal;

const statusBadge = { ok: "badge-ok", soon: "badge-soon", overdue: "badge-overdue", unknown: "badge-unknown" };
const statusLabel = { ok: "정상", soon: "교체 임박", overdue: "교체 필요", unknown: "미기록" };

function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "요청 처리 중 오류가 발생했습니다");
  }
  return res.status === 204 ? null : res.json();
}

async function loadUnits() {
  const units = await fetchJson("/api/units");
  renderCanvas(units);
}

function renderCanvas(units) {
  const canvas = document.getElementById("canvas");
  canvas.innerHTML = units.map(unitCardHtml).join("");
  units.forEach((u) => {
    const card = canvas.querySelector(`[data-unit-id="${u.id}"]`);
    card.style.left = `${u.pos_x}%`;
    card.style.top = `${u.pos_y}%`;

    card.querySelector(".edit-unit-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      openUnitEditModal(u);
    });
    card.querySelector(".delete-unit-btn")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`"${u.name}" 유닛을 삭제할까요? 등록된 부품/이력도 함께 삭제됩니다.`)) return;
      await fetchJson(`/api/units/${u.id}`, { method: "DELETE" });
      loadUnits();
    });

    makeDraggable(card, u);
  });
}

function makeDraggable(card, unit) {
  card.addEventListener("mousedown", (e) => {
    if (!editMode) return;
    if (e.target.closest(".unit-edit-actions")) return;
    e.preventDefault();

    const canvas = document.getElementById("canvas");
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
        await fetchJson(`/api/units/${unit.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pos_x, pos_y }),
        });
      } else {
        openUnitModal(unit.id, unit.name);
      }
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });

  card.addEventListener("click", (e) => {
    if (editMode) return;
    if (e.target.closest(".unit-edit-actions")) return;
    openUnitModal(unit.id, unit.name);
  });
}

function unitCardHtml(u) {
  return `
    <div class="unit-card ${editMode ? "edit-mode" : ""}" data-unit-id="${u.id}" style="--uc:${u.color}">
      <span class="unit-status-dot dot-${u.overall_status}"></span>
      <span class="unit-icon">${u.icon}</span>
      <div class="unit-name">${escapeHtml(u.name)}</div>
      <div class="unit-part-count">${u.part_count}개 부품 등록</div>
      <div class="unit-edit-actions">
        <button class="edit-unit-btn" title="편집"><i class="bi bi-pencil"></i></button>
        <button class="delete-unit-btn" title="삭제"><i class="bi bi-trash"></i></button>
      </div>
    </div>`;
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

async function openUnitModal(unitId, unitName) {
  currentUnitId = unitId;
  document.getElementById("unitModalTitle").textContent = unitName;
  document.getElementById("addPartForm").classList.add("d-none");
  document.getElementById("addPartForm").reset();
  await loadParts(unitId);
  unitModal.show();
}

async function loadParts(unitId) {
  const parts = await fetchJson(`/api/units/${unitId}/parts`);
  const list = document.getElementById("partsList");
  if (parts.length === 0) {
    list.innerHTML = `<p class="text-muted">등록된 부품이 없습니다. "부품 등록" 버튼으로 추가하세요.</p>`;
    return;
  }
  list.innerHTML = parts.map(partCardHtml).join("");
  parts.forEach((p) => {
    const card = list.querySelector(`[data-part-id="${p.id}"]`);
    card.querySelector(".replace-btn").addEventListener("click", () => openReplaceModal(p.id));
    card.querySelector(".history-btn").addEventListener("click", () => openHistoryModal(p.id, p.name));
    card.querySelector(".delete-part-btn").addEventListener("click", async () => {
      if (!confirm(`"${p.name}" 부품을 삭제할까요?`)) return;
      await fetchJson(`/api/parts/${p.id}`, { method: "DELETE" });
      loadParts(unitId);
      loadUnits();
    });
  });
}

function partCardHtml(p) {
  const badge = statusBadge[p.status];
  const label = statusLabel[p.status];
  const lastText = p.last_replaced_date ? `최근 교체: ${p.last_replaced_date}` : "교체 이력 없음";
  const dueText = p.next_due ? `다음 교체 예정: ${p.next_due} (${p.days_left >= 0 ? p.days_left + "일 남음" : Math.abs(p.days_left) + "일 초과"})` : "";
  return `
    <div class="part-card" data-part-id="${p.id}">
      <div class="d-flex justify-content-between align-items-start">
        <div>
          <span class="part-title">${escapeHtml(p.name)}</span>
          <span class="badge ${badge} ms-1">${label}</span>
          ${p.spec ? `<div class="part-spec">${escapeHtml(p.spec)}</div>` : ""}
        </div>
        <div class="text-end">
          <button class="btn btn-sm btn-outline-secondary history-btn"><i class="bi bi-clock-history"></i></button>
          <button class="btn btn-sm btn-outline-danger delete-part-btn"><i class="bi bi-trash"></i></button>
        </div>
      </div>
      <div class="mt-2 small text-muted">
        교체 주기: ${p.cycle_days}일 &middot; ${lastText}
        ${dueText ? `<br>${dueText}` : ""}
        ${p.note ? `<br>비고: ${escapeHtml(p.note)}` : ""}
      </div>
      <button class="btn btn-sm btn-primary mt-2 replace-btn"><i class="bi bi-arrow-repeat"></i> 교체 기록 추가</button>
    </div>`;
}

function openReplaceModal(partId) {
  currentPartId = partId;
  document.getElementById("replaceDate").value = new Date().toISOString().slice(0, 10);
  document.getElementById("replaceNote").value = "";
  replaceModal.show();
}

async function openHistoryModal(partId, partName) {
  const history = await fetchJson(`/api/parts/${partId}/history`);
  const list = document.getElementById("historyList");
  document.querySelector("#historyModal .modal-title").textContent = `교체 이력 - ${partName}`;
  if (history.length === 0) {
    list.innerHTML = `<p class="text-muted">교체 이력이 없습니다.</p>`;
  } else {
    list.innerHTML = history
      .map(
        (h) => `
      <div class="history-row d-flex justify-content-between">
        <div><strong>${h.replaced_date}</strong> ${h.note ? " - " + escapeHtml(h.note) : ""}</div>
        <button class="btn btn-sm btn-link text-danger p-0 del-history-btn" data-id="${h.id}">삭제</button>
      </div>`
      )
      .join("");
    list.querySelectorAll(".del-history-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await fetchJson(`/api/history/${btn.dataset.id}`, { method: "DELETE" });
        openHistoryModal(partId, partName);
        if (currentUnitId) loadParts(currentUnitId);
        loadUnits();
      });
    });
  }
  historyModal.show();
}

function openUnitEditModal(unit) {
  document.getElementById("unitEditTitle").textContent = unit ? "유닛 편집" : "유닛 추가";
  document.getElementById("unitEditId").value = unit ? unit.id : "";
  document.getElementById("unitEditName").value = unit ? unit.name : "";
  document.getElementById("unitEditIcon").value = unit ? unit.icon : "⚙️";
  document.getElementById("unitEditColor").value = unit ? unit.color : "#1a3a5c";
  unitEditModal.show();
}

document.addEventListener("DOMContentLoaded", () => {
  unitModal = new bootstrap.Modal(document.getElementById("unitModal"));
  replaceModal = new bootstrap.Modal(document.getElementById("replaceModal"));
  historyModal = new bootstrap.Modal(document.getElementById("historyModal"));
  unitEditModal = new bootstrap.Modal(document.getElementById("unitEditModal"));

  tick();
  setInterval(tick, 1000);
  loadUnits();

  document.getElementById("editModeBtn").addEventListener("click", (e) => {
    editMode = !editMode;
    e.currentTarget.classList.toggle("btn-outline-light", !editMode);
    e.currentTarget.classList.toggle("btn-warning", editMode);
    document.getElementById("addUnitBtn").classList.toggle("d-none", !editMode);
    loadUnits();
  });

  document.getElementById("addUnitBtn").addEventListener("click", () => openUnitEditModal(null));

  document.getElementById("showAddPartBtn").addEventListener("click", () => {
    document.getElementById("addPartForm").classList.remove("d-none");
    document.getElementById("partLastDate").value = "";
  });
  document.getElementById("cancelAddPartBtn").addEventListener("click", () => {
    document.getElementById("addPartForm").classList.add("d-none");
    document.getElementById("addPartForm").reset();
  });

  document.getElementById("addPartForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      name: document.getElementById("partName").value.trim(),
      spec: document.getElementById("partSpec").value.trim(),
      cycle_days: parseInt(document.getElementById("partCycle").value, 10),
      last_replaced_date: document.getElementById("partLastDate").value || null,
      note: document.getElementById("partNote").value.trim(),
    };
    try {
      await fetchJson(`/api/units/${currentUnitId}/parts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      e.target.reset();
      e.target.classList.add("d-none");
      loadParts(currentUnitId);
      loadUnits();
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("replaceForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      replaced_date: document.getElementById("replaceDate").value,
      note: document.getElementById("replaceNote").value.trim(),
    };
    try {
      await fetchJson(`/api/parts/${currentPartId}/replace`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      replaceModal.hide();
      loadParts(currentUnitId);
      loadUnits();
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("unitEditForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = document.getElementById("unitEditId").value;
    const payload = {
      name: document.getElementById("unitEditName").value.trim(),
      icon: document.getElementById("unitEditIcon").value.trim(),
      color: document.getElementById("unitEditColor").value,
    };
    try {
      if (id) {
        await fetchJson(`/api/units/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } else {
        await fetchJson(`/api/units`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      unitEditModal.hide();
      loadUnits();
    } catch (err) {
      alert(err.message);
    }
  });
});
