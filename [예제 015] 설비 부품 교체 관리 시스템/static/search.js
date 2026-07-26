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
let searchPage = 1;
let searchTotalPages = 1;

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

async function runSearch(resetPage = false) {
  if (resetPage) searchPage = 1;
  const q = document.getElementById("partSearchInput").value.trim();
  const status = document.getElementById("statusFilter").value;
  const equipmentId = document.getElementById("equipmentFilter").value;
  const list = document.getElementById("searchResults");
  const hint = document.getElementById("searchHintMsg");
  const pager = document.getElementById("searchPager");

  // 검색어/상태/설비 필터가 전부 비어있으면 서버에 요청조차 보내지 않는다. 검색 페이지를
  // 열자마자 등록된 부품 전체를 매번 불러오던 것이 느려지는 원인이었다.
  if (!q && !status && !equipmentId) {
    list.innerHTML = "";
    searchTotalPages = 1;
    pager.classList.add("d-none");
    pager.classList.remove("d-flex");
    hint.textContent = "검색어를 입력하거나 상태/설비를 선택하세요.";
    hint.classList.remove("d-none");
    return;
  }

  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (status) params.set("status", status);
  if (equipmentId) params.set("equipment_id", equipmentId);
  params.set("page", searchPage);
  const res = await fetch(`/api/search?${params.toString()}`);
  if (res.status === 401) {
    window.location.href = "/login";
    return;
  }
  const payload = await res.json();
  const parts = payload.items;
  searchTotalPages = payload.total_pages;

  if (parts.length === 0) {
    list.innerHTML = "";
    pager.classList.add("d-none");
    pager.classList.remove("d-flex");
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

  if (payload.total_pages > 1) {
    document.getElementById("searchPageInfo").textContent =
      `${payload.page} / ${payload.total_pages} 페이지 · 전체 ${payload.total}건`;
    document.getElementById("searchPrevBtn").disabled = payload.page <= 1;
    document.getElementById("searchNextBtn").disabled = payload.page >= payload.total_pages;
    pager.classList.remove("d-none");
    pager.classList.add("d-flex");
  } else {
    pager.classList.add("d-none");
    pager.classList.remove("d-flex");
  }
}

function searchRowHtml(p) {
  const label = p.label || STATUS_LABEL[p.status] || "미기록";
  const lastText = p.last_replaced_date ? `최근 교체 ${p.last_replaced_date}` : "교체 이력 없음";
  const daysText = p.status === "overdue" ? `${Math.abs(p.days_left)}일 초과`
    : (p.status === "soon" || p.status === "ok") ? `${p.days_left}일 남음`
    : "";
  const specText = p.spec ? `${escapeHtml(p.spec)} &middot; ` : "";
  const cycleText = p.cycle_days != null ? `${p.cycle_days}일` : "N/A";
  return `
    <div class="alert-row" data-row-id="${p.id}">
      <span class="badge badge-${p.status} alert-badge">${label}</span>
      <div class="alert-main">
        <div class="alert-title">
          <span>${p.equipment_icon}</span> ${escapeHtml(p.equipment_name)}
          <span class="alert-sep">›</span> ${escapeHtml(p.unit_name)}
          <span class="alert-sep">›</span> <strong>${p.icon} ${escapeHtml(p.name)}</strong>
        </div>
        <div class="alert-meta">${specText}교체 주기 ${cycleText} &middot; ${lastText}${daysText ? " &middot; " + daysText : ""}</div>
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
    searchTimer = setTimeout(() => runSearch(true), 200);
  });
  document.getElementById("statusFilter").addEventListener("change", () => runSearch(true));
  document.getElementById("equipmentFilter").addEventListener("change", () => runSearch(true));
  document.getElementById("searchPrevBtn").addEventListener("click", () => {
    if (searchPage > 1) {
      searchPage -= 1;
      runSearch();
    }
  });
  document.getElementById("searchNextBtn").addEventListener("click", () => {
    if (searchPage < searchTotalPages) {
      searchPage += 1;
      runSearch();
    }
  });

  // 통계 등 다른 화면에서 ?q=로 넘어온 경우에만 페이지를 열자마자 자동으로 검색한다.
  // 그 외(검색 페이지를 직접 열었을 때)는 아무것도 입력/선택하기 전까지 조회하지 않는다.
  const q = new URLSearchParams(window.location.search).get("q");
  if (q) {
    document.getElementById("partSearchInput").value = q;
    runSearch(true);
  } else {
    document.getElementById("searchHintMsg").classList.remove("d-none");
  }
});
