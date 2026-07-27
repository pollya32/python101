function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s || "";
  return div.innerHTML;
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

let lastTrashData = { equipments: [], units: [], parts: [] };

async function loadTrash() {
  const data = await fetchJson("/api/trash");
  lastTrashData = data;
  renderEquipments(data.equipments);
  renderUnits(data.units);
  renderParts(data.parts);
}

function actionButtons(type, id, restoreLabel) {
  return `
    <button class="btn btn-sm btn-outline-primary restore-btn" data-type="${type}" data-id="${id}">
      <i class="bi bi-arrow-counterclockwise"></i> ${restoreLabel}
    </button>
    <button class="btn btn-sm btn-outline-danger permanent-delete-btn" data-type="${type}" data-id="${id}">
      <i class="bi bi-trash3"></i> 영구 삭제
    </button>`;
}

function renderEquipments(equipments) {
  const tbody = document.getElementById("trashEquipmentBody");
  const empty = document.getElementById("trashEquipmentEmpty");
  if (equipments.length === 0) {
    tbody.innerHTML = "";
    empty.classList.remove("d-none");
  } else {
    empty.classList.add("d-none");
    tbody.innerHTML = equipments
      .map(
        (e) => `
      <tr>
        <td>${escapeHtml(e.name)}</td>
        <td>${e.unit_count}개</td>
        <td class="text-muted small">${e.deleted_at}</td>
        <td class="d-flex gap-2">${actionButtons("equipment", e.id, "설비 복원")}</td>
      </tr>`
      )
      .join("");
  }
}

function renderUnits(units) {
  const tbody = document.getElementById("trashUnitBody");
  const empty = document.getElementById("trashUnitEmpty");
  if (units.length === 0) {
    tbody.innerHTML = "";
    empty.classList.remove("d-none");
  } else {
    empty.classList.add("d-none");
    tbody.innerHTML = units
      .map(
        (u) => `
      <tr>
        <td>${escapeHtml(u.name)}</td>
        <td class="text-muted">${escapeHtml(u.equipment_name || "-")}</td>
        <td class="text-muted small">${u.deleted_at}</td>
        <td class="d-flex gap-2">${actionButtons("unit", u.id, "유닛 복원")}</td>
      </tr>`
      )
      .join("");
  }
}

function renderParts(parts) {
  const tbody = document.getElementById("trashPartBody");
  const empty = document.getElementById("trashPartEmpty");
  if (parts.length === 0) {
    tbody.innerHTML = "";
    empty.classList.remove("d-none");
  } else {
    empty.classList.add("d-none");
    tbody.innerHTML = parts
      .map(
        (p) => `
      <tr>
        <td>${escapeHtml(p.name)}</td>
        <td class="text-muted">${escapeHtml(p.unit_name || "-")}</td>
        <td class="text-muted">${escapeHtml(p.equipment_name || "-")}</td>
        <td class="text-muted small">${p.deleted_at}</td>
        <td class="d-flex gap-2">${actionButtons("part", p.id, "부품 복원")}</td>
      </tr>`
      )
      .join("");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  tick();
  setInterval(tick, 1000);
  loadTrash();

  document.getElementById("emptyTrashBtn").addEventListener("click", async () => {
    const total = lastTrashData.equipments.length + lastTrashData.units.length + lastTrashData.parts.length;
    if (total === 0) {
      alert("휴지통이 비어 있습니다.");
      return;
    }
    if (!confirm(`휴지통에 있는 항목 ${total}개를 모두 영구적으로 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.`)) return;
    try {
      const result = await fetchJson("/api/trash/empty", { method: "POST" });
      await loadTrash();
      alert(
        `휴지통을 비웠습니다. (설비 ${result.equipment_count}개, 유닛 ${result.unit_count}개, 부품 ${result.part_count}개)`
      );
    } catch (err) {
      alert(err.message);
    }
  });

  document.addEventListener("click", async (e) => {
    const restoreBtn = e.target.closest(".restore-btn");
    if (restoreBtn) {
      const { type, id } = restoreBtn.dataset;
      try {
        await fetchJson(`/api/trash/${type}/${id}/restore`, { method: "POST" });
        await loadTrash();
      } catch (err) {
        alert(err.message);
      }
      return;
    }
    const deleteBtn = e.target.closest(".permanent-delete-btn");
    if (deleteBtn) {
      const { type, id } = deleteBtn.dataset;
      if (!confirm("영구적으로 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.")) return;
      try {
        await fetchJson(`/api/trash/${type}/${id}`, { method: "DELETE" });
        await loadTrash();
      } catch (err) {
        alert(err.message);
      }
    }
  });
});
