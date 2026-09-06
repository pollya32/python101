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

let allInventory = [];
let groupModal = null;

async function loadInventory() {
  allInventory = await fetchJson("/api/inventory");
  renderInventory();
}

function updateExportLink() {
  const lowOnly = document.getElementById("lowStockOnlyCheck").checked;
  document.getElementById("exportInventoryBtn").href = `/api/inventory/export${lowOnly ? "?low_only=1" : ""}`;
}

// 규격이 같은 부품끼리 하나의 행으로 묶는다 (부품 이름/소속 유닛이 달라도 병합).
// allInventory는 서버에서 이미 규격 → 소속 유닛 → 부품이름 순으로 정렬되어 오므로
// 순서대로 훑으며 규격이 바뀌는 지점마다 새 그룹을 만들면 된다.
function groupBySpec(rows) {
  const groups = [];
  const bySpec = new Map();
  for (const p of rows) {
    const key = p.spec || "";
    let g = bySpec.get(key);
    if (!g) {
      g = { spec: p.spec, names: [], units: [], totalStock: 0, totalSafetyStock: 0, parts: [] };
      bySpec.set(key, g);
      groups.push(g);
    }
    if (!g.names.includes(p.name)) g.names.push(p.name);
    if (!g.units.includes(p.unit_name)) g.units.push(p.unit_name);
    g.totalStock += p.stock_qty || 0;
    g.totalSafetyStock += p.safety_stock || 0;
    g.parts.push(p);
  }
  return groups;
}

function renderInventory() {
  const lowOnly = document.getElementById("lowStockOnlyCheck").checked;
  updateExportLink();
  const groups = groupBySpec(allInventory).filter((g) => !lowOnly || g.totalStock <= g.totalSafetyStock);
  const tbody = document.getElementById("inventoryBody");
  const empty = document.getElementById("inventoryEmpty");
  if (groups.length === 0) {
    tbody.innerHTML = "";
    empty.classList.remove("d-none");
    empty.textContent = lowOnly ? "안전재고 이하인 부품이 없습니다." : "등록된 부품이 없습니다.";
    return;
  }
  empty.classList.add("d-none");
  tbody.innerHTML = groups
    .map(
      (g, idx) => `
    <tr data-group-idx="${idx}" class="inventory-group-row ${g.totalStock <= g.totalSafetyStock ? "table-danger" : ""}" style="cursor:pointer">
      <td class="text-muted">${escapeHtml(g.spec)}</td>
      <td>${escapeHtml(g.names.join(", "))}</td>
      <td class="text-muted">${escapeHtml(g.units.join(", "))}</td>
      <td class="fw-bold">${g.totalStock}${g.totalSafetyStock ? `<span class="text-muted fw-normal small"> / 안전 ${g.totalSafetyStock}</span>` : ""}</td>
    </tr>`
    )
    .join("");

  tbody.querySelectorAll(".inventory-group-row").forEach((tr) => {
    tr.addEventListener("click", () => {
      const idx = parseInt(tr.dataset.groupIdx, 10);
      openGroupModal(groups[idx]);
    });
  });
}

function openGroupModal(group) {
  document.getElementById("groupModalSpec").textContent = group.spec || "-";
  renderGroupModalBody(group);
  if (!groupModal) groupModal = new bootstrap.Modal(document.getElementById("inventoryGroupModal"));
  groupModal.show();
}

function renderGroupModalBody(group) {
  const tbody = document.getElementById("groupModalBody");
  tbody.innerHTML = group.parts
    .map(
      (p) => `
    <tr data-part-id="${p.id}">
      <td>${escapeHtml(p.name)}</td>
      <td class="text-muted">${escapeHtml(p.unit_name)}</td>
      <td><input type="number" class="form-control form-control-sm stock-qty-input" data-id="${p.id}" value="${p.stock_qty || 0}" min="0" step="1"></td>
      <td><input type="number" class="form-control form-control-sm safety-stock-input" data-id="${p.id}" value="${p.safety_stock || 0}" min="0" step="1"></td>
      <td class="text-muted">${escapeHtml(p.supplier)}</td>
      <td class="text-muted">${escapeHtml(p.supplier_contact)}</td>
      <td class="text-muted">${p.lead_time_days != null ? p.lead_time_days + "일" : "-"}</td>
    </tr>`
    )
    .join("");

  function reloadAfterFieldChange(id, field, value) {
    const entry = allInventory.find((p) => p.id === id);
    if (entry) entry[field] = value;
    renderInventory();
    const group = groupBySpec(allInventory).find((g) => g.parts.some((p) => p.id === id));
    if (group) {
      document.getElementById("groupModalSpec").textContent = group.spec || "-";
      renderGroupModalBody(group);
    }
  }

  tbody.querySelectorAll(".stock-qty-input").forEach((input) => {
    input.addEventListener("change", async () => {
      const id = parseInt(input.dataset.id, 10);
      const stock_qty = parseInt(input.value, 10) || 0;
      try {
        await fetchJson(`/api/parts/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ stock_qty }),
        });
        reloadAfterFieldChange(id, "stock_qty", stock_qty);
      } catch (err) {
        alert(err.message);
      }
    });
  });

  tbody.querySelectorAll(".safety-stock-input").forEach((input) => {
    input.addEventListener("change", async () => {
      const id = parseInt(input.dataset.id, 10);
      const safety_stock = parseInt(input.value, 10) || 0;
      try {
        await fetchJson(`/api/parts/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ safety_stock }),
        });
        reloadAfterFieldChange(id, "safety_stock", safety_stock);
      } catch (err) {
        alert(err.message);
      }
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  tick();
  setInterval(tick, 1000);
  loadInventory();

  document.getElementById("lowStockOnlyCheck").addEventListener("change", renderInventory);
});
