let editMode = false;
let currentPartId = null;
let partDetailModal, replaceModal, historyModal, partEditModal, drawingModal;
let currentParts = [];
let currentEquipmentId = null;
let currentPartDrawingData = null;
const MASTER_EQUIPMENT_ID = 1;
const MAX_DRAWING_BYTES = 5 * 1024 * 1024;

const statusColor = { ok: "#22c55e", soon: "#f59e0b", overdue: "#ef4444", unknown: "#9ca3af" };
const statusBadge = { ok: "badge-ok", soon: "badge-soon", overdue: "badge-overdue", unknown: "badge-unknown" };
const statusLabel = { ok: "정상", soon: "교체 임박", overdue: "교체 필요", unknown: "미기록" };

const ICON_CHOICES = [
  "🔩", "⚙️", "🔧", "🛠️", "🪛", "🔨", "📦", "🖥️",
  "🖨️", "💻", "📡", "🎛️", "🚨", "💡", "🔌", "⚡",
  "🔋", "🌡️", "💧", "🧪", "🧯", "🧰", "🏭", "⚗️",
  "🌀", "🗜️", "🧲", "📊", "🛞", "🚿", "🔥", "❄️",
];

function renderIconPicker(containerId, inputId, current) {
  const container = document.getElementById(containerId);
  const input = document.getElementById(inputId);
  container.innerHTML = ICON_CHOICES.map(
    (ic) => `<button type="button" class="icon-choice ${ic === current ? "selected" : ""}" data-icon="${ic}">${ic}</button>`
  ).join("");
  container.classList.add("d-none");
  container.querySelectorAll(".icon-choice").forEach((btn) => {
    btn.addEventListener("click", () => {
      input.value = btn.dataset.icon;
      container.querySelectorAll(".icon-choice").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
      container.classList.add("d-none");
    });
  });
  if (!container.dataset.toggleBound) {
    container.dataset.toggleBound = "1";
    input.addEventListener("click", () => {
      container.classList.toggle("d-none");
    });
    document.addEventListener("click", (e) => {
      if (!container.contains(e.target) && e.target !== input) {
        container.classList.add("d-none");
      }
    });
  }
}

function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
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

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

// ── 리치 메모/노트: 엑셀 표 붙여넣기 시 서식(표 구조) 유지 ─────────────────
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

function linkifyRichHtml(rawHtml) {
  const container = document.createElement("div");
  container.innerHTML = rawHtml || "";
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const targets = [];
  let node;
  while ((node = walker.nextNode())) {
    if (node.parentElement && node.parentElement.closest("a")) continue;
    if (/https?:\/\//.test(node.nodeValue)) targets.push(node);
  }
  targets.forEach((textNode) => {
    const frag = document.createDocumentFragment();
    textNode.nodeValue.split(/(https?:\/\/[^\s<]+)/g).forEach((part) => {
      if (/^https?:\/\//.test(part)) {
        const a = document.createElement("a");
        a.href = part;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = part;
        frag.appendChild(a);
      } else if (part) {
        frag.appendChild(document.createTextNode(part));
      }
    });
    textNode.parentNode.replaceChild(frag, textNode);
  });
  return container.innerHTML;
}

function isRichContentEmpty(html) {
  const container = document.createElement("div");
  container.innerHTML = html || "";
  return container.textContent.trim() === "";
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

// ── 표 편집 툴바: 붙여넣은 표 안을 클릭하면 행/열 추가·삭제 버튼 표시 ───────
let tableEditToolbar = null;
let tableEditActiveCell = null;

function ensureTableEditToolbar() {
  if (tableEditToolbar) return tableEditToolbar;
  const bar = document.createElement("div");
  bar.className = "table-edit-toolbar d-none";
  bar.innerHTML = `
    <button type="button" data-action="add-row" title="아래에 행 추가"><i class="bi bi-plus-lg"></i>행</button>
    <button type="button" data-action="del-row" title="이 행 삭제"><i class="bi bi-dash-lg"></i>행</button>
    <button type="button" data-action="add-col" title="오른쪽에 열 추가"><i class="bi bi-plus-lg"></i>열</button>
    <button type="button" data-action="del-col" title="이 열 삭제"><i class="bi bi-dash-lg"></i>열</button>
  `;
  document.body.appendChild(bar);
  bar.addEventListener("mousedown", (e) => e.preventDefault());
  bar.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn || !tableEditActiveCell || !document.body.contains(tableEditActiveCell)) return;
    const actions = {
      "add-row": insertTableRow, "del-row": deleteTableRow,
      "add-col": insertTableColumn, "del-col": deleteTableColumn,
    };
    actions[btn.dataset.action](tableEditActiveCell);
  });
  tableEditToolbar = bar;
  return bar;
}

function showTableEditToolbar(cell) {
  const bar = ensureTableEditToolbar();
  tableEditActiveCell = cell;
  const rect = cell.getBoundingClientRect();
  bar.classList.remove("d-none");
  bar.style.top = `${Math.max(rect.top - bar.offsetHeight - 4, 4)}px`;
  bar.style.left = `${rect.left}px`;
}

function hideTableEditToolbar() {
  if (tableEditToolbar) tableEditToolbar.classList.add("d-none");
  tableEditActiveCell = null;
}

function cellColumnIndex(cell) {
  return Array.from(cell.parentElement.children).indexOf(cell);
}

function insertTableRow(cell) {
  const tr = cell.closest("tr");
  if (!tr) return;
  const newRow = document.createElement("tr");
  Array.from(tr.children).forEach(() => newRow.appendChild(document.createElement("td")));
  tr.after(newRow);
}

function deleteTableRow(cell) {
  const tr = cell.closest("tr");
  const table = cell.closest("table");
  if (!tr || !table) return;
  tr.remove();
  if (!table.querySelector("tr")) table.remove();
  hideTableEditToolbar();
}

function insertTableColumn(cell) {
  const table = cell.closest("table");
  if (!table) return;
  const colIndex = cellColumnIndex(cell);
  table.querySelectorAll("tr").forEach((row) => {
    const rowCell = row.children[colIndex];
    const newCell = document.createElement(rowCell && rowCell.tagName === "TH" ? "th" : "td");
    if (rowCell) rowCell.after(newCell);
    else row.appendChild(newCell);
  });
}

function deleteTableColumn(cell) {
  const table = cell.closest("table");
  if (!table) return;
  const colIndex = cellColumnIndex(cell);
  table.querySelectorAll("tr").forEach((row) => {
    const rowCell = row.children[colIndex];
    if (rowCell) rowCell.remove();
  });
  if (!table.querySelector("td, th")) table.remove();
  hideTableEditToolbar();
}

function attachTableEditToolbar(el) {
  if (!el || el.dataset.tableToolbarBound) return;
  el.dataset.tableToolbarBound = "1";
  const check = () => {
    const sel = window.getSelection();
    let node = sel.rangeCount ? sel.anchorNode : null;
    if (node && node.nodeType === Node.TEXT_NODE) node = node.parentElement;
    const cell = node && node.closest ? node.closest("td, th") : null;
    if (cell && el.contains(cell)) {
      showTableEditToolbar(cell);
    } else {
      hideTableEditToolbar();
    }
  };
  el.addEventListener("keyup", check);
  el.addEventListener("mouseup", check);
  el.addEventListener("focus", check);
  el.addEventListener("blur", () => {
    setTimeout(() => {
      if (!tableEditToolbar || !tableEditToolbar.matches(":hover")) hideTableEditToolbar();
    }, 150);
  });
}

let lastNotesContent = "";

async function loadNotes() {
  const data = await fetchJson(`/api/units/${UNIT_ID}/notes`);
  lastNotesContent = data.content || "";
  renderNotesView(lastNotesContent);
  document.getElementById("notesEdit").innerHTML = lastNotesContent;
  document.getElementById("notesSavedAt").textContent = data.updated_at
    ? `최종 수정: ${data.updated_at}`
    : "";
}

function renderNotesView(content) {
  const view = document.getElementById("notesView");
  if (isRichContentEmpty(content)) {
    view.innerHTML = "";
    view.classList.add("is-empty");
  } else {
    view.classList.remove("is-empty");
    view.innerHTML = linkifyRichHtml(content);
  }
}

function setNotesEditing(editing) {
  document.getElementById("notesView").classList.toggle("d-none", editing);
  document.getElementById("notesEdit").classList.toggle("d-none", !editing);
  document.getElementById("editNotesBtn").classList.toggle("d-none", editing);
  document.getElementById("saveNotesBtn").classList.toggle("d-none", !editing);
  document.getElementById("cancelNotesBtn").classList.toggle("d-none", !editing);
  if (editing) document.getElementById("notesEdit").focus();
}

async function loadUnitHeader() {
  try {
    const unit = await fetchJson(`/api/units/${UNIT_ID}`);
    currentEquipmentId = unit.equipment_id;
    document.getElementById("unitPageTitle").textContent = unit.name;
    document.getElementById("unitIcon").textContent = unit.icon;
    document.getElementById("unitName").textContent = unit.name;
    document.getElementById("unitShape").style.setProperty("--shape-color", unit.color);
    document.getElementById("backToEquipmentBtn").href = `/equipment/${unit.equipment_id}`;
    const isMaster = unit.equipment_id === MASTER_EQUIPMENT_ID;
    document.getElementById("masterHint").classList.toggle("d-none", !isMaster);
    document.getElementById("applyPartsBtn").classList.toggle("d-none", !isMaster);
  } catch (err) {
    alert("유닛 정보를 불러올 수 없습니다.");
    window.location.href = "/";
  }
}

async function loadParts() {
  currentParts = await fetchJson(`/api/units/${UNIT_ID}/parts`);
  renderPartsCanvas(currentParts);
}

function renderPartsCanvas(parts) {
  const canvas = document.getElementById("partsCanvas");
  canvas.innerHTML = parts.map(partShapeHtml).join("");
  parts.forEach((p) => {
    const card = canvas.querySelector(`[data-part-id="${p.id}"]`);
    card.style.left = `${p.pos_x}%`;
    card.style.top = `${p.pos_y}%`;
    card.style.width = `${p.width}px`;
    card.style.height = `${p.height}px`;

    card.querySelector(".edit-unit-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      openPartEditModal(p);
    });
    card.querySelector(".delete-unit-btn")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`"${p.name}" 부품을 삭제할까요? 휴지통으로 이동하며, 나중에 복원할 수 있습니다.`)) return;
      await fetchJson(`/api/parts/${p.id}`, { method: "DELETE" });
      loadParts();
    });
    card.querySelector(".copy-part-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      copyPart(p);
    });
    card.querySelector(".unit-drawing-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      currentPartId = p.id;
      openDrawingModal();
    });

    makeDraggable(card, p);
    makeResizable(card, p);
    makeResizableHorizontal(card, p);
  });
}

const MIN_SIZE_W = 90;
const MIN_SIZE_H = 80;
const MAX_SIZE_W = 320;
const MAX_SIZE_H = 260;

function makeResizable(card, part) {
  const handle = card.querySelector(".resize-handle");
  if (!handle) return;

  handle.addEventListener("mousedown", (e) => {
    if (!editMode) return;
    e.preventDefault();
    e.stopPropagation();

    const startX = e.clientX;
    const startY = e.clientY;
    const startWidth = card.offsetWidth;
    const startHeight = card.offsetHeight;

    card.classList.add("dragging");

    function onMove(ev) {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      const w = Math.min(Math.max(startWidth + dx, MIN_SIZE_W), MAX_SIZE_W);
      const h = Math.min(Math.max(startHeight + dy, MIN_SIZE_H), MAX_SIZE_H);
      card.style.width = `${w}px`;
      card.style.height = `${h}px`;
    }

    async function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      card.classList.remove("dragging");
      const width = card.offsetWidth;
      const height = card.offsetHeight;
      part.width = width;
      part.height = height;
      await fetchJson(`/api/parts/${part.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ width, height }),
      });
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

function makeResizableHorizontal(card, part) {
  const handle = card.querySelector(".resize-handle-h");
  if (!handle) return;

  handle.addEventListener("mousedown", (e) => {
    if (!editMode) return;
    e.preventDefault();
    e.stopPropagation();

    const startX = e.clientX;
    const startWidth = card.offsetWidth;

    card.classList.add("dragging");

    function onMove(ev) {
      const dx = ev.clientX - startX;
      const w = Math.min(Math.max(startWidth + dx, MIN_SIZE_W), MAX_SIZE_W);
      card.style.width = `${w}px`;
    }

    async function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      card.classList.remove("dragging");
      const width = card.offsetWidth;
      part.width = width;
      await fetchJson(`/api/parts/${part.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ width }),
      });
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

function makeDraggable(card, part) {
  card.addEventListener("mousedown", (e) => {
    if (!editMode) return;
    if (e.target.closest(".unit-edit-actions")) return;
    if (e.target.closest(".unit-drawing-btn")) return;
    e.preventDefault();

    const canvas = document.getElementById("partsCanvas");
    const rect = canvas.getBoundingClientRect();
    const startX = e.clientX;
    const startY = e.clientY;
    const startLeft = (parseFloat(card.style.left) / 100) * rect.width;
    const startTop = (parseFloat(card.style.top) / 100) * rect.height;
    let moved = false;

    card.classList.add("dragging");

    function onMove(ev) {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved = true;
      const px = Math.min(Math.max(startLeft + dx, rect.width * 0.04), rect.width * 0.96);
      const py = Math.min(Math.max(startTop + dy, rect.height * 0.04), rect.height * 0.96);
      card.style.left = `${(px / rect.width) * 100}%`;
      card.style.top = `${(py / rect.height) * 100}%`;
    }

    async function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      card.classList.remove("dragging");
      if (moved) {
        const pos_x = parseFloat(card.style.left);
        const pos_y = parseFloat(card.style.top);
        part.pos_x = pos_x;
        part.pos_y = pos_y;
        await fetchJson(`/api/parts/${part.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pos_x, pos_y }),
        });
      } else {
        openPartDetailModal(part.id);
      }
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });

  card.addEventListener("click", (e) => {
    if (editMode) return;
    if (e.target.closest(".unit-edit-actions")) return;
    if (e.target.closest(".unit-drawing-btn")) return;
    openPartDetailModal(part.id);
  });
}

function partShapeHtml(p) {
  const color = statusColor[p.status];
  return `
    <div class="unit-card ${editMode ? "edit-mode" : ""}" data-part-id="${p.id}" style="--uc:${color}">
      <span class="unit-status-dot dot-${p.status}"></span>
      <div class="unit-icon-wrap">
        <span class="unit-icon">${p.icon}</span>
        ${p.drawing_data ? `<button type="button" class="unit-drawing-btn" title="도면 보기"><i class="bi bi-image"></i></button>` : ""}
      </div>
      <div class="unit-name">${escapeHtml(p.name)}</div>
      <div class="unit-part-count">${p.label || statusLabel[p.status]}</div>
      <div class="unit-edit-actions">
        <button class="edit-unit-btn" title="편집"><i class="bi bi-pencil"></i></button>
        <button class="copy-part-btn" title="복사"><i class="bi bi-copy"></i></button>
        <button class="delete-unit-btn" title="삭제"><i class="bi bi-trash"></i></button>
      </div>
      <div class="resize-handle" title="크기 조절 (가로+세로)"></div>
      <div class="resize-handle-h" title="가로 크기 조절"></div>
    </div>`;
}

const PART_CLIPBOARD_KEY = "partClipboard";

async function copyPart(part) {
  const clipboard = {
    name: part.name,
    spec: part.spec,
    cycle_days: part.cycle_days,
    cycle_unit: part.cycle_unit,
    cost: part.cost,
    note: part.note,
    memo: part.memo,
    drawing_data: part.drawing_data,
    icon: part.icon,
    width: part.width,
    height: part.height,
    stock_qty: part.stock_qty,
    safety_stock: part.safety_stock,
    supplier: part.supplier,
    supplier_contact: part.supplier_contact,
    lead_time_days: part.lead_time_days,
  };
  try {
    await idbClipboardSet(PART_CLIPBOARD_KEY, clipboard);
  } catch (err) {
    alert("부품 복사에 실패했습니다: " + err.message);
    return;
  }
  await updatePartPasteButton();
  alert(`"${part.name}" 부품을 복사했습니다.\n"붙여넣기" 버튼으로 동일한 부품을 만들 수 있습니다.`);
}

async function getPartClipboard() {
  try {
    return await idbClipboardGet(PART_CLIPBOARD_KEY);
  } catch {
    return null;
  }
}

async function updatePartPasteButton() {
  const clipboard = await getPartClipboard();
  const btn = document.getElementById("pastePartBtn");
  btn.classList.toggle("d-none", !editMode || !clipboard);
  if (clipboard) btn.title = `"${clipboard.name}" 붙여넣기`;
}

async function pastePart() {
  const clipboard = await getPartClipboard();
  if (!clipboard) {
    alert("복사된 부품이 없습니다. 먼저 부품의 복사 아이콘을 눌러주세요.");
    return;
  }
  await fetchJson(`/api/units/${UNIT_ID}/parts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: `${clipboard.name} 복사본`,
      spec: clipboard.spec,
      cycle_days: clipboard.cycle_days,
      cycle_unit: clipboard.cycle_unit,
      cost: clipboard.cost,
      note: clipboard.note,
      memo: clipboard.memo,
      drawing_data: clipboard.drawing_data,
      icon: clipboard.icon,
      width: clipboard.width,
      height: clipboard.height,
      stock_qty: clipboard.stock_qty,
      safety_stock: clipboard.safety_stock,
      supplier: clipboard.supplier,
      supplier_contact: clipboard.supplier_contact,
      lead_time_days: clipboard.lead_time_days,
    }),
  });
  loadParts();
}

function formatCycleDisplay(cycleDays, cycleUnit) {
  if (cycleUnit === "N/A" || cycleDays == null) return "N/A";
  if (cycleUnit === "년") {
    const years = Math.round((cycleDays / 365) * 100) / 100;
    return `${years}년`;
  }
  return `${cycleDays}일`;
}

function cycleDaysToDisplayValue(cycleDays, cycleUnit) {
  if (cycleUnit === "N/A" || cycleDays == null) return "";
  if (cycleUnit === "년") {
    return Math.round((cycleDays / 365) * 100) / 100;
  }
  return cycleDays;
}

function updateCycleInputState() {
  const isNA = document.getElementById("partEditCycleUnit").value === "N/A";
  const cycleInput = document.getElementById("partEditCycle");
  cycleInput.disabled = isNA;
  if (isNA) cycleInput.value = "";
}

function formatCost(cost) {
  return `${Number(cost || 0).toLocaleString("ko-KR")}원`;
}

function setDrawingPreview(dataUrl) {
  currentPartDrawingData = dataUrl;
  const preview = document.getElementById("partDrawingPreview");
  const placeholder = document.getElementById("partDrawingPlaceholder");
  const removeBtn = document.getElementById("partDrawingRemoveBtn");
  const status = document.getElementById("partDrawingStatus");
  if (dataUrl) {
    preview.src = dataUrl;
    preview.classList.remove("d-none");
    placeholder.classList.add("d-none");
    removeBtn.classList.remove("d-none");
    status.textContent = "등록됨";
    document.getElementById("partDrawingArea").classList.remove("d-none");
  } else {
    preview.classList.add("d-none");
    preview.src = "";
    placeholder.classList.remove("d-none");
    removeBtn.classList.add("d-none");
    status.textContent = "";
    document.getElementById("partDrawingArea").classList.add("d-none");
  }
}

function handleDrawingPaste(e) {
  const items = e.clipboardData ? e.clipboardData.items : null;
  if (!items) return;
  for (const item of items) {
    if (item.kind === "file" && item.type.startsWith("image/")) {
      e.preventDefault();
      const file = item.getAsFile();
      if (file.size > MAX_DRAWING_BYTES) {
        alert("이미지 용량이 너무 큽니다 (최대 5MB).");
        return;
      }
      const reader = new FileReader();
      reader.onload = () => setDrawingPreview(reader.result);
      reader.readAsDataURL(file);
      return;
    }
  }
}

function openPartDetailModal(partId) {
  const p = currentParts.find((x) => x.id === partId);
  if (!p) return;
  currentPartId = partId;
  document.getElementById("partDetailTitle").textContent = p.name;
  const badge = statusBadge[p.status];
  const label = p.label || statusLabel[p.status];
  const lastText = p.last_replaced_date ? `최근 교체: ${p.last_replaced_date}` : "교체 이력 없음";
  const dueText = p.next_due
    ? `다음 교체 예정: ${p.next_due} (${p.days_left >= 0 ? p.days_left + "일 남음" : Math.abs(p.days_left) + "일 초과"})`
    : "";
  const isLowStock = (p.stock_qty || 0) <= (p.safety_stock || 0);
  const safetyText = p.safety_stock ? ` (안전재고 ${p.safety_stock}개)` : "";
  const stockText = `재고: <span class="${isLowStock ? "text-danger fw-bold" : ""}">${p.stock_qty || 0}개</span>${safetyText}`;
  const supplierText = p.supplier
    ? ` &middot; 구매처: ${escapeHtml(p.supplier)}${p.supplier_contact ? " (" + escapeHtml(p.supplier_contact) + ")" : ""}`
    : "";
  const leadTimeText = p.lead_time_days ? ` &middot; 리드타임: ${p.lead_time_days}일` : "";
  document.getElementById("partDetailBody").innerHTML = `
    <span class="badge ${badge} mb-2">${label}</span>
    ${p.spec ? `<div class="part-spec mb-1">규격: ${escapeHtml(p.spec)}</div>` : ""}
    <div class="small text-muted">
      교체 주기: ${formatCycleDisplay(p.cycle_days, p.cycle_unit)} &middot; 금액: ${formatCost(p.cost)} &middot; ${lastText}
      ${dueText ? `<br>${dueText}` : ""}
      ${p.note ? `<br>비고: ${escapeHtml(p.note)}` : ""}
      <br>${stockText}${supplierText}${leadTimeText}
    </div>`;
  renderPartMemoView(p.memo);
  document.getElementById("partDetailMemoEdit").innerHTML = p.memo || "";
  setPartMemoEditing(false);
  document.getElementById("partDetailDrawingBtn").classList.toggle("d-none", !p.drawing_data);
  partDetailModal.show();
}

function renderPartMemoView(memo) {
  const memoView = document.getElementById("partDetailMemo");
  if (isRichContentEmpty(memo)) {
    memoView.innerHTML = "";
    memoView.classList.add("is-empty");
  } else {
    memoView.classList.remove("is-empty");
    memoView.innerHTML = linkifyRichHtml(memo);
  }
}

function setPartMemoEditing(editing) {
  document.getElementById("partDetailMemo").classList.toggle("d-none", editing);
  document.getElementById("partDetailMemoEdit").classList.toggle("d-none", !editing);
  document.getElementById("editPartMemoBtn").classList.toggle("d-none", editing);
  document.getElementById("savePartMemoBtn").classList.toggle("d-none", !editing);
  document.getElementById("cancelPartMemoBtn").classList.toggle("d-none", !editing);
  if (editing) document.getElementById("partDetailMemoEdit").focus();
}

function openDrawingModal() {
  const p = currentParts.find((x) => x.id === currentPartId);
  if (!p || !p.drawing_data) return;
  document.getElementById("drawingModalTitle").textContent = `도면 - ${p.name}`;
  document.getElementById("drawingModalImg").src = p.drawing_data;
  drawingModal.show();
}

function openReplaceModal() {
  const p = currentParts.find((x) => x.id === currentPartId);
  document.getElementById("replaceDate").value = new Date().toISOString().slice(0, 10);
  document.getElementById("replaceCost").value = (p && p.cost) || 0;
  document.getElementById("replaceNote").value = "";
  replaceModal.show();
}

async function openHistoryModal() {
  const p = currentParts.find((x) => x.id === currentPartId);
  const history = await fetchJson(`/api/parts/${currentPartId}/history`);
  const list = document.getElementById("historyList");
  document.querySelector("#historyModal .modal-title").textContent = `교체 이력 - ${p ? p.name : ""}`;
  if (history.length === 0) {
    list.innerHTML = `<p class="text-muted">교체 이력이 없습니다.</p>`;
  } else {
    list.innerHTML = history
      .map(
        (h) => `
      <div class="history-row d-flex justify-content-between">
        <div><strong>${h.replaced_date}</strong> &middot; ${formatCost(h.cost)} ${h.note ? " - " + escapeHtml(h.note) : ""}</div>
        <button class="btn btn-sm btn-link text-danger p-0 del-history-btn" data-id="${h.id}">삭제</button>
      </div>`
      )
      .join("");
    list.querySelectorAll(".del-history-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await fetchJson(`/api/history/${btn.dataset.id}`, { method: "DELETE" });
        loadParts().then(() => openHistoryModal());
      });
    });
  }
  historyModal.show();
}

function openPartEditModal(part) {
  document.getElementById("partEditTitle").textContent = part ? "부품 편집" : "부품 추가";
  document.getElementById("partEditId").value = part ? part.id : "";
  document.getElementById("partEditName").value = part ? part.name : "";
  document.getElementById("partEditSpec").value = part ? part.spec || "" : "";
  const icon = part ? part.icon : "🔩";
  document.getElementById("partEditIcon").value = icon;
  const cycleUnit = part ? part.cycle_unit || "N/A" : "N/A";
  document.getElementById("partEditCycleUnit").value = cycleUnit;
  document.getElementById("partEditCycle").value = part ? cycleDaysToDisplayValue(part.cycle_days, cycleUnit) : "";
  updateCycleInputState();
  document.getElementById("partEditCost").value = part ? part.cost || 0 : 0;
  document.getElementById("partEditNote").value = part ? part.note || "" : "";
  document.getElementById("partEditMemo").innerHTML = part ? part.memo || "" : "";
  document.getElementById("partEditStockQty").value = part ? part.stock_qty || 0 : 0;
  document.getElementById("partEditSafetyStock").value = part ? part.safety_stock || 0 : 0;
  document.getElementById("partEditLeadTime").value = part && part.lead_time_days != null ? part.lead_time_days : "";
  document.getElementById("partEditSupplier").value = part ? part.supplier || "" : "";
  document.getElementById("partEditSupplierContact").value = part ? part.supplier_contact || "" : "";
  document.getElementById("partEditLastDate").value = "";
  document.getElementById("partEditLastDateWrap").classList.toggle("d-none", !!part);
  renderIconPicker("partIconPicker", "partEditIcon", icon);
  setDrawingPreview(part ? part.drawing_data || null : null);

  // 기준 설비(TEAG01)에서 동기화된 부품은 비-기준 설비에서 재고/금액/구매처를 고쳐도
  // 반영되지 않고 다음 "전체 설비에 적용" 시 덮어써지므로, 아예 수정 불가로 막는다.
  // 각 설비에서 직접 등록한 독립 부품(local_only)은 자유롭게 수정 가능.
  const locked = !!part && currentEquipmentId !== MASTER_EQUIPMENT_ID && !part.local_only;
  ["partEditStockQty", "partEditSafetyStock", "partEditCost", "partEditSupplier"].forEach((id) => {
    document.getElementById(id).disabled = locked;
  });
  document.getElementById("partLockedHint").classList.toggle("d-none", !locked);

  partEditModal.show();
}

document.addEventListener("DOMContentLoaded", () => {
  partDetailModal = new bootstrap.Modal(document.getElementById("partDetailModal"));
  replaceModal = new bootstrap.Modal(document.getElementById("replaceModal"));
  historyModal = new bootstrap.Modal(document.getElementById("historyModal"));
  partEditModal = new bootstrap.Modal(document.getElementById("partEditModal"));
  drawingModal = new bootstrap.Modal(document.getElementById("drawingModal"));

  tick();
  setInterval(tick, 1000);
  loadUnitHeader();
  loadParts();
  loadNotes();
  attachRichPasteHandler(document.getElementById("partEditMemo"));
  attachTableEditToolbar(document.getElementById("partEditMemo"));
  document.getElementById("partEditCycleUnit").addEventListener("change", updateCycleInputState);

  document.getElementById("editModeBtn").addEventListener("click", (e) => {
    editMode = !editMode;
    e.currentTarget.classList.toggle("btn-outline-light", !editMode);
    e.currentTarget.classList.toggle("btn-warning", editMode);
    document.getElementById("addPartBtn").classList.toggle("d-none", !editMode);
    updatePartPasteButton();
    renderPartsCanvas(currentParts);
  });

  document.getElementById("addPartBtn").addEventListener("click", () => openPartEditModal(null));
  document.getElementById("pastePartBtn").addEventListener("click", pastePart);

  attachRichPasteHandler(document.getElementById("notesEdit"));
  attachTableEditToolbar(document.getElementById("notesEdit"));
  document.getElementById("editNotesBtn").addEventListener("click", () => setNotesEditing(true));
  document.getElementById("cancelNotesBtn").addEventListener("click", () => {
    document.getElementById("notesEdit").innerHTML = lastNotesContent;
    setNotesEditing(false);
  });
  document.getElementById("saveNotesBtn").addEventListener("click", async () => {
    const content = sanitizeRichHtml(document.getElementById("notesEdit").innerHTML);
    try {
      const data = await fetchJson(`/api/units/${UNIT_ID}/notes`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      lastNotesContent = data.content || "";
      renderNotesView(lastNotesContent);
      document.getElementById("notesSavedAt").textContent = data.updated_at
        ? `최종 수정: ${data.updated_at}`
        : "";
      setNotesEditing(false);
    } catch (err) {
      alert(err.message);
    }
  });

  attachRichPasteHandler(document.getElementById("partDetailMemoEdit"));
  attachTableEditToolbar(document.getElementById("partDetailMemoEdit"));
  document.getElementById("editPartMemoBtn").addEventListener("click", () => setPartMemoEditing(true));
  document.getElementById("cancelPartMemoBtn").addEventListener("click", () => {
    const p = currentParts.find((x) => x.id === currentPartId);
    document.getElementById("partDetailMemoEdit").innerHTML = (p && p.memo) || "";
    setPartMemoEditing(false);
  });
  document.getElementById("savePartMemoBtn").addEventListener("click", async () => {
    const memo = sanitizeRichHtml(document.getElementById("partDetailMemoEdit").innerHTML);
    try {
      const updated = await fetchJson(`/api/parts/${currentPartId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ memo }),
      });
      const p = currentParts.find((x) => x.id === currentPartId);
      if (p) p.memo = updated.memo;
      renderPartMemoView(updated.memo);
      setPartMemoEditing(false);
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("partDetailReplaceBtn").addEventListener("click", () => {
    partDetailModal.hide();
    openReplaceModal();
  });
  document.getElementById("partDetailHistoryBtn").addEventListener("click", () => {
    partDetailModal.hide();
    openHistoryModal();
  });
  document.getElementById("partDetailDrawingBtn").addEventListener("click", () => {
    partDetailModal.hide();
    openDrawingModal();
  });

  document.getElementById("partDrawingBtn").addEventListener("click", () => {
    const area = document.getElementById("partDrawingArea");
    area.classList.toggle("d-none");
    if (!area.classList.contains("d-none")) area.focus();
  });
  document.getElementById("partDrawingArea").addEventListener("paste", handleDrawingPaste);
  document.getElementById("partDrawingRemoveBtn").addEventListener("click", () => {
    setDrawingPreview(null);
  });

  document.getElementById("replaceForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      replaced_date: document.getElementById("replaceDate").value,
      cost: parseFloat(document.getElementById("replaceCost").value) || 0,
      note: document.getElementById("replaceNote").value.trim(),
    };
    try {
      await fetchJson(`/api/parts/${currentPartId}/replace`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      replaceModal.hide();
      loadParts();
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("partEditForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = document.getElementById("partEditId").value;
    const cycleUnit = document.getElementById("partEditCycleUnit").value;
    let cycleDays = null;
    if (cycleUnit !== "N/A") {
      const cycleValue = parseFloat(document.getElementById("partEditCycle").value);
      if (isNaN(cycleValue) || cycleValue <= 0) {
        alert("교체 주기를 입력하거나 N/A를 선택하세요");
        return;
      }
      cycleDays = cycleUnit === "년" ? Math.round(cycleValue * 365) : Math.round(cycleValue);
    }
    const payload = {
      name: document.getElementById("partEditName").value.trim(),
      spec: document.getElementById("partEditSpec").value.trim(),
      icon: document.getElementById("partEditIcon").value.trim(),
      cycle_days: cycleDays,
      cycle_unit: cycleUnit,
      cost: parseFloat(document.getElementById("partEditCost").value) || 0,
      note: document.getElementById("partEditNote").value.trim(),
      memo: sanitizeRichHtml(document.getElementById("partEditMemo").innerHTML),
      drawing_data: currentPartDrawingData,
      stock_qty: parseInt(document.getElementById("partEditStockQty").value, 10) || 0,
      safety_stock: parseInt(document.getElementById("partEditSafetyStock").value, 10) || 0,
      lead_time_days: document.getElementById("partEditLeadTime").value || null,
      supplier: document.getElementById("partEditSupplier").value.trim(),
      supplier_contact: document.getElementById("partEditSupplierContact").value.trim(),
    };
    try {
      if (id) {
        await fetchJson(`/api/parts/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } else {
        payload.last_replaced_date = document.getElementById("partEditLastDate").value || null;
        await fetchJson(`/api/units/${UNIT_ID}/parts`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      partEditModal.hide();
      loadParts();
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("applyPartsBtn").addEventListener("click", async () => {
    const ok = confirm(
      "마지막으로 BACKUP한 이 유닛의 부품 구성을 동일한 이름의 유닛을 가진 나머지 설비 전체에 적용합니다.\n" +
      "(기본 유닛 구성 페이지에서 BACKUP한 시점의 구성이 기준이며, 그 이후 이 페이지에서 수정한 내용은 다시 BACKUP해야 반영됩니다)\n" +
      "- 이름이 같은 부품은 규격/교체주기/비고/메모/도면/재고수량/구매처/리드타임/아이콘/위치/크기가 백업된 값으로 갱신됩니다.\n" +
      "- 백업에 없는 이름의 부품은 각 설비에서 삭제되며, 등록된 교체 이력도 함께 삭제됩니다.\n" +
      "- 각 설비에서 직접 등록한 독립 부품은 영향받지 않습니다.\n\n" +
      "계속하시겠습니까?"
    );
    if (!ok) return;
    try {
      const result = await fetchJson(`/api/units/${UNIT_ID}/apply-parts`, { method: "POST" });
      alert(`설비 ${result.equipment_count}대에 부품 구성(${result.part_count}개)을 적용했습니다.`);
    } catch (err) {
      alert(err.message);
    }
  });
});
