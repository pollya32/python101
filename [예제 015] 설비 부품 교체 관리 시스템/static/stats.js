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

function formatCycle(days, unit) {
  if (days === null || days === undefined) {
    return "미등록";
  }
  if (unit === "년") {
    return `${Math.round((days / 365) * 100) / 100}년`;
  }
  return `${days}일`;
}

// ── 통계 막대 차트 (규격/유닛 상위 5건, 순수 SVG로 그려서 별도 라이브러리 없이 동작) ──
const CHART_W = 460;
const CHART_LABEL_W = 108;
const CHART_VALUE_W = 60;
const CHART_ROW_H = 30;
const CHART_BAR_H = 14;

let chartTooltip = null;

function ensureChartTooltip() {
  if (chartTooltip) return chartTooltip;
  chartTooltip = document.createElement("div");
  chartTooltip.className = "stat-chart-tooltip d-none";
  document.body.appendChild(chartTooltip);
  return chartTooltip;
}

function showChartTooltip(evt, text) {
  const tip = ensureChartTooltip();
  tip.textContent = text;
  tip.classList.remove("d-none");
  positionChartTooltip(evt);
}

function positionChartTooltip(evt) {
  if (!chartTooltip) return;
  chartTooltip.style.left = `${evt.clientX + 14}px`;
  chartTooltip.style.top = `${evt.clientY + 14}px`;
}

function hideChartTooltip() {
  if (chartTooltip) chartTooltip.classList.add("d-none");
}

function renderBarChart(elId, rows, opts) {
  const el = document.getElementById(elId);
  const top = rows.slice(0, 5);
  if (top.length === 0) {
    el.innerHTML = "";
    return;
  }
  const maxVal = Math.max(...top.map(opts.valueFn), 1);
  const barMaxW = CHART_W - CHART_LABEL_W - CHART_VALUE_W - 16;
  const height = top.length * CHART_ROW_H + 6;

  const bars = top
    .map((r, i) => {
      const val = opts.valueFn(r);
      const w = maxVal > 0 ? Math.max((val / maxVal) * barMaxW, val > 0 ? 4 : 0) : 0;
      const y = i * CHART_ROW_H;
      const rawLabel = opts.labelFn(r);
      const label = rawLabel.length > 11 ? rawLabel.slice(0, 10) + "…" : rawLabel;
      const valueText = opts.formatValue(val);
      return `
      <g class="stat-bar-row" data-idx="${i}" tabindex="0" role="button" aria-label="${escapeHtml(rawLabel)}: ${escapeHtml(valueText)}">
        <text x="${CHART_LABEL_W - 8}" y="${y + CHART_BAR_H + 1}" text-anchor="end" class="stat-bar-label">${escapeHtml(label)}</text>
        <rect x="${CHART_LABEL_W}" y="${y + 3}" width="${barMaxW}" height="${CHART_BAR_H}" rx="${CHART_BAR_H / 2}" class="stat-bar-track"></rect>
        <rect x="${CHART_LABEL_W}" y="${y + 3}" width="${w}" height="${CHART_BAR_H}" rx="${CHART_BAR_H / 2}" class="stat-bar-fill"></rect>
        <text x="${CHART_LABEL_W + barMaxW + 8}" y="${y + CHART_BAR_H + 1}" class="stat-bar-value">${escapeHtml(valueText)}</text>
      </g>`;
    })
    .join("");

  el.innerHTML = `<svg viewBox="0 0 ${CHART_W} ${height}" class="stat-bar-chart" role="img" aria-label="상위 ${top.length}건 막대 그래프">${bars}</svg>`;

  top.forEach((r, i) => {
    const row = el.querySelector(`[data-idx="${i}"]`);
    if (!row) return;
    const rawLabel = opts.labelFn(r);
    const valueText = opts.formatValue(opts.valueFn(r));
    row.addEventListener("mouseenter", (e) => showChartTooltip(e, `${rawLabel} · ${valueText}`));
    row.addEventListener("mousemove", positionChartTooltip);
    row.addEventListener("mouseleave", hideChartTooltip);
    row.addEventListener("focus", (e) => showChartTooltip(e, `${rawLabel} · ${valueText}`));
    row.addEventListener("blur", hideChartTooltip);
    row.addEventListener("click", () => opts.onClick(r));
  });
}

function currentPeriodParams() {
  const params = new URLSearchParams();
  selectedUnitNames.forEach((name) => params.append("unit_name", name));
  const startDate = document.getElementById("statsStartDate").value;
  const endDate = document.getElementById("statsEndDate").value;
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  return params;
}

async function loadStats() {
  const params = currentPeriodParams();
  const res = await fetch(`/api/stats?${params.toString()}`);
  if (res.status === 401) {
    window.location.href = "/login";
    return;
  }
  const data = await res.json();
  renderUnitFilter(data.unit_names);
  renderPartSpecPanel(
    "statsCost", data.by_cost,
    (r) => `구매금액 ${formatMoney(r.purchase_cost)} · 교체 합산 ${formatMoney(r.replacement_cost_total)}`
  );
  renderPartSpecPanel("statsUsage", data.by_usage, (r) => `${r.usage_count}회 교체`);
  renderPartSpecPanel(
    "statsCycle", data.by_short_cycle,
    (r) => `표준주기 ${formatCycle(r.min_cycle_days, r.min_cycle_unit)} · 실제 평균 ${r.avg_actual_interval_days}일`
  );

  const goToSearch = (r) => { window.location.href = `/search?q=${encodeURIComponent(r.name)}`; };
  renderBarChart("statsCostChart", data.by_cost, {
    labelFn: (r) => r.name,
    valueFn: (r) => r.replacement_cost_total,
    formatValue: formatMoney,
    onClick: goToSearch,
  });
  renderBarChart("statsUsageChart", data.by_usage, {
    labelFn: (r) => r.name,
    valueFn: (r) => r.usage_count,
    formatValue: (v) => `${v}회`,
    onClick: goToSearch,
  });
  renderBarChart("statsCycleChart", data.by_short_cycle, {
    labelFn: (r) => r.name,
    valueFn: (r) => r.avg_actual_interval_days,
    formatValue: (v) => `${v}일`,
    onClick: goToSearch,
  });
  document.getElementById("costSubtitle").textContent = data.period_active
    ? "(선택 기간 교체 이력 금액 합산 기준 정렬 · 구매금액도 함께 표시)"
    : "(교체 이력 금액 합산 기준 정렬 · 구매금액도 함께 표시)";
  document.getElementById("usageSubtitle").textContent = data.period_active ? "(선택 기간 교체 이력 기준)" : "(전체 교체 이력 기준)";
  document.getElementById("clearPeriodBtn").classList.toggle("d-none", !data.period_active);
  updateExportLinks();
}

function renderPartSpecPanel(elId, rows, metricText) {
  const el = document.getElementById(elId);
  if (rows.length === 0) {
    el.innerHTML = `<p class="text-muted text-center py-4 mb-0">데이터가 없습니다.</p>`;
    return;
  }
  el.innerHTML = rows
    .map(
      (r, i) => `
    <div class="stats-row" data-row-idx="${i}">
      <span class="stats-rank">${i + 1}</span>
      <div class="alert-main">
        <div class="alert-title">
          <strong>${escapeHtml(r.name)}</strong>
          ${r.spec ? `<span class="alert-sep">›</span> ${escapeHtml(r.spec)}` : ""}
        </div>
        <div class="alert-meta">${metricText(r)} &middot; ${r.instance_count}곳에 등록됨</div>
      </div>
      <i class="bi bi-chevron-right alert-chevron"></i>
    </div>`
    )
    .join("");
  rows.forEach((r, i) => {
    const row = el.querySelector(`[data-row-idx="${i}"]`);
    row.addEventListener("click", () => {
      window.location.href = `/search?q=${encodeURIComponent(r.name)}`;
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

function updateExportLinks() {
  const qs = currentPeriodParams().toString();
  document.getElementById("exportCsvBtn").href = `/api/stats/export.csv${qs ? "?" + qs : ""}`;
  document.getElementById("exportPptxBtn").href = `/api/stats/export.pptx${qs ? "?" + qs : ""}`;
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

  document.getElementById("statsStartDate").addEventListener("change", loadStats);
  document.getElementById("statsEndDate").addEventListener("change", loadStats);
  document.getElementById("clearPeriodBtn").addEventListener("click", () => {
    document.getElementById("statsStartDate").value = "";
    document.getElementById("statsEndDate").value = "";
    loadStats();
  });
});
