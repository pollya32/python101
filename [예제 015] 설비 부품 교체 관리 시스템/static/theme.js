// 전체 스타일 테마 (기본 / 사이버틱) 전환.
// <head>에서 동기 로드되어 본문이 그려지기 전에 저장된 테마를 즉시 적용하므로
// 페이지 진입 시 밝은 화면이 번쩍이는 현상(FOUC)이 없다. 추가 네트워크 요청이나
// 반복 실행 코드가 없어 성능에는 영향을 주지 않는다.
(function () {
  const THEME_KEY = "appTheme";

  function currentTheme() {
    return localStorage.getItem(THEME_KEY) === "cyber" ? "cyber" : "default";
  }

  function applyTheme(theme) {
    if (theme === "cyber") {
      document.documentElement.dataset.theme = "cyber";
    } else {
      delete document.documentElement.dataset.theme;
    }
  }

  applyTheme(currentTheme());

  document.addEventListener("DOMContentLoaded", () => {
    const bar = document.querySelector(".topbar > div:last-of-type");
    if (!bar) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-outline-light theme-toggle-btn";
    btn.title = "화면 스타일 전환 (기본 / 사이버틱)";

    function refreshLabel() {
      btn.innerHTML =
        currentTheme() === "cyber"
          ? '<i class="bi bi-stars"></i> 사이버'
          : '<i class="bi bi-palette"></i> 기본';
    }

    btn.addEventListener("click", () => {
      const next = currentTheme() === "cyber" ? "default" : "cyber";
      localStorage.setItem(THEME_KEY, next);
      applyTheme(next);
      refreshLabel();
    });

    refreshLabel();
    bar.appendChild(btn);
  });
})();
