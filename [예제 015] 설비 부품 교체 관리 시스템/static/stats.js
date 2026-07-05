let selectedUnitNames = [];

function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function formatMoney(v) {
  return `${Number(v || 0).toLocaleString("ko-KR")}원`;
}

async function loadStats() {
  const params = new URLSearchParams();
  selectedUnitNames.forEach((name) => params.append("unit_name", name));
  const res = await fetch(`/api/stats?${params.toString()}`);
  const data = await res.json();
  renderUnitFilter(data.unit_names);
  renderPanel("statsCost", data.by_cost, (r) => formatMoney(r.total_cost));
  renderPanel("statsUsage", data.by_usage, (r) => `${r.usage_count}회 교체`);
  renderPanel("statsCycle", data.by_short_cycle, (r) => `${r.min_cycle_days}일 주기`);
  renderPanel("statsPartCount", data.by_part_count, (r) => `${r.part_count}개`);
}

function renderPanel(elId, rows, metricText) {
  const el = document.getElementById(elId);
  if (rows.length === 0) {
    el.innerHTML = `<p class="text-muted text-center py-4 mb-0">데이터가 없습니다.</p>`;
    return;
  }
  el.innerHTML = rows
    .map(
      (r, i) => `
    <div class="stats-row" data-unit-id="${r.unit_id}">
      <span class="stats-rank">${i + 1}</span>
      <div class="alert-main">
        <div class="alert-title">
          <span>${r.equipment_icon}</span> ${escapeHtml(r.equipment_name)}
          <span class="alert-sep">›</span> ${r.unit_icon} ${escapeHtml(r.unit_name)}
        </div>
        <div class="alert-meta">${metricText(r)}</div>
      </div>
      <i class="bi bi-chevron-right alert-chevron"></i>
    </div>`
    )
    .join("");
  rows.forEach((r) => {
    const row = el.querySelector(`[data-unit-id="${r.unit_id}"]`);
    row.addEventListener("click", () => {
      window.location.href = `/unit/${r.unit_id}`;
    });
  });
}

function renderUnitFilter(names) {
  const menu = document.getElementById("unitFilterMenu");
  if (menu.dataset.rendered === "1") return;
  menu.dataset.rendered = "1";
  menu.innerHTML = names
    .map(
      (name, i) => `
    <div class="form-check">
      <input class="form-check-input" type="checkbox" value="${escapeHtml(name)}" id="unitFilter${i}">
      <label class="form-check-label" for="unitFilter${i}">${escapeHtml(name)}</label>
    </div>`
    )
    .join("");
  menu.querySelectorAll(".form-check-input").forEach((cb) => {
    cb.addEventListener("change", () => {
      selectedUnitNames = Array.from(menu.querySelectorAll(".form-check-input:checked")).map(
        (el) => el.value
      );
      updateFilterUi();
      loadStats();
    });
  });
}

function updateFilterUi() {
  const countEl = document.getElementById("unitFilterCount");
  const clearBtn = document.getElementById("clearFilterBtn");
  if (selectedUnitNames.length > 0) {
    countEl.innerHTML = `<span class="nav-badge">${selectedUnitNames.length}</span>`;
    clearBtn.classList.remove("d-none");
  } else {
    countEl.innerHTML = "";
    clearBtn.classList.add("d-none");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  tick();
  setInterval(tick, 1000);
  loadStats();

  document.getElementById("clearFilterBtn").addEventListener("click", () => {
    selectedUnitNames = [];
    document
      .querySelectorAll("#unitFilterMenu .form-check-input")
      .forEach((cb) => (cb.checked = false));
    updateFilterUi();
    loadStats();
  });
});
