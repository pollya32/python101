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

// ── 리치 붙여넣기 (엑셀 표 서식 유지) — 다른 페이지와 동일한 화이트리스트 정제 ──
const RICH_ALLOWED_TAGS = {
  TABLE: [], THEAD: [], TBODY: [], TFOOT: [], TR: [], COL: [], COLGROUP: [], CAPTION: [],
  TH: ["colspan", "rowspan"], TD: ["colspan", "rowspan"],
  B: [], STRONG: [], I: [], EM: [], U: [], BR: [], P: [], DIV: [], SPAN: [],
  UL: [], OL: [], LI: [], A: ["href"],
};
const RICH_STRIP_TAGS = new Set([
  "SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "LINK", "META", "SVG",
  "FORM", "IMG", "INPUT", "BUTTON", "TEXTAREA", "SELECT", "VIDEO", "AUDIO", "SOURCE",
]);

function sanitizeRichNode(node) {
  Array.from(node.childNodes).forEach((child) => {
    if (child.nodeType === Node.COMMENT_NODE) {
      child.remove();
      return;
    }
    if (child.nodeType !== Node.ELEMENT_NODE) return;
    const tag = child.tagName;
    if (RICH_STRIP_TAGS.has(tag)) {
      child.remove();
      return;
    }
    const allowed = RICH_ALLOWED_TAGS[tag];
    if (!allowed) {
      sanitizeRichNode(child);
      while (child.firstChild) node.insertBefore(child.firstChild, child);
      child.remove();
      return;
    }
    Array.from(child.attributes).forEach((attr) => {
      if (!allowed.includes(attr.name)) child.removeAttribute(attr.name);
    });
    if (tag === "A") {
      const href = child.getAttribute("href") || "";
      if (!/^https?:\/\//i.test(href)) {
        child.removeAttribute("href");
      } else {
        child.setAttribute("target", "_blank");
        child.setAttribute("rel", "noopener noreferrer");
      }
    }
    sanitizeRichNode(child);
  });
}

function sanitizeRichHtml(rawHtml) {
  const container = document.createElement("div");
  container.innerHTML = rawHtml || "";
  sanitizeRichNode(container);
  return container.innerHTML;
}

function attachRichPasteHandler(el) {
  if (!el || el.dataset.richPasteBound) return;
  el.dataset.richPasteBound = "1";
  el.addEventListener("paste", (e) => {
    e.preventDefault();
    const html = e.clipboardData.getData("text/html");
    const text = e.clipboardData.getData("text/plain");
    if (html) {
      document.execCommand("insertHTML", false, sanitizeRichHtml(html));
    } else if (text) {
      document.execCommand("insertText", false, text);
    }
  });
}

// ── 진행 현황 집계: 첫 행(Chamber 제목)과 첫 열(호기 이름)을 제외한 칸에서
//    내용이 있으면 완료, 빈칸이면 미진행으로 센다 ─────────────────────────
function computeProgress(dataHtml) {
  const container = document.createElement("div");
  container.innerHTML = dataHtml || "";
  const table = container.querySelector("table");
  if (!table) return { done: 0, pending: 0, total: 0 };
  const rows = Array.from(table.querySelectorAll("tr"));
  let done = 0;
  let pending = 0;
  rows.slice(1).forEach((tr) => {
    Array.from(tr.children).slice(1).forEach((cell) => {
      if (cell.textContent.trim()) done++;
      else pending++;
    });
  });
  return { done, pending, total: done + pending };
}

// 반원형 게이지 (정적 SVG — 애니메이션 없음)
function gaugeHtml(pct) {
  const ARC_LEN = 157.08; // 반지름 50 반원 둘레
  const dash = (Math.max(0, Math.min(100, pct)) / 100) * ARC_LEN;
  const color = pct >= 100 ? "#22c55e" : pct >= 50 ? "#4338ca" : "#f59e0b";
  return `
    <svg viewBox="0 0 120 70" class="gauge-svg" role="img" aria-label="진행률 ${pct}%">
      <path d="M 10 62 A 50 50 0 0 1 110 62" fill="none" class="gauge-track" stroke-width="11" stroke-linecap="round"/>
      <path d="M 10 62 A 50 50 0 0 1 110 62" fill="none" stroke="${color}" stroke-width="11" stroke-linecap="round"
        stroke-dasharray="${dash.toFixed(2)} ${ARC_LEN.toFixed(2)}"/>
      <text x="60" y="56" text-anchor="middle" class="gauge-pct">${pct}%</text>
    </svg>`;
}

let allItems = [];
let rolloutEditModal;

async function loadItems() {
  allItems = await fetchJson("/api/rollout");
  renderItems();
}

function renderItems() {
  const list = document.getElementById("rolloutList");
  const empty = document.getElementById("rolloutEmpty");
  if (allItems.length === 0) {
    list.innerHTML = "";
    empty.classList.remove("d-none");
    return;
  }
  empty.classList.add("d-none");
  list.innerHTML = allItems
    .map((item) => {
      const p = computeProgress(item.data_html);
      const pct = p.total > 0 ? Math.round((p.done / p.total) * 100) : 0;
      return `
      <div class="rollout-card" data-item-id="${item.id}">
        <div class="rollout-card-head">
          <div class="rollout-title">${escapeHtml(item.title)}</div>
          <div class="d-flex align-items-center gap-1">
            <button class="btn btn-sm btn-outline-secondary toggle-data-btn">
              <i class="bi bi-eye"></i> 데이터 보기
            </button>
            <button class="btn btn-sm btn-outline-secondary edit-item-btn" title="편집"><i class="bi bi-pencil"></i></button>
            <button class="btn btn-sm btn-outline-secondary delete-item-btn" title="삭제"><i class="bi bi-trash3"></i></button>
          </div>
        </div>
        <div class="rollout-card-body">
          ${gaugeHtml(pct)}
          <div class="rollout-counts">
            <span class="rollout-count-done"><i class="bi bi-check-circle-fill"></i> 완료 ${p.done}건</span>
            <span class="rollout-count-pending"><i class="bi bi-circle"></i> 미진행 ${p.pending}건</span>
            <span class="text-muted small">전체 ${p.total}건</span>
          </div>
        </div>
        <div class="rollout-data d-none">${item.data_html || '<p class="text-muted small mb-0">붙여넣은 데이터가 없습니다.</p>'}</div>
        <div class="rollout-updated text-muted">최종 수정: ${item.updated_at || item.created_at || ""}</div>
      </div>`;
    })
    .join("");

  allItems.forEach((item) => {
    const card = list.querySelector(`[data-item-id="${item.id}"]`);
    const dataDiv = card.querySelector(".rollout-data");
    const toggleBtn = card.querySelector(".toggle-data-btn");
    toggleBtn.addEventListener("click", () => {
      const hidden = dataDiv.classList.toggle("d-none");
      toggleBtn.innerHTML = hidden
        ? '<i class="bi bi-eye"></i> 데이터 보기'
        : '<i class="bi bi-eye-slash"></i> 데이터 숨기기';
    });
    card.querySelector(".edit-item-btn").addEventListener("click", () => openEditModal(item));
    card.querySelector(".delete-item-btn").addEventListener("click", async () => {
      if (!confirm(`"${item.title}" 항목을 삭제할까요?`)) return;
      try {
        await fetchJson(`/api/rollout/${item.id}`, { method: "DELETE" });
        loadItems();
      } catch (err) {
        alert(err.message);
      }
    });
  });
}

function openEditModal(item) {
  document.getElementById("rolloutEditTitle").textContent = item ? "횡전개 항목 편집" : "횡전개 항목 추가";
  document.getElementById("rolloutEditId").value = item ? item.id : "";
  document.getElementById("rolloutEditName").value = item ? item.title : "";
  document.getElementById("rolloutEditData").innerHTML = item ? item.data_html || "" : "";
  rolloutEditModal.show();
}

document.addEventListener("DOMContentLoaded", () => {
  rolloutEditModal = new bootstrap.Modal(document.getElementById("rolloutEditModal"));
  tick();
  setInterval(tick, 1000);
  loadItems();

  attachRichPasteHandler(document.getElementById("rolloutEditData"));
  document.getElementById("addRolloutBtn").addEventListener("click", () => openEditModal(null));

  document.getElementById("rolloutEditForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = document.getElementById("rolloutEditId").value;
    const payload = {
      title: document.getElementById("rolloutEditName").value.trim(),
      data_html: sanitizeRichHtml(document.getElementById("rolloutEditData").innerHTML),
    };
    try {
      if (id) {
        await fetchJson(`/api/rollout/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } else {
        await fetchJson("/api/rollout", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      rolloutEditModal.hide();
      loadItems();
    } catch (err) {
      alert(err.message);
    }
  });
});
