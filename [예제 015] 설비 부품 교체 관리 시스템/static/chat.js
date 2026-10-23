function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleString("ko-KR");
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s == null ? "" : String(s);
  return div.innerHTML;
}

function formatTime() {
  return new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
}

let chatHistory = [];
let chatBodyEl = null;

function appendMessage(role, html, isLoading) {
  const row = document.createElement("div");
  row.className = `msg-row ${role === "user" ? "user" : "bot"}`;
  const bubbleClass = isLoading ? "msg-bubble msg-loading" : "msg-bubble";
  row.innerHTML = `
    <div class="msg-avatar"><i class="bi bi-${role === "user" ? "person-fill" : "robot"}"></i></div>
    <div>
      <div class="${bubbleClass}">${html}</div>
      ${isLoading ? "" : `<div class="msg-time">${formatTime()}</div>`}
    </div>
  `;
  chatBodyEl.appendChild(row);
  chatBodyEl.scrollTop = chatBodyEl.scrollHeight;
  return row;
}

function startNewConversation() {
  chatHistory = [];
  chatBodyEl.innerHTML = "";
  appendMessage("bot", "안녕하세요! 설비·부품 교체 데이터에 대해 궁금하신 점을 물어보세요.");
}

async function handleChatSubmit(e) {
  e.preventDefault();
  const input = document.getElementById("chatInput");
  const btn = document.getElementById("chatSendBtn");
  const question = input.value.trim();
  if (!question) return;

  appendMessage("user", escapeHtml(question));
  chatHistory.push({ role: "user", content: question });
  input.value = "";
  input.disabled = true;
  btn.disabled = true;

  const loadingRow = appendMessage("bot", "답변을 생성하는 중입니다...", true);

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: chatHistory }),
    });
    if (res.status === 401) {
      window.location.href = "/login";
      return;
    }
    const body = await res.json().catch(() => ({}));
    loadingRow.remove();
    if (!res.ok) {
      const msg = body.not_configured
        ? `${escapeHtml(body.error)} <a href="/config">설정 페이지로 이동</a>`
        : escapeHtml(body.error || "요청 처리 중 오류가 발생했습니다.");
      appendMessage("bot", msg).querySelector(".msg-bubble").classList.add("msg-error");
      return;
    }
    appendMessage("bot", escapeHtml(body.reply));
    chatHistory.push({ role: "assistant", content: body.reply });
  } catch (err) {
    loadingRow.remove();
    appendMessage("bot", `요청 중 오류가 발생했습니다: ${escapeHtml(err.message)}`)
      .querySelector(".msg-bubble").classList.add("msg-error");
  } finally {
    input.disabled = false;
    btn.disabled = false;
    input.focus();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  tick();
  setInterval(tick, 1000);
  chatBodyEl = document.getElementById("chatBody");
  startNewConversation();
  document.getElementById("chatForm").addEventListener("submit", handleChatSubmit);
  document.getElementById("chatResetBtn").addEventListener("click", startNewConversation);
});
