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

async function loadInventory() {
  allInventory = await fetchJson("/api/inventory");
  renderInventory();
}

function updateExportLink() {
  const lowOnly = document.getElementById("lowStockOnlyCheck").checked;
  document.getElementById("exportInventoryBtn").href = `/api/inventory/export${lowOnly ? "?low_only=1" : ""}`;
}

function renderInventory() {
  const lowOnly = document.getElementById("lowStockOnlyCheck").checked;
  updateExportLink();
  const rows = lowOnly ? allInventory.filter((p) => (p.stock_qty || 0) <= 0) : allInventory;
  const tbody = document.getElementById("inventoryBody");
  const empty = document.getElementById("inventoryEmpty");
  if (rows.length === 0) {
    tbody.innerHTML = "";
    empty.classList.remove("d-none");
    empty.textContent = lowOnly ? "재고 부족 부품이 없습니다." : "등록된 부품이 없습니다.";
    return;
  }
  empty.classList.add("d-none");
  tbody.innerHTML = rows
    .map(
      (p) => `
    <tr data-part-id="${p.id}" class="${(p.stock_qty || 0) <= 0 ? "table-danger" : ""}">
      <td class="text-muted">${escapeHtml(p.spec)}</td>
      <td>${escapeHtml(p.name)}</td>
      <td class="text-muted">${escapeHtml(p.unit_name)}</td>
      <td><input type="number" class="form-control form-control-sm stock-qty-input" data-id="${p.id}" value="${p.stock_qty || 0}" min="0" step="1"></td>
      <td class="text-muted">${escapeHtml(p.supplier)}</td>
      <td class="text-muted">${escapeHtml(p.supplier_contact)}</td>
      <td class="text-muted">${p.lead_time_days != null ? p.lead_time_days + "일" : "-"}</td>
    </tr>`
    )
    .join("");

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
        const entry = allInventory.find((p) => p.id === id);
        if (entry) entry.stock_qty = stock_qty;
        renderInventory();
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
