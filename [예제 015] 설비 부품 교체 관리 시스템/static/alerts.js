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

document.addEventListener("DOMContentLoaded", () => {
  tick();
  setInterval(tick, 1000);
  loadAlerts();
});
