let editMode = false;
let equipmentEditModal;
let allEquipments = [];

const ICON_CHOICES = [
  "🏭", "⚙️", "🔧", "🔩", "🛠️", "🪛", "🔨", "📦",
  "🖥️", "🖨️", "💻", "📡", "🎛️", "🚨", "💡", "🔌",
  "⚡", "🔋", "🌡️", "💧", "🧪", "🧯", "🧰", "⚗️",
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

function gridPos(index, cols = 5) {
  const row = Math.floor(index / cols);
  const col = index % cols;
  const x = 10 + col * (80 / Math.max(cols - 1, 1));
  const y = Math.min(15 + row * 22, 92);
  return { x, y };
}

async function loadEquipments() {
  allEquipments = await fetchJson("/api/equipments");
  updateAlertsNavBadge();
  applyFilterSort();
}

function updateAlertsNavBadge() {
  const total = allEquipments.reduce((sum, eq) => sum + eq.overdue_count, 0);
  const badge = document.getElementById("alertsNavBadge");
  badge.innerHTML = total > 0 ? `<span class="nav-badge">${total}</span>` : "";
}

function applyFilterSort() {
  const q = document.getElementById("equipmentSearch").value.trim().toLowerCase();
  const sortBy = document.getElementById("equipmentSort").value;
  let list = allEquipments.filter((eq) => eq.name.toLowerCase().includes(q));
  if (sortBy === "overdue") {
    list = [...list].sort((a, b) => b.overdue_count - a.overdue_count || a.id - b.id);
  } else if (sortBy === "name") {
    list = [...list].sort((a, b) => a.name.localeCompare(b.name, "ko") || a.id - b.id);
  } else {
    list = [...list].sort((a, b) => a.id - b.id);
  }
  document.getElementById("noResultsMsg").classList.toggle("d-none", list.length > 0);
  renderGrid(list, sortBy);
}

function renderGrid(equipments, sortBy) {
  const grid = document.getElementById("equipmentGrid");
  grid.innerHTML = equipments.map(equipmentCardHtml).join("") +
    '<div id="alignGuideV" class="align-guide align-guide-v d-none"></div>' +
    '<div id="alignGuideH" class="align-guide align-guide-h d-none"></div>';
  equipments.forEach((eq, idx) => {
    const card = grid.querySelector(`[data-equipment-id="${eq.id}"]`);
    let x, y;
    if (sortBy === "custom") {
      x = eq.pos_x;
      y = eq.pos_y;
    } else {
      const p = gridPos(idx);
      x = p.x;
      y = p.y;
    }
    card.style.left = `${x}%`;
    card.style.top = `${y}%`;

    card.querySelector(".edit-unit-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      openEquipmentEditModal(eq);
    });
    card.querySelector(".delete-unit-btn")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`"${eq.name}" 설비를 삭제할까요? 소속 유닛/부품도 함께 휴지통으로 이동합니다. (휴지통에서 복원할 수 있습니다)`)) return;
      await fetchJson(`/api/equipments/${eq.id}`, { method: "DELETE" });
      loadEquipments();
    });
    card.addEventListener("click", (e) => {
      if (editMode) return;
      if (e.target.closest(".unit-edit-actions")) return;
      window.location.href = `/equipment/${eq.id}`;
    });

    makeDraggable(card, eq);
  });
}

function makeDraggable(card, eq) {
  card.addEventListener("mousedown", (e) => {
    if (!editMode) return;
    if (e.target.closest(".unit-edit-actions")) return;
    e.preventDefault();

    const canvas = document.getElementById("equipmentGrid");
    const rect = canvas.getBoundingClientRect();
    const startX = e.clientX;
    const startY = e.clientY;
    const startLeft = (parseFloat(card.style.left) / 100) * rect.width;
    const startTop = (parseFloat(card.style.top) / 100) * rect.height;
    let moved = false;

    const SNAP_PX = 8;
    const otherPositions = Array.from(canvas.querySelectorAll(".equipment-card"))
      .filter((c) => c !== card)
      .map((c) => ({
        x: (parseFloat(c.style.left) / 100) * rect.width,
        y: (parseFloat(c.style.top) / 100) * rect.height,
      }));
    const guideV = document.getElementById("alignGuideV");
    const guideH = document.getElementById("alignGuideH");

    card.classList.add("dragging");

    function onMove(ev) {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved = true;
      let px = Math.min(Math.max(startLeft + dx, rect.width * 0.04), rect.width * 0.96);
      let py = Math.min(Math.max(startTop + dy, rect.height * 0.04), rect.height * 0.96);

      let snappedX = null;
      let snappedY = null;
      let bestDx = SNAP_PX;
      let bestDy = SNAP_PX;
      for (const pos of otherPositions) {
        const dxAbs = Math.abs(pos.x - px);
        if (dxAbs <= bestDx) {
          bestDx = dxAbs;
          snappedX = pos.x;
        }
        const dyAbs = Math.abs(pos.y - py);
        if (dyAbs <= bestDy) {
          bestDy = dyAbs;
          snappedY = pos.y;
        }
      }
      if (snappedX !== null) px = snappedX;
      if (snappedY !== null) py = snappedY;

      guideV.classList.toggle("d-none", snappedX === null);
      if (snappedX !== null) guideV.style.left = `${(snappedX / rect.width) * 100}%`;
      guideH.classList.toggle("d-none", snappedY === null);
      if (snappedY !== null) guideH.style.top = `${(snappedY / rect.height) * 100}%`;

      card.style.left = `${(px / rect.width) * 100}%`;
      card.style.top = `${(py / rect.height) * 100}%`;
    }

    async function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      card.classList.remove("dragging");
      guideV.classList.add("d-none");
      guideH.classList.add("d-none");
      if (moved) {
        const pos_x = parseFloat(card.style.left);
        const pos_y = parseFloat(card.style.top);
        eq.pos_x = pos_x;
        eq.pos_y = pos_y;
        await fetchJson(`/api/equipments/${eq.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pos_x, pos_y }),
        });
        const sortSelect = document.getElementById("equipmentSort");
        if (sortSelect.value !== "custom") sortSelect.value = "custom";
        applyFilterSort();
      }
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

function equipmentCardHtml(eq) {
  return `
    <div class="equipment-card ${editMode ? "edit-mode" : ""}" data-equipment-id="${eq.id}">
      <span class="unit-status-dot dot-${eq.overall_status}"></span>
      <div class="unit-icon-wrap">
        <span class="unit-icon">${eq.icon}</span>
        ${eq.soon_count > 0 ? `<span class="soon-badge" title="교체 임박 부품 ${eq.soon_count}건">${eq.soon_count}</span>` : ""}
        ${eq.overdue_count > 0 ? `<span class="overdue-badge" title="교체 필요 부품 ${eq.overdue_count}건">${eq.overdue_count}</span>` : ""}
      </div>
      <div class="unit-name">${escapeHtml(eq.name)}</div>
      <div class="unit-part-count">${eq.unit_count}개 유닛</div>
      <div class="unit-edit-actions">
        <button class="edit-unit-btn" title="편집"><i class="bi bi-pencil"></i></button>
        <button class="delete-unit-btn" title="삭제"><i class="bi bi-trash"></i></button>
      </div>
    </div>`;
}

function openEquipmentEditModal(eq) {
  document.getElementById("equipmentEditTitle").textContent = eq ? "설비 편집" : "설비 추가";
  document.getElementById("equipmentEditId").value = eq ? eq.id : "";
  document.getElementById("equipmentEditName").value = eq ? eq.name : "";
  const icon = eq ? eq.icon : "🏭";
  document.getElementById("equipmentEditIcon").value = icon;
  renderIconPicker("equipmentIconPicker", "equipmentEditIcon", icon);
  equipmentEditModal.show();
}

document.addEventListener("DOMContentLoaded", () => {
  equipmentEditModal = new bootstrap.Modal(document.getElementById("equipmentEditModal"));
  const changePasswordModal = new bootstrap.Modal(document.getElementById("changePasswordModal"));

  tick();
  setInterval(tick, 1000);
  loadEquipments();

  document.getElementById("changePasswordBtn").addEventListener("click", () => {
    document.getElementById("changePasswordForm").reset();
    changePasswordModal.show();
  });

  document.getElementById("backupBtn").addEventListener("click", async () => {
    try {
      const result = await fetchJson("/api/backup", { method: "POST" });
      alert(`백업이 저장되었습니다.\n${result.path}`);
    } catch (err) {
      alert(err.message);
    }
  });
  document.getElementById("changePasswordForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await fetchJson("/api/auth/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: document.getElementById("currentPasswordInput").value,
          new_password: document.getElementById("newPasswordInput").value,
        }),
      });
      changePasswordModal.hide();
      alert("비밀번호가 변경되었습니다.");
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("editModeBtn").addEventListener("click", (e) => {
    editMode = !editMode;
    e.currentTarget.classList.toggle("btn-outline-light", !editMode);
    e.currentTarget.classList.toggle("btn-warning", editMode);
    loadEquipments();
  });

  document.getElementById("addEquipmentBtn").addEventListener("click", () => openEquipmentEditModal(null));
  document.getElementById("statsBtn").addEventListener("click", () => {
    window.open("/stats", "_blank", "noopener,noreferrer");
  });

  document.getElementById("equipmentSearch").addEventListener("input", applyFilterSort);
  document.getElementById("equipmentSort").addEventListener("change", applyFilterSort);

  document.getElementById("equipmentEditForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = document.getElementById("equipmentEditId").value;
    const payload = {
      name: document.getElementById("equipmentEditName").value.trim(),
      icon: document.getElementById("equipmentEditIcon").value.trim(),
    };
    try {
      if (id) {
        await fetchJson(`/api/equipments/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } else {
        await fetchJson("/api/equipments", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      equipmentEditModal.hide();
      loadEquipments();
    } catch (err) {
      alert(err.message);
    }
  });
});

// ── 현황 메일 발송 (사내 메일 API) ─────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  const mailModalEl = document.getElementById("mailModal");
  if (!mailModalEl) return;
  const mailModal = new bootstrap.Modal(mailModalEl);
  const statusMsg = document.getElementById("mailStatusMsg");

  async function loadRecipients() {
    const list = await fetchJson("/api/mail/recipients");
    const ul = document.getElementById("mailRecipientList");
    if (list.length === 0) {
      ul.innerHTML = '<li class="list-group-item text-muted small">등록된 수신자가 없습니다.</li>';
      return;
    }
    ul.innerHTML = list
      .map(
        (r) => `
      <li class="list-group-item d-flex justify-content-between align-items-center py-1">
        <span>${r.email_id}@samsung.com</span>
        <button class="btn btn-sm btn-outline-danger py-0 del-recipient-btn" data-id="${r.id}">삭제</button>
      </li>`
      )
      .join("");
    ul.querySelectorAll(".del-recipient-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await fetchJson(`/api/mail/recipients/${btn.dataset.id}`, { method: "DELETE" });
        loadRecipients();
      });
    });
  }

  async function loadSchedule() {
    const data = await fetchJson("/api/mail/schedule");
    document.getElementById("mailScheduleTime").value = data.time || "";
  }

  document.getElementById("mailBtn").addEventListener("click", () => {
    statusMsg.textContent = "";
    loadRecipients();
    loadSchedule();
    mailModal.show();
  });

  document.getElementById("addRecipientBtn").addEventListener("click", async () => {
    const input = document.getElementById("mailRecipientInput");
    if (!input.value.trim()) return;
    try {
      await fetchJson("/api/mail/recipients", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email_id: input.value.trim() }),
      });
      input.value = "";
      loadRecipients();
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("saveScheduleBtn").addEventListener("click", async () => {
    const t = document.getElementById("mailScheduleTime").value;
    await fetchJson("/api/mail/schedule", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ time: t }),
    });
    statusMsg.textContent = t ? `매일 ${t}에 자동 발송됩니다.` : "자동 발송이 해제되었습니다.";
  });

  document.getElementById("clearScheduleBtn").addEventListener("click", async () => {
    document.getElementById("mailScheduleTime").value = "";
    await fetchJson("/api/mail/schedule", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ time: "" }),
    });
    statusMsg.textContent = "자동 발송이 해제되었습니다.";
  });

  document.getElementById("sendMailNowBtn").addEventListener("click", async () => {
    const btn = document.getElementById("sendMailNowBtn");
    btn.disabled = true;
    statusMsg.textContent = "발송 중...";
    try {
      const res = await fetch("/api/mail/send", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      statusMsg.textContent = data.message || "오류가 발생했습니다";
    } catch (err) {
      statusMsg.textContent = "발송 요청 실패: " + err.message;
    } finally {
      btn.disabled = false;
    }
  });
});
