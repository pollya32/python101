let editMode = false;
let equipmentEditModal;

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

async function loadEquipments() {
  const equipments = await fetchJson("/api/equipments");
  renderGrid(equipments);
}

function renderGrid(equipments) {
  const grid = document.getElementById("equipmentGrid");
  grid.innerHTML = equipments.map(equipmentCardHtml).join("");
  equipments.forEach((eq) => {
    const card = grid.querySelector(`[data-equipment-id="${eq.id}"]`);
    card.querySelector(".edit-unit-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      openEquipmentEditModal(eq);
    });
    card.querySelector(".delete-unit-btn")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`"${eq.name}" 설비를 삭제할까요? 등록된 유닛/부품/이력이 모두 함께 삭제됩니다.`)) return;
      await fetchJson(`/api/equipments/${eq.id}`, { method: "DELETE" });
      loadEquipments();
    });
    card.addEventListener("click", (e) => {
      if (editMode) return;
      if (e.target.closest(".unit-edit-actions")) return;
      window.location.href = `/equipment/${eq.id}`;
    });
  });
}

function equipmentCardHtml(eq) {
  return `
    <div class="equipment-card ${editMode ? "edit-mode" : ""}" data-equipment-id="${eq.id}">
      <span class="unit-status-dot dot-${eq.overall_status}"></span>
      <div class="unit-icon-wrap">
        <span class="unit-icon">${eq.icon}</span>
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

  tick();
  setInterval(tick, 1000);
  loadEquipments();

  document.getElementById("editModeBtn").addEventListener("click", (e) => {
    editMode = !editMode;
    e.currentTarget.classList.toggle("btn-outline-light", !editMode);
    e.currentTarget.classList.toggle("btn-warning", editMode);
    loadEquipments();
  });

  document.getElementById("addEquipmentBtn").addEventListener("click", () => openEquipmentEditModal(null));

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
