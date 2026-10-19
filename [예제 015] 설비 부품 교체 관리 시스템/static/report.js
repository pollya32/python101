function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s == null ? "" : String(s);
  return div.innerHTML;
}

function formatMoney(v) {
  return `₩${Number(v || 0).toLocaleString("ko-KR")}`;
}

const STATUS_BADGE_LABEL = { ok: "정상", soon: "교체 임박", overdue: "교체 필요", unknown: "N/A" };

function renderStatRow(data) {
  const el = document.getElementById("reportStatRow");
  el.innerHTML = `
    <div class="report-stat-card">
      <div class="label">총 교체 건수 (최근 ${data.window_days}일)</div>
      <div class="value">${Number(data.total_count).toLocaleString("ko-KR")}건</div>
    </div>
    <div class="report-stat-card">
      <div class="label">총 교체 비용 (최근 ${data.window_days}일)</div>
      <div class="value">${formatMoney(data.total_cost)}</div>
    </div>
    <div class="report-stat-card">
      <div class="label">평균 실제 교체 간격</div>
      <div class="value">${data.avg_cycle_days != null ? data.avg_cycle_days + "일" : "-"}</div>
    </div>
    <div class="report-stat-card">
      <div class="label">교체 필요 / 임박 설비</div>
      <div class="value"><span class="overdue-n">${data.donut.counts.overdue}건</span> <span class="sep">/</span> <span class="soon-n">${data.donut.counts.soon}건</span></div>
    </div>
  `;
}

function renderCostChart(data) {
  document.getElementById("reportCostTitle").textContent = `교체 비용 상위 부품 (최근 ${data.window_days}일)`;
  const el = document.getElementById("reportCostChart");
  if (!data.cost_bars.length) {
    el.innerHTML = `<p class="report-empty">최근 ${data.window_days}일간 교체 이력이 없습니다.</p>`;
    return;
  }
  el.innerHTML = `<div class="report-bar-chart">${data.cost_bars.map((b) => `
    <div class="report-bar-row">
      <div class="rbname">${escapeHtml(b.name)}</div>
      <div class="report-bar-track"><div class="report-bar-fill" style="width: ${b.pct}%;"></div></div>
      <div class="rbamount">${formatMoney(b.cost)}</div>
    </div>
  `).join("")}</div>`;
}

function renderDonut(data) {
  const d = data.donut;
  const el = document.getElementById("reportDonut");
  el.innerHTML = `
    <div class="report-donut-wrap">
      <div class="report-donut" style="background: conic-gradient(#22c55e 0deg ${d.b1}deg, #f59e0b ${d.b1}deg ${d.b2}deg, #ef4444 ${d.b2}deg ${d.b3}deg, #9ca3af ${d.b3}deg 360deg);">
        <div class="report-donut-center">
          <div class="rn">${d.total}</div>
          <div class="rl">전체 설비</div>
        </div>
      </div>
      <div class="report-donut-legend">
        <span><i style="background: #22c55e;"></i>정상 ${d.counts.ok}</span>
        <span><i style="background: #f59e0b;"></i>임박 ${d.counts.soon}</span>
        <span><i style="background: #ef4444;"></i>필요 ${d.counts.overdue}</span>
        <span><i style="background: #9ca3af;"></i>미기록 ${d.counts.unknown}</span>
      </div>
    </div>
  `;
}

function renderTable(data) {
  const el = document.getElementById("reportTableWrap");
  if (!data.table_rows.length) {
    el.innerHTML = `<p class="report-empty">교체 이력이 2건 이상인 부품이 아직 없어 교체 주기를 계산할 수 없습니다.</p>`;
    return;
  }
  el.innerHTML = `
    <table class="report-table">
      <thead>
        <tr><th>부품명</th><th>스펙</th><th>설정 주기</th><th>평균 실제 간격</th><th>최근 교체일</th><th>상태</th></tr>
      </thead>
      <tbody>
        ${data.table_rows.map((r) => `
          <tr>
            <td>${escapeHtml(r.name)}</td>
            <td>${escapeHtml(r.spec)}</td>
            <td>${r.min_cycle_days ? `${r.min_cycle_days}일` : "N/A"}</td>
            <td>${r.avg_actual_interval_days}일</td>
            <td>${escapeHtml(r.last_replaced_date)}</td>
            <td><span class="badge badge-${r.status}">${escapeHtml(r.status_label)}</span></td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function renderInsights(data) {
  const el = document.getElementById("reportInsights");
  if (!data.insights.length) {
    el.innerHTML = `<p class="report-empty">분석할 데이터가 아직 충분하지 않습니다. 교체 이력이 쌓이면 분석 문장이 표시됩니다.</p>`;
    return;
  }
  el.innerHTML = `<div class="report-insight-list">${data.insights.map((ins) => `
    <div class="report-insight ${ins.tag}">
      <span class="rtag">${escapeHtml(ins.label)}</span>
      <span>${ins.text}</span>
    </div>
  `).join("")}</div>`;
}

async function loadReport() {
  const res = await fetch("/api/report");
  if (res.status === 401) {
    window.location.href = "/login";
    return;
  }
  const data = await res.json();
  document.getElementById("reportTitle").textContent = `최근 ${data.window_days}일 부품 교체 분석 보고서`;
  document.getElementById("reportSubtitle").textContent =
    `생성일 ${data.generated_at} · 전체 설비 ${data.equipment_count}호기 · 유닛 ${data.unit_count}개 기준`;
  renderStatRow(data);
  renderCostChart(data);
  renderDonut(data);
  renderTable(data);
  renderInsights(data);
}

async function handleAskSubmit(e) {
  e.preventDefault();
  const input = document.getElementById("askInput");
  const btn = document.getElementById("askBtn");
  const answerEl = document.getElementById("askAnswer");
  const question = input.value.trim();
  if (!question) return;

  btn.disabled = true;
  input.disabled = true;
  answerEl.innerHTML = `<div class="ask-answer ask-loading">답변을 생성하는 중입니다...</div>`;

  try {
    const res = await fetch("/api/report/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (res.status === 401) {
      window.location.href = "/login";
      return;
    }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (body.not_configured) {
        answerEl.innerHTML = `<div class="ask-answer ask-error">${escapeHtml(body.error)} <a href="/config">설정 페이지로 이동</a></div>`;
      } else {
        answerEl.innerHTML = `<div class="ask-answer ask-error">${escapeHtml(body.error || "요청 처리 중 오류가 발생했습니다.")}</div>`;
      }
      return;
    }
    answerEl.innerHTML = `<div class="ask-answer">${escapeHtml(body.answer)}</div>`;
  } catch (err) {
    answerEl.innerHTML = `<div class="ask-answer ask-error">요청 중 오류가 발생했습니다: ${escapeHtml(err.message)}</div>`;
  } finally {
    btn.disabled = false;
    input.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  tick();
  setInterval(tick, 1000);
  loadReport();
  document.getElementById("askForm").addEventListener("submit", handleAskSubmit);
});
