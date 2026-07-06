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

function unitOptionsHtml(selectedUnitId) {
  return (
    `<option value="">유닛 선택...</option>` +
    masterUnits
      .map(
        (u) =>
          `<option value="${u.id}" ${u.id === selectedUnitId ? "selected" : ""}>${escapeHtml(u.name)}</option>`
      )
      .join("")
  );
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
      <td>
        <select class="form-select form-select-sm unit-select" data-id="${e.id}">
          ${unitOptionsHtml(e.unit_id)}
        </select>
      </td>
      <td><input type="text" class="form-control form-control-sm field-input" data-id="${e.id}" data-field="part_name" value="${escapeHtml(e.part_name)}"></td>
      <td><input type="text" class="form-control form-control-sm field-input" data-id="${e.id}" data-field="q_code" value="${escapeHtml(e.q_code)}"></td>
      <td><input type="text" class="form-control form-control-sm field-input" data-id="${e.id}" data-field="note" value="${escapeHtml(e.note)}"></td>
      <td>
        ${e.status === "registered"
          ? '<span class="bulk-status-registered"><i class="bi bi-check-circle-fill"></i> 등록됨</span>'
          : '<span class="bulk-status-pending">대기</span>'}
      </td>
      <td>
        <button class="btn btn-sm btn-primary register-btn" data-id="${e.id}" ${e.status === "registered" || !e.unit_id ? "disabled" : ""}>
          등록
        </button>
      </td>
    </tr>`
    )
    .join("");

  tbody.querySelectorAll(".unit-select").forEach((sel) => {
    sel.addEventListener("change", async () => {
      const id = parseInt(sel.dataset.id, 10);
      await fetchJson(`/api/bulk-parts/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ unit_id: sel.value ? parseInt(sel.value, 10) : null }),
      });
      await loadEntries();
    });
  });

  tbody.querySelectorAll(".field-input").forEach((input) => {
    input.addEventListener("change", async () => {
      const id = parseInt(input.dataset.id, 10);
      await fetchJson(`/api/bulk-parts/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [input.dataset.field]: input.value.trim() }),
      });
    });
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
