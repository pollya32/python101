function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

const STATUS_LABEL = { ok: "정상", soon: "교체 임박", overdue: "교체 필요", unknown: "미기록" };

let searchTimer;

async function loadEquipmentFilterOptions() {
  const res = await fetch("/api/equipments");
  if (res.status === 401) {
    window.location.href = "/login";
    return;
  }
  const equipments = await res.json();
  const select = document.getElementById("equipmentFilter");
  select.innerHTML =
    '<option value="">전체 설비</option>' +
    equipments.map((e) => `<option value="${e.id}">${escapeHtml(e.name)}</option>`).join("");
}

async function runSearch() {
  const q = document.getElementById("partSearchInput").value.trim();
  const status = document.getElementById("statusFilter").value;
  const equipmentId = document.getElementById("equipmentFilter").value;
  const list = document.getElementById("searchResults");
  const hint = document.getElementById("searchHintMsg");

  if (!q) {
    list.innerHTML = "";
    hint.textContent = "부품명 또는 규격을 입력하면 모든 설비에서 찾아드립니다.";
    hint.classList.remove("d-none");
    return;
  }

  const params = new URLSearchParams({ q });
  if (status) params.set("status", status);
  if (equipmentId) params.set("equipment_id", equipmentId);
  const res = await fetch(`/api/search?${params.toString()}`);
  if (res.status === 401) {
    window.location.href = "/login";
    return;
  }
  const parts = await res.json();

  if (parts.length === 0) {
    list.innerHTML = "";
    hint.textContent = "검색 결과가 없습니다.";
    hint.classList.remove("d-none");
    return;
  }

  hint.classList.add("d-none");
  list.innerHTML = parts.map(searchRowHtml).join("");
  parts.forEach((p) => {
    const row = list.querySelector(`[data-row-id="${p.id}"]`);
    row.addEventListener("click", () => {
      window.location.href = `/unit/${p.unit_id}`;
    });
  });
}

function searchRowHtml(p) {
  const label = STATUS_LABEL[p.status] || "미기록";
  const lastText = p.last_replaced_date ? `최근 교체 ${p.last_replaced_date}` : "교체 이력 없음";
  const daysText = p.status === "overdue" ? `${Math.abs(p.days_left)}일 초과`
    : (p.status === "soon" || p.status === "ok") ? `${p.days_left}일 남음`
    : "";
  const specText = p.spec ? `${escapeHtml(p.spec)} &middot; ` : "";
  return `
    <div class="alert-row" data-row-id="${p.id}">
      <span class="badge badge-${p.status} alert-badge">${label}</span>
      <div class="alert-main">
        <div class="alert-title">
          <span>${p.equipment_icon}</span> ${escapeHtml(p.equipment_name)}
          <span class="alert-sep">›</span> ${escapeHtml(p.unit_name)}
          <span class="alert-sep">›</span> <strong>${p.icon} ${escapeHtml(p.name)}</strong>
        </div>
        <div class="alert-meta">${specText}교체 주기 ${p.cycle_days}일 &middot; ${lastText}${daysText ? " &middot; " + daysText : ""}</div>
      </div>
      <i class="bi bi-chevron-right alert-chevron"></i>
    </div>`;
}

document.addEventListener("DOMContentLoaded", async () => {
  tick();
  setInterval(tick, 1000);
  await loadEquipmentFilterOptions();
  document.getElementById("partSearchInput").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 200);
  });
  document.getElementById("statusFilter").addEventListener("change", runSearch);
  document.getElementById("equipmentFilter").addEventListener("change", runSearch);

  const q = new URLSearchParams(window.location.search).get("q");
  if (q) {
    document.getElementById("partSearchInput").value = q;
    runSearch();
  }
});
