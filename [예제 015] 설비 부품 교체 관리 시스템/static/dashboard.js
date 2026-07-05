let editMode = false;
let equipmentEditModal;

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
      <span class="unit-icon">🏭</span>
      <div class="unit-name">${escapeHtml(eq.name)}</div>
      <div class="unit-part-count">${eq.unit_count}개 유닛</div>
      <div class="unit-edit-actions">
        <button class="edit-unit-btn" title="설비명 편집"><i class="bi bi-pencil"></i></button>
      </div>
    </div>`;
}

function openEquipmentEditModal(eq) {
  document.getElementById("equipmentEditId").value = eq.id;
  document.getElementById("equipmentEditName").value = eq.name;
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

  document.getElementById("equipmentEditForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = document.getElementById("equipmentEditId").value;
    const payload = {
      name: document.getElementById("equipmentEditName").value.trim(),
    };
    try {
      await fetchJson(`/api/equipments/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      equipmentEditModal.hide();
      loadEquipments();
    } catch (err) {
      alert(err.message);
    }
  });
});
