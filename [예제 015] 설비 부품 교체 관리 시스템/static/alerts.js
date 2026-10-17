function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

async function loadAlerts() {
  const res = await fetch("/api/alerts");
  if (res.status === 401) {
    window.location.href = "/login";
    return;
  }
  const parts = await res.json();
  renderAlerts(parts);
}

function renderAlerts(parts) {
  const list = document.getElementById("alertsList");
  if (parts.length === 0) {
    list.innerHTML = `<p class="text-muted text-center py-5">교체가 필요하거나 임박한 부품이 없습니다.</p>`;
    return;
  }
  list.innerHTML = parts.map(alertRowHtml).join("");
  parts.forEach((p) => {
    const row = list.querySelector(`[data-row-id="${p.id}"]`);
    row.addEventListener("click", () => {
      window.location.href = `/unit/${p.unit_id}`;
    });
  });
}

function alertRowHtml(p) {
  const isOverdue = p.status === "overdue";
  const badge = isOverdue ? "badge-overdue" : "badge-soon";
  const label = isOverdue ? "교체 필요" : "교체 임박";
  const daysText = isOverdue ? `${Math.abs(p.days_left)}일 초과` : `${p.days_left}일 남음`;
  const lastText = p.last_replaced_date ? `최근 교체 ${p.last_replaced_date}` : "교체 이력 없음";
  return `
    <div class="alert-row" data-row-id="${p.id}">
      <span class="badge ${badge} alert-badge">${label}</span>
      <div class="alert-main">
        <div class="alert-title">
          <span>${p.equipment_icon}</span> ${escapeHtml(p.equipment_name)}
          <span class="alert-sep">›</span> ${escapeHtml(p.unit_name)}
          <span class="alert-sep">›</span> <strong>${p.icon} ${escapeHtml(p.name)}</strong>
        </div>
        <div class="alert-meta">교체 주기 ${p.cycle_days}일 &middot; ${lastText} &middot; ${daysText}</div>
      </div>
      <i class="bi bi-chevron-right alert-chevron"></i>
    </div>`;
}

const WEEKDAY_LABELS = ["일", "월", "화", "수", "목", "금", "토"];
const STATUS_ORDER = { overdue: 0, soon: 1, ok: 2, unknown: 3 };

let calDayModal;
const today = new Date();
let calYear = today.getFullYear();
let calMonth = today.getMonth() + 1; // 1~12
const maxCalYear = new Date(today.getFullYear(), today.getMonth() + 6, 1).getFullYear();
const maxCalMonth = new Date(today.getFullYear(), today.getMonth() + 6, 1).getMonth() + 1;

function isAtMaxMonth() {
  return calYear === maxCalYear && calMonth === maxCalMonth;
}

async function loadCalendar() {
  document.getElementById("calMonthLabel").textContent = `${calYear}년 ${calMonth}월`;
  document.getElementById("calNextBtn").disabled = isAtMaxMonth();

  const res = await fetch(`/api/calendar?year=${calYear}&month=${calMonth}`);
  if (res.status === 401) {
    window.location.href = "/login";
    return;
  }
  const payload = await res.json();
  renderCalendar(payload.days || {});
}

function renderCalendar(days) {
  const weekdaysEl = document.getElementById("calWeekdays");
  weekdaysEl.innerHTML = WEEKDAY_LABELS.map((w) => `<div>${w}</div>`).join("");

  const firstOfMonth = new Date(calYear, calMonth - 1, 1);
  const startWeekday = firstOfMonth.getDay(); // 0=일요일
  const daysInMonth = new Date(calYear, calMonth, 0).getDate();
  const todayStr = new Date().toISOString().slice(0, 10);

  const cells = [];
  for (let i = 0; i < startWeekday; i++) {
    cells.push(`<div class="calendar-cell calendar-cell-empty"></div>`);
  }
  for (let day = 1; day <= daysInMonth; day++) {
    const dateStr = `${calYear}-${String(calMonth).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const items = days[dateStr] || [];
    const isToday = dateStr === todayStr;
    if (items.length === 0) {
      cells.push(
        `<div class="calendar-cell${isToday ? " calendar-cell-today" : ""}">` +
          `<span class="calendar-cell-date">${day}</span></div>`
      );
      continue;
    }
    const worstStatus = items.reduce(
      (worst, p) => (STATUS_ORDER[p.status] < STATUS_ORDER[worst] ? p.status : worst),
      "unknown"
    );
    const statusCounts = {};
    items.forEach((p) => { statusCounts[p.status] = (statusCounts[p.status] || 0) + 1; });
    const dots = Object.keys(statusCounts)
      .sort((a, b) => STATUS_ORDER[a] - STATUS_ORDER[b])
      .map((s) => `<span class="dot dot-${s}"></span>`)
      .join("");
    cells.push(
      `<div class="calendar-cell calendar-has-items${isToday ? " calendar-cell-today" : ""}" data-date="${dateStr}" data-worst="${worstStatus}">` +
        `<span class="calendar-cell-date">${day}</span>` +
        `<span class="calendar-cell-dots">${dots}</span>` +
        `<span class="calendar-cell-count">${items.length}건</span>` +
      `</div>`
    );
  }

  document.getElementById("calGrid").innerHTML = cells.join("");
  document.querySelectorAll(".calendar-has-items").forEach((cell) => {
    cell.addEventListener("click", () => openCalDayModal(cell.dataset.date, days[cell.dataset.date]));
  });
}

function openCalDayModal(dateStr, items) {
  document.getElementById("calDayModalTitle").textContent = `${dateStr} 교체 예정 (${items.length}건)`;
  const list = document.getElementById("calDayModalList");
  list.innerHTML = items.map(alertRowHtml).join("");
  items.forEach((p) => {
    const row = list.querySelector(`[data-row-id="${p.id}"]`);
    row.addEventListener("click", () => {
      window.location.href = `/unit/${p.unit_id}`;
    });
  });
  calDayModal.show();
}

function setView(view) {
  const listBtn = document.getElementById("viewListBtn");
  const calBtn = document.getElementById("viewCalendarBtn");
  const listEl = document.getElementById("alertsList");
  const calEl = document.getElementById("calendarView");
  if (view === "calendar") {
    listEl.classList.add("d-none");
    calEl.classList.remove("d-none");
    listBtn.classList.remove("btn-primary");
    listBtn.classList.add("btn-outline-primary");
    calBtn.classList.remove("btn-outline-primary");
    calBtn.classList.add("btn-primary");
    loadCalendar();
  } else {
    calEl.classList.add("d-none");
    listEl.classList.remove("d-none");
    calBtn.classList.remove("btn-primary");
    calBtn.classList.add("btn-outline-primary");
    listBtn.classList.remove("btn-outline-primary");
    listBtn.classList.add("btn-primary");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  tick();
  setInterval(tick, 1000);
  loadAlerts();

  calDayModal = new bootstrap.Modal(document.getElementById("calDayModal"));

  document.getElementById("viewListBtn").addEventListener("click", () => setView("list"));
  document.getElementById("viewCalendarBtn").addEventListener("click", () => setView("calendar"));
  document.getElementById("calPrevBtn").addEventListener("click", () => {
    calMonth -= 1;
    if (calMonth < 1) { calMonth = 12; calYear -= 1; }
    loadCalendar();
  });
  document.getElementById("calNextBtn").addEventListener("click", () => {
    if (isAtMaxMonth()) return;
    calMonth += 1;
    if (calMonth > 12) { calMonth = 1; calYear += 1; }
    loadCalendar();
  });
});
