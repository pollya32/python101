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

const ACTION_LABEL = {
  create: "생성",
  update: "수정",
  delete: "삭제",
  restore: "복원",
  permanent_delete: "영구 삭제",
  replace: "교체 기록",
  backup: "백업",
};

const TARGET_LABEL = {
  equipment: "설비",
  unit: "유닛",
  part: "부품",
  backup: "백업",
};

const ACTION_BADGE = {
  create: "bulk-status-registered",
  update: "text-primary",
  delete: "text-danger",
  restore: "text-success",
  permanent_delete: "text-danger fw-bold",
  replace: "text-primary",
  backup: "text-muted",
};

async function loadActivityLog() {
  const rows = await fetchJson("/api/activity-log");
  const tbody = document.getElementById("activityLogBody");
  const empty = document.getElementById("activityLogEmpty");
  if (rows.length === 0) {
    tbody.innerHTML = "";
    empty.classList.remove("d-none");
    return;
  }
  empty.classList.add("d-none");
  tbody.innerHTML = rows
    .map(
      (r) => `
    <tr>
      <td class="text-muted small">${r.created_at}</td>
      <td>${escapeHtml(r.actor_name || "익명")}</td>
      <td class="${ACTION_BADGE[r.action] || ""}">${ACTION_LABEL[r.action] || r.action}</td>
      <td>${TARGET_LABEL[r.target_type] || r.target_type || "-"}${r.target_name ? " - " + escapeHtml(r.target_name) : ""}</td>
      <td class="text-muted small">${escapeHtml(r.detail || "")}</td>
    </tr>`
    )
    .join("");
}

document.addEventListener("DOMContentLoaded", () => {
  tick();
  setInterval(tick, 1000);
  loadActivityLog();
});
