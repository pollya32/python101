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

// ── 표를 병합 셀(rowspan/colspan)까지 반영한 2차원 격자로 펼친다.
//    엑셀에서 호기 칸이 세로 병합된 채 복사돼도 열 위치가 어긋나지 않게 하기 위함 ──
function tableToGrid(table) {
  const grid = [];
  Array.from(table.querySelectorAll("tr")).forEach((tr, r) => {
    grid[r] = grid[r] || [];
    let c = 0;
    Array.from(tr.children).forEach((cell) => {
      while (grid[r][c] !== undefined) c++;
      const colspan = parseInt(cell.getAttribute("colspan") || "1", 10) || 1;
      const rowspan = parseInt(cell.getAttribute("rowspan") || "1", 10) || 1;
      const text = cell.textContent.trim();
      for (let dr = 0; dr < rowspan; dr++) {
        for (let dc = 0; dc < colspan; dc++) {
          grid[r + dr] = grid[r + dr] || [];
          grid[r + dr][c + dc] = text;
        }
      }
      c += colspan;
    });
  });
  return grid;
}

// ── 진행 현황 집계 (열 형식 기준):
//    1행 = 제목 행(1~2열 횡전개 제목, 3열 '진행 날짜' 제목)이므로 건너뛰고,
//    2행부터 1열 = 호기, 2열 = CH 이름, 3열 = 진행 날짜로 본다.
//    CH 이름(2열)이 있는 행만 집계 대상이며, 그 행의 진행 날짜(3열)에
//    내용이 있으면 완료, 빈칸이면 미진행으로 센다 ─────────────────────────
function computeProgress(dataHtml) {
  const container = document.createElement("div");
  container.innerHTML = dataHtml || "";
  const table = container.querySelector("table");
  if (!table) return { done: 0, pending: 0, total: 0 };
  const grid = tableToGrid(table);
  let done = 0;
  let pending = 0;
  for (let r = 1; r < grid.length; r++) {
    const row = grid[r] || [];
    const chName = (row[1] || "").trim();
    if (!chName) continue;
    if ((row[2] || "").trim()) done++;
    else pending++;
  }
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
let rolloutDataModal;
let currentDataItemId = null;

async function loadItems() {
  allItems = await fetchJson("/api/rollout");
  renderItems();
}

// ── 데이터 팝업: 게이지 클릭 시 열리며, 셀 직접 수정·행 선택 삭제·누적 붙여넣기를 지원 ──
function renderDataModalTable(item) {
  const wrap = document.getElementById("rolloutDataTableWrap");
  const container = document.createElement("div");
  container.innerHTML = item.data_html || "";
  const table = container.querySelector("table");
  if (!table) {
    wrap.innerHTML = '<p class="text-muted small mb-0">붙여넣은 데이터가 없습니다. 아래에서 행을 추가해보세요.</p>';
    return;
  }
  Array.from(table.querySelectorAll("tr")).forEach((tr, i) => {
    const cell = document.createElement(i === 0 ? "th" : "td");
    cell.className = "row-select-cell";
    if (i > 0) {
      cell.innerHTML = `<input type="checkbox" class="form-check-input row-select" data-row-index="${i}">`;
    }
    tr.insertBefore(cell, tr.firstChild);
  });
  // 셀을 클릭해 날짜를 바로 기입/수정할 수 있게 한다 (체크박스 칸 제외)
  table.querySelectorAll("td, th").forEach((cell) => {
    if (!cell.classList.contains("row-select-cell")) cell.setAttribute("contenteditable", "true");
  });
  wrap.innerHTML = "";
  wrap.appendChild(table);
}

function openDataModal(item) {
  currentDataItemId = item.id;
  document.getElementById("rolloutDataTitle").textContent = `${item.title} — 데이터`;
  document.getElementById("rolloutAppendData").innerHTML = "";
  renderDataModalTable(item);
  rolloutDataModal.show();
}

// ── 팝업 표 직접 편집: 날짜를 기입하면 잠시 후 자동 저장되고 게이지에 즉시 반영 ──
let dataEditSaveTimer = null;
let dataEditDirty = false;

function serializeDataModalTable() {
  const table = document.querySelector("#rolloutDataTableWrap table");
  if (!table) return "";
  const clone = table.cloneNode(true);
  clone.querySelectorAll(".row-select-cell").forEach((c) => c.remove());
  clone.querySelectorAll("[contenteditable]").forEach((c) => c.removeAttribute("contenteditable"));
  const div = document.createElement("div");
  div.appendChild(clone);
  return sanitizeRichHtml(div.innerHTML);
}

async function flushDataEdit() {
  if (!dataEditDirty || currentDataItemId == null) return;
  dataEditDirty = false;
  clearTimeout(dataEditSaveTimer);
  try {
    await saveDataHtml(currentDataItemId, serializeDataModalTable());
  } catch (err) {
    alert(err.message);
  }
}

async function saveDataHtml(itemId, dataHtml) {
  const updated = await fetchJson(`/api/rollout/${itemId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data_html: dataHtml }),
  });
  const idx = allItems.findIndex((x) => x.id === itemId);
  if (idx >= 0) allItems[idx] = updated;
  renderItems();
  return updated;
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
    const gaugeArea = card.querySelector(".rollout-card-body");
    gaugeArea.title = "클릭하면 데이터 팝업이 열립니다";
    gaugeArea.addEventListener("click", () => openDataModal(item));
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
  rolloutDataModal = new bootstrap.Modal(document.getElementById("rolloutDataModal"));
  tick();
  setInterval(tick, 1000);
  loadItems();

  attachRichPasteHandler(document.getElementById("rolloutEditData"));
  attachRichPasteHandler(document.getElementById("rolloutAppendData"));
  document.getElementById("addRolloutBtn").addEventListener("click", () => openEditModal(null));

  // 팝업 표의 셀에 날짜를 기입하면 잠시 후 자동 저장 → 게이지 카운트 즉시 반영
  document.getElementById("rolloutDataTableWrap").addEventListener("input", (e) => {
    if (e.target && e.target.classList && e.target.classList.contains("row-select")) return;
    dataEditDirty = true;
    clearTimeout(dataEditSaveTimer);
    dataEditSaveTimer = setTimeout(flushDataEdit, 600);
  });
  // 저장 전에 팝업을 닫아도 수정 내용이 유실되지 않도록 닫힐 때 즉시 저장
  document.getElementById("rolloutDataModal").addEventListener("hide.bs.modal", () => {
    flushDataEdit();
  });

  document.getElementById("deleteRowsBtn").addEventListener("click", async () => {
    await flushDataEdit();
    const item = allItems.find((x) => x.id === currentDataItemId);
    if (!item) return;
    const checked = Array.from(
      document.querySelectorAll("#rolloutDataTableWrap .row-select:checked")
    ).map((cb) => parseInt(cb.dataset.rowIndex, 10));
    if (checked.length === 0) {
      alert("삭제할 행을 먼저 선택하세요.");
      return;
    }
    if (!confirm(`선택한 ${checked.length}개 행을 삭제할까요?`)) return;
    const container = document.createElement("div");
    container.innerHTML = item.data_html || "";
    const table = container.querySelector("table");
    if (!table) return;
    const rows = Array.from(table.querySelectorAll("tr"));
    checked.sort((a, b) => b - a).forEach((i) => {
      if (rows[i]) rows[i].remove();
    });
    if (!table.querySelector("tr")) table.remove();
    try {
      const updated = await saveDataHtml(item.id, container.innerHTML);
      renderDataModalTable(updated);
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("appendRowsBtn").addEventListener("click", async () => {
    await flushDataEdit();
    const item = allItems.find((x) => x.id === currentDataItemId);
    if (!item) return;
    const pastedContainer = document.createElement("div");
    pastedContainer.innerHTML = sanitizeRichHtml(document.getElementById("rolloutAppendData").innerHTML);
    const pastedTable = pastedContainer.querySelector("table");
    if (!pastedTable) {
      alert("추가할 표 데이터를 먼저 붙여넣으세요.");
      return;
    }
    const container = document.createElement("div");
    container.innerHTML = item.data_html || "";
    const table = container.querySelector("table");
    if (!table) {
      container.innerHTML = "";
      container.appendChild(pastedTable);
    } else {
      const target = table.querySelector("tbody") || table;
      Array.from(pastedTable.querySelectorAll("tr")).forEach((tr) => target.appendChild(tr));
    }
    try {
      const updated = await saveDataHtml(item.id, container.innerHTML);
      document.getElementById("rolloutAppendData").innerHTML = "";
      renderDataModalTable(updated);
    } catch (err) {
      alert(err.message);
    }
  });

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
