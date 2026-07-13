let editMode = false;
let unitEditModal, equipmentInfoModal, unitDrawingModal;
let currentEquipmentData = null;
let currentUnitDrawingData = null;
const MAX_DRAWING_BYTES = 5 * 1024 * 1024;

const ICON_CHOICES = [
  "⚙️", "🔧", "🔩", "🛠️", "🪛", "🔨", "📦", "🖥️",
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

async function loadEquipmentHeader() {
  try {
    const equipment = await fetchJson(`/api/equipments/${EQUIPMENT_ID}`);
    currentEquipmentData = equipment;
    document.getElementById("equipmentPageTitle").textContent = equipment.name;
    document.title = `${equipment.name} - 설비 부품 교체 관리 시스템`;
    renderEquipmentInfo(equipment);
  } catch (err) {
    alert("설비 정보를 불러올 수 없습니다.");
    window.location.href = "/";
  }
}

function renderEquipmentInfo(equipment) {
  document.getElementById("equipmentLocationView").textContent = equipment.location || "미입력";
  document.getElementById("equipmentSetupDateView").textContent = equipment.setup_date || "미입력";
  document.getElementById("equipmentRuntimeView").textContent = equipment.runtime_display || "-";
}

async function loadUnits() {
  const units = await fetchJson(`/api/equipments/${EQUIPMENT_ID}/units`);
  renderCanvas(units);
}

function renderCanvas(units) {
  const canvas = document.getElementById("canvas");
  canvas.innerHTML = units.map(unitCardHtml).join("");
  units.forEach((u) => {
    const card = canvas.querySelector(`[data-unit-id="${u.id}"]`);
    card.style.left = `${u.pos_x}%`;
    card.style.top = `${u.pos_y}%`;
    card.style.width = `${u.width}px`;
    card.style.height = `${u.height}px`;

    card.querySelector(".edit-unit-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      openUnitEditModal(u);
    });
    card.querySelector(".delete-unit-btn")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`"${u.name}" 유닛을 삭제할까요? 등록된 부품도 함께 휴지통으로 이동합니다. (휴지통에서 복원할 수 있습니다)`)) return;
      await fetchJson(`/api/units/${u.id}`, { method: "DELETE" });
      loadUnits();
    });

    card.querySelector(".copy-unit-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      copyUnit(u);
    });

    card.querySelector(".unit-drawing-btn")?.addEventListener("click", (e) => {
      e.stopPropagation();
      openUnitDrawingModal(u);
    });

    makeDraggable(card, u);
    makeResizable(card, u);
    makeResizableHorizontal(card, u);
  });
}

const MIN_UNIT_WIDTH = 90;
const MIN_UNIT_HEIGHT = 80;
const MAX_UNIT_WIDTH = 320;
const MAX_UNIT_HEIGHT = 260;

function makeResizable(card, unit) {
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
      const w = Math.min(Math.max(startWidth + dx, MIN_UNIT_WIDTH), MAX_UNIT_WIDTH);
      const h = Math.min(Math.max(startHeight + dy, MIN_UNIT_HEIGHT), MAX_UNIT_HEIGHT);
      card.style.width = `${w}px`;
      card.style.height = `${h}px`;
    }

    async function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      card.classList.remove("dragging");
      const width = card.offsetWidth;
      const height = card.offsetHeight;
      unit.width = width;
      unit.height = height;
      await fetchJson(`/api/units/${unit.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ width, height }),
      });
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

function makeResizableHorizontal(card, unit) {
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
      const w = Math.min(Math.max(startWidth + dx, MIN_UNIT_WIDTH), MAX_UNIT_WIDTH);
      card.style.width = `${w}px`;
    }

    async function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      card.classList.remove("dragging");
      const width = card.offsetWidth;
      unit.width = width;
      await fetchJson(`/api/units/${unit.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ width }),
      });
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

function makeDraggable(card, unit) {
  card.addEventListener("mousedown", (e) => {
    if (!editMode) return;
    if (e.target.closest(".unit-edit-actions")) return;
    if (e.target.closest(".unit-drawing-btn")) return;
    e.preventDefault();

    const canvas = document.getElementById("canvas");
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
        unit.pos_x = pos_x;
        unit.pos_y = pos_y;
        await fetchJson(`/api/units/${unit.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pos_x, pos_y }),
        });
      } else {
        window.location.href = `/unit/${unit.id}`;
      }
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });

  card.addEventListener("click", (e) => {
    if (editMode) return;
    if (e.target.closest(".unit-edit-actions")) return;
    if (e.target.closest(".unit-drawing-btn")) return;
    window.location.href = `/unit/${unit.id}`;
  });
}

function unitCardHtml(u) {
  return `
    <div class="unit-card ${editMode ? "edit-mode" : ""}" data-unit-id="${u.id}" style="--uc:${u.color}">
      <span class="unit-status-dot dot-${u.overall_status}"></span>
      <div class="unit-icon-wrap">
        <span class="unit-icon">${u.icon}</span>
        ${u.soon_count > 0 ? `<span class="soon-badge" title="교체 임박 부품 ${u.soon_count}건">${u.soon_count}</span>` : ""}
        ${u.overdue_count > 0 ? `<span class="overdue-badge" title="교체 필요 부품 ${u.overdue_count}건">${u.overdue_count}</span>` : ""}
        ${u.drawing_data ? `<button type="button" class="unit-drawing-btn" title="도면 보기"><i class="bi bi-image"></i></button>` : ""}
      </div>
      <div class="unit-name">${escapeHtml(u.name)}</div>
      <div class="unit-part-count">${u.part_count}개 부품 등록</div>
      <div class="unit-edit-actions">
        <button class="edit-unit-btn" title="편집"><i class="bi bi-pencil"></i></button>
        <button class="copy-unit-btn" title="복사"><i class="bi bi-copy"></i></button>
        <button class="delete-unit-btn" title="삭제"><i class="bi bi-trash"></i></button>
      </div>
      <div class="resize-handle" title="크기 조절 (가로+세로)"></div>
      <div class="resize-handle-h" title="가로 크기 조절"></div>
    </div>`;
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

let lastNotesContent = "";

async function loadNotes() {
  const data = await fetchJson(`/api/equipments/${EQUIPMENT_ID}/notes`);
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

const UNIT_CLIPBOARD_KEY = "unitClipboard";

async function copyUnit(unit) {
  const parts = await fetchJson(`/api/units/${unit.id}/parts`);
  const clipboard = {
    name: unit.name,
    icon: unit.icon,
    color: unit.color,
    width: unit.width,
    height: unit.height,
    parts: parts.map((p) => ({
      name: p.name,
      spec: p.spec,
      cycle_days: p.cycle_days,
      cycle_unit: p.cycle_unit,
      cost: p.cost,
      last_replaced_date: p.last_replaced_date,
      note: p.note,
      memo: p.memo,
      drawing_data: p.drawing_data,
      icon: p.icon,
      pos_x: p.pos_x,
      pos_y: p.pos_y,
      width: p.width,
      height: p.height,
      stock_qty: p.stock_qty,
      safety_stock: p.safety_stock,
      supplier: p.supplier,
      supplier_contact: p.supplier_contact,
      lead_time_days: p.lead_time_days,
    })),
  };
  try {
    await idbClipboardSet(UNIT_CLIPBOARD_KEY, clipboard);
  } catch (err) {
    alert("유닛 복사에 실패했습니다: " + err.message);
    return;
  }
  await updatePasteButton();
  alert(`"${unit.name}" 유닛을 복사했습니다. (부품 ${parts.length}개 포함)\n"붙여넣기" 버튼으로 동일한 유닛을 만들 수 있습니다.`);
}

async function getUnitClipboard() {
  try {
    return await idbClipboardGet(UNIT_CLIPBOARD_KEY);
  } catch {
    return null;
  }
}

async function updatePasteButton() {
  const clipboard = await getUnitClipboard();
  const btn = document.getElementById("pasteUnitBtn");
  btn.classList.toggle("d-none", !editMode || !clipboard);
  if (clipboard) btn.title = `"${clipboard.name}" 붙여넣기 (부품 ${clipboard.parts.length}개 포함)`;
}

async function pasteUnit() {
  const clipboard = await getUnitClipboard();
  if (!clipboard) {
    alert("복사된 유닛이 없습니다. 먼저 유닛의 복사 아이콘을 눌러주세요.");
    return;
  }
  const newUnit = await fetchJson(`/api/equipments/${EQUIPMENT_ID}/units`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: `${clipboard.name} 복사본`,
      icon: clipboard.icon,
      color: clipboard.color,
      width: clipboard.width,
      height: clipboard.height,
    }),
  });
  for (const part of clipboard.parts) {
    await fetchJson(`/api/units/${newUnit.id}/parts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(part),
    });
  }
  loadUnits();
}

function openUnitEditModal(unit) {
  document.getElementById("unitEditTitle").textContent = unit ? "유닛 편집" : "유닛 추가";
  document.getElementById("unitEditId").value = unit ? unit.id : "";
  document.getElementById("unitEditName").value = unit ? unit.name : "";
  const icon = unit ? unit.icon : "⚙️";
  document.getElementById("unitEditIcon").value = icon;
  document.getElementById("unitEditColor").value = unit ? unit.color : "#1a3a5c";
  renderIconPicker("unitIconPicker", "unitEditIcon", icon);
  setUnitDrawingPreview(unit ? unit.drawing_data || null : null);
  unitEditModal.show();
}

function setUnitDrawingPreview(dataUrl) {
  currentUnitDrawingData = dataUrl;
  const preview = document.getElementById("unitDrawingPreview");
  const placeholder = document.getElementById("unitDrawingPlaceholder");
  const removeBtn = document.getElementById("unitDrawingRemoveBtn");
  const status = document.getElementById("unitDrawingStatus");
  if (dataUrl) {
    preview.src = dataUrl;
    preview.classList.remove("d-none");
    placeholder.classList.add("d-none");
    removeBtn.classList.remove("d-none");
    status.textContent = "등록됨";
    document.getElementById("unitDrawingArea").classList.remove("d-none");
  } else {
    preview.classList.add("d-none");
    preview.src = "";
    placeholder.classList.remove("d-none");
    removeBtn.classList.add("d-none");
    status.textContent = "";
    document.getElementById("unitDrawingArea").classList.add("d-none");
  }
}

function handleUnitDrawingPaste(e) {
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
      reader.onload = () => setUnitDrawingPreview(reader.result);
      reader.readAsDataURL(file);
      return;
    }
  }
}

function openUnitDrawingModal(unit) {
  if (!unit || !unit.drawing_data) return;
  document.getElementById("unitDrawingModalTitle").textContent = `도면 - ${unit.name}`;
  document.getElementById("unitDrawingModalImg").src = unit.drawing_data;
  unitDrawingModal.show();
}

document.addEventListener("DOMContentLoaded", () => {
  unitEditModal = new bootstrap.Modal(document.getElementById("unitEditModal"));
  equipmentInfoModal = new bootstrap.Modal(document.getElementById("equipmentInfoModal"));
  unitDrawingModal = new bootstrap.Modal(document.getElementById("unitDrawingModal"));

  tick();
  setInterval(tick, 1000);
  loadEquipmentHeader();
  loadUnits();
  loadNotes();

  document.getElementById("unitDrawingBtn").addEventListener("click", () => {
    const area = document.getElementById("unitDrawingArea");
    area.classList.toggle("d-none");
    if (!area.classList.contains("d-none")) area.focus();
  });
  document.getElementById("unitDrawingArea").addEventListener("paste", handleUnitDrawingPaste);
  document.getElementById("unitDrawingRemoveBtn").addEventListener("click", () => {
    setUnitDrawingPreview(null);
  });

  document.getElementById("editEquipmentInfoBtn").addEventListener("click", () => {
    document.getElementById("equipmentLocationInput").value = currentEquipmentData?.location || "";
    document.getElementById("equipmentSetupDateInput").value = currentEquipmentData?.setup_date || "";
    equipmentInfoModal.show();
  });

  document.getElementById("equipmentInfoForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      location: document.getElementById("equipmentLocationInput").value.trim(),
      setup_date: document.getElementById("equipmentSetupDateInput").value || null,
    };
    try {
      await fetchJson(`/api/equipments/${EQUIPMENT_ID}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      equipmentInfoModal.hide();
      loadEquipmentHeader();
    } catch (err) {
      alert(err.message);
    }
  });

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
      const data = await fetchJson(`/api/equipments/${EQUIPMENT_ID}/notes`, {
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

  document.getElementById("editModeBtn").addEventListener("click", (e) => {
    editMode = !editMode;
    e.currentTarget.classList.toggle("btn-outline-light", !editMode);
    e.currentTarget.classList.toggle("btn-warning", editMode);
    document.getElementById("addUnitBtn").classList.toggle("d-none", !editMode);
    updatePasteButton();
    loadUnits();
  });

  document.getElementById("addUnitBtn").addEventListener("click", () => openUnitEditModal(null));
  document.getElementById("pasteUnitBtn").addEventListener("click", pasteUnit);

  document.getElementById("unitEditForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = document.getElementById("unitEditId").value;
    const payload = {
      name: document.getElementById("unitEditName").value.trim(),
      icon: document.getElementById("unitEditIcon").value.trim(),
      color: document.getElementById("unitEditColor").value,
      drawing_data: currentUnitDrawingData,
    };
    try {
      if (id) {
        await fetchJson(`/api/units/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } else {
        await fetchJson(`/api/equipments/${EQUIPMENT_ID}/units`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      unitEditModal.hide();
      loadUnits();
    } catch (err) {
      alert(err.message);
    }
  });
});
