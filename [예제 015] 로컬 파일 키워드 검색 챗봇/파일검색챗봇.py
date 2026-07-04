"""
내 컴퓨터 폴더 검색 챗봇
=====================================================
채팅창에 궁금한 내용을 물어보면, 지정한 폴더 안의 파일들을 키워드로 훑어서
관련 있는 파일의 핵심 내용과 파일 위치를 정리해서 알려주는 프로그램입니다.

설치: pip install -r requirements.txt
실행: python 파일검색챗봇.py
접속: http://localhost:5000

※ 이 프로그램은 실행한 컴퓨터의 파일을 직접 읽습니다.
   반드시 본인 컴퓨터에서만 실행하고, 외부 네트워크에 노출하지 마세요.
"""

import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime

from flask import Flask, jsonify, render_template_string, request

try:
    from pypdf import PdfReader
    HAS_PDF = True
except BaseException:
    # 일부 환경에서는 선택적 의존성(pypdf)이 깨져 있어도 앱 전체가 죽지 않도록 함
    HAS_PDF = False

try:
    from docx import Document as DocxDocument
    HAS_DOCX = True
except BaseException:
    HAS_DOCX = False

app = Flask(__name__)

_BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_BASE, "config.json")

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".log", ".ini", ".cfg", ".py", ".js", ".ts", ".jsx", ".tsx", ".java",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".go", ".rb", ".php", ".sql", ".sh",
    ".bat", ".html", ".htm", ".css", ".xml", ".rst",
}
PDF_EXTENSIONS = {".pdf"}
DOCX_EXTENSIONS = {".docx"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | PDF_EXTENSIONS | DOCX_EXTENSIONS

EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules", "venv", ".venv", "env",
    ".idea", ".vscode", "dist", "build", ".mypy_cache", "$RECYCLE.BIN",
    "System Volume Information",
}
EXCLUDE_EXTENSIONS = {".env", ".pem", ".key", ".crt", ".p12", ".pfx"}

MAX_TEXT_FILE_SIZE = 3 * 1024 * 1024  # 3MB 넘는 텍스트 파일은 내용은 건너뜀
MAX_FILES_SCAN = 8000                 # 안전을 위한 스캔 파일 수 상한
MAX_RESULTS = 30
SNIPPET_CONTEXT = 60
MAX_SNIPPETS = 3
MAX_SUMMARY_SENTENCES = 3

STOPWORDS = {
    "파일", "자료", "내용", "정리", "요약", "위치", "어디", "어디에", "어디야",
    "어디있어", "어디있나요", "어디에있어", "어디에있나요", "찾아줘", "찾아",
    "알려줘", "알려", "해줘", "줘", "좀", "관련", "대한", "대해", "대해서",
    "관해", "관해서", "있어", "있나요", "있는지", "무엇", "뭐야", "뭐있어",
    "뭐가있어", "뭔가요", "궁금해", "궁금", "싶어", "보여줘", "검색",
    "검색해줘", "검색해",
}
PARTICLE_SUFFIXES = sorted([
    "이라는", "라는", "이란", "란", "이나", "나", "이며", "며", "이랑", "랑",
    "에서는", "에서", "에게", "으로", "까지", "부터", "에는", "에도", "와는",
    "과는", "에", "로", "과", "와", "이", "가", "은", "는", "을", "를", "도",
    "만", "의",
], key=len, reverse=True)


# ── 설정 저장/불러오기 ────────────────────────────────────────────────────

def default_root_dir():
    home = os.path.expanduser("~")
    for candidate in ("Documents", "문서", "Desktop", "바탕화면"):
        p = os.path.join(home, candidate)
        if os.path.isdir(p):
            return p
    return home


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("root_dir") and os.path.isdir(data["root_dir"]):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"root_dir": default_root_dir()}


def save_config(root_dir):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"root_dir": root_dir}, f, ensure_ascii=False)


# ── 키워드 추출 ──────────────────────────────────────────────────────────

def strip_particle(token):
    for suf in PARTICLE_SUFFIXES:
        if token.endswith(suf) and len(token) > len(suf):
            return token[: -len(suf)]
    return token


def extract_keywords(query):
    """질문 문장에서 검색 키워드를 뽑아낸다. "..." 로 감싸면 정확한 구문 검색."""
    phrase_matches = re.findall(r'"([^"]+)"|\'([^\']+)\'', query)
    phrases = [a or b for a, b in phrase_matches if (a or b).strip()]
    remaining = re.sub(r'["\']([^"\']+)["\']', " ", query)

    tokens = re.findall(r"[가-힣A-Za-z0-9]+", remaining)
    keywords = []
    for tok in tokens:
        base = strip_particle(tok)
        if not base or base in STOPWORDS:
            continue
        if len(base) < 2 and not (base.isdigit() or base.isupper()):
            continue
        if base not in keywords:
            keywords.append(base)

    if not keywords and not phrases:
        # 전부 걸러졌다면 원본 토큰이라도 사용
        keywords = [t for t in tokens if t not in STOPWORDS]

    return keywords, phrases


# ── 파일 내용 읽기 ────────────────────────────────────────────────────────

def read_text_file(path):
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
        except OSError:
            return None
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return None


def read_pdf_file(path):
    if not HAS_PDF:
        return None
    try:
        reader = PdfReader(path)
        parts = []
        for page in reader.pages[:30]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n".join(parts)
    except Exception:
        return None


def read_docx_file(path):
    if not HAS_DOCX:
        return None
    try:
        doc = DocxDocument(path)
        return "\n".join(p.text for p in doc.paragraphs)
    except Exception:
        return None


def extract_text(path, ext):
    if ext in TEXT_EXTENSIONS:
        return read_text_file(path)
    if ext in PDF_EXTENSIONS:
        return read_pdf_file(path)
    if ext in DOCX_EXTENSIONS:
        return read_docx_file(path)
    return None


# ── 검색어 위치 찾기 / 요약 ───────────────────────────────────────────────

def find_term_spans(text, terms):
    low = text.lower()
    spans = []
    for term in terms:
        t = term.lower().strip()
        if not t:
            continue
        start = 0
        while True:
            idx = low.find(t, start)
            if idx == -1:
                break
            spans.append((idx, idx + len(t)))
            start = idx + len(t)
    return spans


def build_snippet(text, terms):
    spans = sorted(find_term_spans(text, terms))
    if not spans:
        chunk = re.sub(r"\s+", " ", text[: SNIPPET_CONTEXT * 2]).strip()
        return chunk

    merged = []
    for s, e in spans:
        if merged and s <= merged[-1][1] + SNIPPET_CONTEXT:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    pieces = []
    for s, e in merged[:MAX_SNIPPETS]:
        a = max(0, s - SNIPPET_CONTEXT)
        b = min(len(text), e + SNIPPET_CONTEXT)
        chunk = re.sub(r"\s+", " ", text[a:b]).strip()
        if a > 0:
            chunk = "…" + chunk
        if b < len(text):
            chunk = chunk + "…"
        pieces.append(chunk)
    return "  /  ".join(pieces)


def summarize(text, terms):
    raw_sentences = re.split(r"[\n]+|(?<=[.!?。])\s+", text)
    sentences = [s.strip() for s in raw_sentences if s.strip()]
    if not sentences:
        return ""

    scored = []
    for i, s in enumerate(sentences):
        low = s.lower()
        score = sum(low.count(t.lower()) for t in terms if t.strip())
        if score > 0:
            scored.append((score, i, s))

    if not scored:
        return sentences[0][:150]

    top = sorted(scored, key=lambda x: (-x[0], x[1]))[:MAX_SUMMARY_SENTENCES]
    top_in_order = sorted(top, key=lambda x: x[1])
    return " / ".join(s[:200] for _, _, s in top_in_order)


# ── 파일 검색 ────────────────────────────────────────────────────────────

def search_files(root_dir, keywords, phrases):
    terms = phrases + keywords
    results = []
    scanned = 0
    truncated = False

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")]

        if truncated:
            break

        for fname in filenames:
            if scanned >= MAX_FILES_SCAN:
                truncated = True
                break
            scanned += 1

            ext = os.path.splitext(fname)[1].lower()
            if ext in EXCLUDE_EXTENSIONS:
                continue

            full_path = os.path.join(dirpath, fname)
            filename_score = sum(1 for t in terms if t and t.lower() in fname.lower())

            content = None
            content_only_note = False
            if ext in SUPPORTED_EXTENSIONS:
                try:
                    size = os.path.getsize(full_path)
                except OSError:
                    size = None
                if ext in TEXT_EXTENSIONS and size is not None and size > MAX_TEXT_FILE_SIZE:
                    size_ok = False
                else:
                    size_ok = True
                if size_ok:
                    content = extract_text(full_path, ext)
            elif filename_score > 0:
                content_only_note = True

            content_score = 0
            matched_terms = []
            if content:
                low = content.lower()
                for t in terms:
                    if not t:
                        continue
                    c = low.count(t.lower())
                    if c:
                        content_score += c
                        matched_terms.append(t)

            total_score = filename_score * 3 + content_score
            if total_score <= 0:
                continue

            try:
                stat = os.stat(full_path)
                size_bytes = stat.st_size
                modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            except OSError:
                size_bytes, modified = None, ""

            if content:
                effective_terms = matched_terms or terms
                summary = summarize(content, effective_terms)
                snippet = build_snippet(content, effective_terms)
            elif content_only_note:
                summary = "본문 미리보기를 지원하지 않는 파일 형식이라, 파일명이 검색어와 일치해서 찾았어요."
                snippet = ""
            else:
                summary = ""
                snippet = ""

            results.append({
                "path": full_path,
                "dir": dirpath,
                "name": fname,
                "ext": ext,
                "size": size_bytes,
                "modified": modified,
                "score": total_score,
                "summary": summary,
                "snippet": snippet,
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:MAX_RESULTS], scanned, truncated


def format_size(num_bytes):
    if num_bytes is None:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.0f}{unit}" if unit == "B" else f"{num_bytes:.1f}{unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f}TB"


# ── 폴더 열기 (내 컴퓨터에서 실행 중일 때만 동작) ─────────────────────────

def open_containing_folder(path):
    system = platform.system()
    try:
        if system == "Windows":
            subprocess.run(["explorer", f"/select,{path}"], check=False)
        elif system == "Darwin":
            subprocess.run(["open", "-R", path], check=False)
        else:
            subprocess.run(["xdg-open", os.path.dirname(path)], check=False)
        return True
    except Exception:
        return False


# ── HTML ─────────────────────────────────────────────────────────────────

PAGE_T = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>내 컴퓨터 파일 검색 챗봇</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
<style>
body{background:#eef1f5;font-family:'Malgun Gothic',sans-serif;height:100vh;display:flex;flex-direction:column}
#topbar{background:#1a3a5c;color:#fff;padding:10px 16px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
#topbar h1{font-size:16px;margin:0;font-weight:700;white-space:nowrap}
#topbar .path-box{flex:1;min-width:220px;display:flex;gap:6px}
#chat{flex:1;overflow-y:auto;padding:18px;display:flex;flex-direction:column;gap:12px}
.msg{max-width:78%;padding:10px 14px;border-radius:14px;font-size:14px;line-height:1.5;white-space:pre-wrap}
.msg.user{align-self:flex-end;background:#1a3a5c;color:#fff;border-bottom-right-radius:4px}
.msg.bot{align-self:flex-start;background:#fff;border:1px solid #dee2e6;border-bottom-left-radius:4px;max-width:88%}
.msg.system{align-self:center;background:#fff3cd;color:#7a5b00;font-size:12px;border-radius:8px}
.file-card{background:#f8f9fb;border:1px solid #e2e6ea;border-radius:10px;padding:10px 12px;margin-top:8px}
.file-card .fname{font-weight:700;font-size:13.5px}
.file-card .fpath{color:#6c757d;font-size:11.5px;word-break:break-all;margin:2px 0 6px}
.file-card .fsummary{font-size:13px;color:#333}
.file-card mark{background:#ffe58f;padding:0 2px;border-radius:2px}
.file-card .factions{margin-top:8px;display:flex;gap:6px}
#inputbar{padding:12px 16px;background:#fff;border-top:1px solid #dee2e6;display:flex;gap:8px}
#inputbar input{flex:1}
.result-count{font-size:12px;color:#6c757d;margin-bottom:4px}
</style>
</head>
<body>
<div id="topbar">
  <h1><i class="bi bi-folder2-open me-1"></i>내 파일 검색 챗봇</h1>
  <div class="path-box">
    <input type="text" id="rootDirInput" class="form-control form-control-sm" value="{{ root_dir }}" placeholder="검색할 폴더 경로">
    <button class="btn btn-sm btn-light" onclick="applyRootDir()">폴더 적용</button>
  </div>
</div>
<div id="chat"></div>
<div id="inputbar">
  <input type="text" id="msgInput" class="form-control" placeholder="예: 회의록에서 예산 관련 내용 찾아줘" autofocus>
  <button class="btn btn-primary" onclick="sendMessage()"><i class="bi bi-send me-1"></i>보내기</button>
</div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
function escapeHtml(s){
  return (s || '').replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}
function highlight(escapedText, terms){
  let out = escapedText;
  (terms || []).forEach(function(t){
    if(!t) return;
    const esc = t.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
    out = out.replace(new RegExp('(' + esc + ')', 'gi'), '<mark>$1</mark>');
  });
  return out;
}
function addMessage(text, cls){
  const chat = document.getElementById('chat');
  const div = document.createElement('div');
  div.className = 'msg ' + cls;
  div.innerHTML = escapeHtml(text);
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}
function addBotResults(data){
  const chat = document.getElementById('chat');
  const wrap = document.createElement('div');
  wrap.className = 'msg bot';
  let html = '';
  if(data.keywords_used && data.keywords_used.length){
    html += '<div class="result-count">검색 키워드: ' + escapeHtml(data.keywords_used.join(', ')) + '</div>';
  }
  if(!data.results.length){
    html += '해당 키워드와 관련된 파일을 찾지 못했어요. 다른 표현으로 다시 물어봐 주세요.';
  } else {
    html += '<div class="result-count">' + data.results.length + '개의 관련 파일을 찾았어요' +
            (data.truncated ? ' (파일이 너무 많아 일부만 스캔했어요)' : '') + '</div>';
    data.results.forEach(function(r){
      const terms = data.keywords_used;
      html += '<div class="file-card">' +
        '<div class="fname"><i class="bi bi-file-earmark-text me-1"></i>' + highlight(escapeHtml(r.name), terms) + '</div>' +
        '<div class="fpath">' + escapeHtml(r.dir) + (r.modified ? ' · ' + r.modified : '') + (r.size_h ? ' · ' + r.size_h : '') + '</div>' +
        (r.summary ? '<div class="fsummary"><strong>핵심 내용:</strong> ' + highlight(escapeHtml(r.summary), terms) + '</div>' : '') +
        (r.snippet ? '<div class="fsummary text-muted mt-1">' + highlight(escapeHtml(r.snippet), terms) + '</div>' : '') +
        '<div class="factions">' +
        '<button class="btn btn-sm btn-outline-primary" onclick="openFolder(' + JSON.stringify(r.path) + ')"><i class="bi bi-folder2-open"></i> 폴더 열기</button>' +
        '<button class="btn btn-sm btn-outline-secondary" onclick="copyPath(' + JSON.stringify(r.path) + ')"><i class="bi bi-clipboard"></i> 경로 복사</button>' +
        '</div></div>';
    });
  }
  wrap.innerHTML = html;
  chat.appendChild(wrap);
  chat.scrollTop = chat.scrollHeight;
}
function sendMessage(){
  const input = document.getElementById('msgInput');
  const text = input.value.trim();
  if(!text) return;
  addMessage(text, 'user');
  input.value = '';
  const thinking = addMessage('검색 중...', 'bot');
  fetch('/api/chat', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({message: text})
  }).then(r => r.json()).then(data => {
    thinking.remove();
    if(data.error){ addMessage(data.error, 'system'); return; }
    addBotResults(data);
  }).catch(() => { thinking.remove(); addMessage('오류가 발생했어요. 잠시 후 다시 시도해주세요.', 'system'); });
}
function applyRootDir(){
  const path = document.getElementById('rootDirInput').value.trim();
  fetch('/api/set_root', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({root_dir: path})
  }).then(r => r.json()).then(data => {
    if(data.ok){ addMessage('검색 폴더가 "' + path + '" (으)로 설정됐어요.', 'system'); }
    else { addMessage(data.error || '폴더를 적용하지 못했어요.', 'system'); }
  });
}
function openFolder(path){
  fetch('/api/open', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path: path})
  }).then(r => r.json()).then(data => {
    if(!data.ok) addMessage(data.error || '폴더를 여는 데 실패했어요.', 'system');
  });
}
function copyPath(path){
  navigator.clipboard.writeText(path).then(() => addMessage('경로를 복사했어요: ' + path, 'system'));
}
document.getElementById('msgInput').addEventListener('keydown', function(e){
  if(e.key === 'Enter') sendMessage();
});
addMessage('안녕하세요! 저는 지정한 폴더 안의 파일을 키워드로 찾아주는 챗봇이에요.\\n예: "여행 계획서 어디 있어?" 처럼 물어보면 관련 파일과 핵심 내용, 위치를 알려드려요.\\n먼저 상단에서 검색할 폴더 경로를 확인/변경해주세요.', 'bot');
</script>
</body>
</html>"""


# ── 라우트 ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    config = load_config()
    return render_template_string(PAGE_T, root_dir=config["root_dir"])


@app.route("/api/config")
def api_config():
    return jsonify(load_config())


@app.route("/api/set_root", methods=["POST"])
def api_set_root():
    data = request.get_json(force=True, silent=True) or {}
    root_dir = (data.get("root_dir") or "").strip()
    if not root_dir or not os.path.isdir(root_dir):
        return jsonify({"ok": False, "error": "존재하지 않는 폴더 경로예요. 다시 확인해주세요."})
    save_config(root_dir)
    return jsonify({"ok": True, "root_dir": root_dir})


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(force=True, silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "질문을 입력해주세요."})

    config = load_config()
    root_dir = config["root_dir"]
    if not os.path.isdir(root_dir):
        return jsonify({"error": "검색할 폴더가 올바르지 않아요. 상단에서 폴더 경로를 다시 지정해주세요."})

    keywords, phrases = extract_keywords(message)
    if not keywords and not phrases:
        return jsonify({"error": "검색할 키워드를 찾지 못했어요. 조금 더 구체적으로 질문해주세요."})

    results, scanned, truncated = search_files(root_dir, keywords, phrases)
    for r in results:
        r["size_h"] = format_size(r["size"])

    return jsonify({
        "keywords_used": phrases + keywords,
        "results": results,
        "scanned": scanned,
        "truncated": truncated,
    })


@app.route("/api/open", methods=["POST"])
def api_open():
    data = request.get_json(force=True, silent=True) or {}
    path = (data.get("path") or "").strip()
    config = load_config()
    root_dir = os.path.abspath(config["root_dir"])

    if not path or not os.path.exists(path):
        return jsonify({"ok": False, "error": "파일을 찾을 수 없어요."})

    abs_path = os.path.abspath(path)
    if os.path.commonpath([abs_path, root_dir]) != root_dir:
        return jsonify({"ok": False, "error": "허용되지 않은 경로예요."})

    ok = open_containing_folder(abs_path)
    return jsonify({"ok": ok, "error": None if ok else "이 프로그램이 로컬 컴퓨터에서 실행 중일 때만 폴더를 열 수 있어요."})


if __name__ == "__main__":
    cfg = load_config()
    print("=" * 55)
    print(" 내 컴퓨터 파일 검색 챗봇 시작!")
    print("=" * 55)
    print(f" 검색 대상 폴더: {cfg['root_dir']}")
    print(" 내 PC 접속: http://localhost:5000")
    print(" 종료:       Ctrl+C")
    if not HAS_PDF:
        print(" (참고) pypdf 미설치: PDF 파일 내용 검색은 지원되지 않아요.")
    if not HAS_DOCX:
        print(" (참고) python-docx 미설치: DOCX 파일 내용 검색은 지원되지 않아요.")
    print("=" * 55)
    app.run(host="0.0.0.0", port=5000, debug=False)
