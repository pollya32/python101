let masterUnits = [];
let entries = [];

function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s || "";
  return div.innerHTML;
}

function cycleDaysToDisplayValue(cycleDays, cycleUnit) {
  if (cycleUnit === "년") {
    return Math.round((cycleDays / 365) * 100) / 100;
  }
  return cycleDays;
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

async function loadEntries() {
  const data = await fetchJson("/api/bulk-parts");
  masterUnits = data.master_units;
  entries = data.entries;
  renderTable();
}

function unitMultiselectHtml(entry) {
  const selectedIds = new Set(entry.units.map((u) => u.id));
  const label =
    entry.units.length === 0
      ? "유닛 선택..."
      : entry.units.length === 1
      ? escapeHtml(entry.units[0].name)
      : `${entry.units.length}개 선택`;
  const checkboxes = masterUnits
    .map(
      (u) => `
    <div class="form-check">
      <input class="form-check-input unit-check" type="checkbox" value="${u.id}" id="unitChk-${entry.id}-${u.id}" ${selectedIds.has(u.id) ? "checked" : ""}>
      <label class="form-check-label" for="unitChk-${entry.id}-${u.id}">${escapeHtml(u.name)}</label>
    </div>`
    )
    .join("");
  return `
    <div class="dropdown">
      <button class="btn btn-sm btn-outline-secondary dropdown-toggle w-100 text-truncate" type="button"
        data-bs-toggle="dropdown" data-bs-auto-close="outside">
        ${label}
      </button>
      <div class="dropdown-menu p-2 unit-filter-menu" data-entry-id="${entry.id}">
        ${checkboxes}
      </div>
    </div>`;
}

function updateRowUnitUi(entryId) {
  const row = document.querySelector(`tr[data-entry-id="${entryId}"]`);
  const entry = entries.find((e) => e.id === entryId);
  if (!row || !entry) return;
  const btn = row.querySelector(".dropdown-toggle");
  btn.textContent =
    entry.units.length === 0
      ? "유닛 선택..."
      : entry.units.length === 1
      ? entry.units[0].name
      : `${entry.units.length}개 선택`;
  const registerBtn = row.querySelector(".register-btn");
  if (entry.status !== "registered") {
    registerBtn.disabled = entry.units.length === 0;
  }
}

function renderTable() {
  const tbody = document.getElementById("bulkTableBody");
  const emptyMsg = document.getElementById("bulkEmptyMsg");
  if (entries.length === 0) {
    tbody.innerHTML = "";
    emptyMsg.classList.remove("d-none");
    return;
  }
  emptyMsg.classList.add("d-none");
  tbody.innerHTML = entries
    .map(
      (e) => `
    <tr data-entry-id="${e.id}">
      <td><input type="checkbox" class="form-check-input row-check" data-id="${e.id}"></td>
      <td class="text-muted">${escapeHtml(e.raw_unit_text)}</td>
      <td>${unitMultiselectHtml(e)}</td>
      <td><input type="text" class="form-control form-control-sm field-input" data-id="${e.id}" data-field="part_name" value="${escapeHtml(e.part_name)}"></td>
      <td><input type="text" class="form-control form-control-sm field-input" data-id="${e.id}" data-field="q_code" value="${escapeHtml(e.q_code)}"></td>
      <td><input type="text" class="form-control form-control-sm field-input" data-id="${e.id}" data-field="note" value="${escapeHtml(e.note)}"></td>
      <td><input type="number" class="form-control form-control-sm field-input" data-id="${e.id}" data-field="cost" value="${e.cost || 0}" min="0" step="100"></td>
      <td>
        <div class="input-group input-group-sm bulk-cycle-group">
          <input type="number" class="form-control form-control-sm cycle-field-input" data-id="${e.id}" data-cyclefield="value" value="${cycleDaysToDisplayValue(e.cycle_days || 90, e.cycle_unit || "일")}" min="1" step="1">
          <select class="form-select form-select-sm flex-grow-0 w-auto cycle-field-input" data-id="${e.id}" data-cyclefield="unit">
            <option value="일" ${(e.cycle_unit || "일") === "일" ? "selected" : ""}>일</option>
            <option value="년" ${e.cycle_unit === "년" ? "selected" : ""}>년</option>
          </select>
        </div>
      </td>
      <td>
        ${e.status === "registered"
          ? '<span class="bulk-status-registered"><i class="bi bi-check-circle-fill"></i> 등록됨</span>'
          : '<span class="bulk-status-pending">대기</span>'}
      </td>
      <td>
        <button class="btn btn-sm btn-primary register-btn" data-id="${e.id}" ${e.status === "registered" || e.units.length === 0 ? "disabled" : ""}>
          등록
        </button>
      </td>
    </tr>`
    )
    .join("");

  // "strategy" is not a real data-bs-* option Bootstrap reads (only autoClose,
  // boundary, display, offset, popperConfig, reference are). To actually escape
  // the table wrapper's overflow:hidden clipping, the Popper positioning
  // strategy must be set to "fixed" via the JS popperConfig option instead.
  tbody.querySelectorAll(".dropdown-toggle").forEach((toggleEl) => {
    bootstrap.Dropdown.getOrCreateInstance(toggleEl, {
      popperConfig: (defaultConfig) => ({ ...defaultConfig, strategy: "fixed" }),
    });
  });

  tbody.querySelectorAll(".unit-filter-menu").forEach((menu) => {
    const entryId = parseInt(menu.dataset.entryId, 10);
    const dropdownWrapper = menu.closest(".dropdown");

    menu.querySelectorAll(".unit-check").forEach((cb) => {
      cb.addEventListener("change", async () => {
        // persist immediately, but do NOT reload/re-render here — that would destroy
        // the open dropdown DOM and make it look like it "closed" after one click.
        const selected = Array.from(menu.querySelectorAll(".unit-check:checked")).map((c) =>
          parseInt(c.value, 10)
        );
        await fetchJson(`/api/bulk-parts/${entryId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ unit_ids: selected }),
        });
        const entry = entries.find((e) => e.id === entryId);
        if (entry) {
          entry.units = masterUnits.filter((u) => selected.includes(u.id));
        }
        updateRowUnitUi(entryId);
      });
    });

    // only re-render the full table (to refresh disabled states etc.) once the
    // dropdown is actually closed, i.e. after the user has finished selecting.
    dropdownWrapper.addEventListener("hidden.bs.dropdown", () => {
      renderTable();
    });
  });

  tbody.querySelectorAll(".field-input").forEach((input) => {
    input.addEventListener("change", async () => {
      const id = parseInt(input.dataset.id, 10);
      const value = input.dataset.field === "cost" ? parseFloat(input.value) || 0 : input.value.trim();
      await fetchJson(`/api/bulk-parts/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [input.dataset.field]: value }),
      });
    });
  });

  tbody.querySelectorAll("tr").forEach((row) => {
    const valueInput = row.querySelector('.cycle-field-input[data-cyclefield="value"]');
    const unitSelect = row.querySelector('.cycle-field-input[data-cyclefield="unit"]');
    if (!valueInput || !unitSelect) return;
    const id = parseInt(valueInput.dataset.id, 10);
    const commitCycle = async () => {
      const cycleUnit = unitSelect.value;
      const cycleValue = parseFloat(valueInput.value) || 1;
      const cycleDays = cycleUnit === "년" ? Math.round(cycleValue * 365) : Math.round(cycleValue);
      await fetchJson(`/api/bulk-parts/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cycle_days: cycleDays, cycle_unit: cycleUnit }),
      });
    };
    valueInput.addEventListener("change", commitCycle);
    unitSelect.addEventListener("change", commitCycle);
  });

  tbody.querySelectorAll(".register-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = parseInt(btn.dataset.id, 10);
      try {
        await fetchJson(`/api/bulk-parts/${id}/register`, { method: "POST" });
        await loadEntries();
      } catch (err) {
        alert(err.message);
      }
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  tick();
  setInterval(tick, 1000);
  loadEntries();

  document.getElementById("applyPasteBtn").addEventListener("click", async () => {
    const text = document.getElementById("pasteArea").value;
    if (!text.trim()) return;
    try {
      await fetchJson("/api/bulk-parts/paste", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      document.getElementById("pasteArea").value = "";
      await loadEntries();
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("selectAllCheck").addEventListener("change", (e) => {
    document.querySelectorAll(".row-check").forEach((cb) => (cb.checked = e.target.checked));
  });

  document.getElementById("deleteSelectedBtn").addEventListener("click", async () => {
    const ids = Array.from(document.querySelectorAll(".row-check:checked")).map((cb) => cb.dataset.id);
    if (ids.length === 0) {
      alert("삭제할 항목을 선택하세요.");
      return;
    }
    if (!confirm(`선택한 ${ids.length}개 항목을 목록에서 삭제할까요? (이미 등록된 부품 자체는 삭제되지 않습니다)`)) return;
    for (const id of ids) {
      await fetchJson(`/api/bulk-parts/${id}`, { method: "DELETE" });
    }
    document.getElementById("selectAllCheck").checked = false;
    await loadEntries();
  });
});
