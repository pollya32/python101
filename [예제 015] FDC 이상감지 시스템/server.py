#!/usr/bin/env python3
"""
FDC 이상감지 시스템 — FastAPI ML 백엔드 서버
실행: python server.py  (또는 start_server.bat)
접속: http://localhost:8000
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import pandas as pd
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from sklearn.svm import OneClassSVM
from sklearn.covariance import EllipticEnvelope
from sklearn.preprocessing import StandardScaler
import io
import os
import time
import traceback
import warnings
warnings.filterwarnings("ignore")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="FDC ML Anomaly Detection API", version="1.1.0")

# ════════════════════════════════════════════════════════════════════
# [보안수정 A] CORS 출처 제한
#   현재: allow_origins=["*"] → 사내망 외부·타 도메인에서도 API 호출 가능
#   수정: 실제 사용 도메인/IP 만 허용
#         예) allow_origins=["http://localhost:8000", "http://10.x.x.x:8000"]
# ════════════════════════════════════════════════════════════════════
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # ← [보안수정 A] 허용 출처를 사내 IP/도메인으로 변경
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ML_METHOD_LABELS = {
    "ml_isoforest": "Isolation Forest",
    "ml_lof":       "Local Outlier Factor",
    "ml_ocsvm":     "One-Class SVM",
    "ml_elliptic":  "Elliptic Envelope (Robust Covariance)",
}

GROUP_BY_COLS = {
    "eqp_ppid":     ['EQP ID', 'PPID'],
    "eqp_ch_ppid":  ['EQP ID', 'CH', 'PPID'],
    "ppid":         ['PPID'],
    "all":          [],
}


# ── 요청 모델 ──────────────────────────────────────────────────

# ════════════════════════════════════════════════════════════════════
# [보안수정 B] DB 자격증명을 요청 바디로 받지 않기
#   현재: 브라우저가 보낸 스크립트 문자열 안에 user/password 평문 포함
#         → HTTP 패킷 캡처 / 개발자도구 Network 탭으로 즉시 노출
#   수정: DB 접속 정보는 서버의 환경변수(.env) 또는 설정 파일로 관리
#         클라이언트는 "어떤 DB 프리셋을 쓸지 이름만" 전달
#         예) preset: str = "oracle_fdc_prod"
#              → 서버에서 os.environ["DB_FDC_DSN"] 으로 조회
# ════════════════════════════════════════════════════════════════════
class RunScriptRequest(BaseModel):
    script: str                    # ← [보안수정 B] DB 접속 정보 제거, 프리셋 이름 방식으로 전환
    timeout: Optional[int] = 30   # ← [보안수정 D] 현재 선언만 있고 실제 강제 안 됨 → 실제 적용 필요


class AnalyzeRequest(BaseModel):
    data: str
    col_map: Dict[str, int]
    method: str
    threshold: float = 0.05
    params: Optional[Dict[str, Any]] = {}
    group_by: Optional[str] = "eqp_ppid"


# ── 데이터 파싱 ────────────────────────────────────────────────
def parse_dataframe(data: str, col_map: Dict[str, int]) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(data), sep='\t', dtype=str, on_bad_lines='skip')
    headers = df.columns.tolist()

    field_map = {
        'date':  '날짜',
        'eqp':   'EQP ID',
        'lot':   'LOTID',
        'ch':    'CH',
        'ppid':  'PPID',
        'value': 'REAL DATA',
    }

    rename = {}
    for field, std_name in field_map.items():
        idx = col_map.get(field, -1)
        if isinstance(idx, int) and 0 <= idx < len(headers):
            rename[headers[idx]] = std_name

    df = df.rename(columns=rename)

    if 'REAL DATA' not in df.columns:
        raise ValueError("'REAL DATA' 컬럼을 찾을 수 없습니다. 컬럼 매핑을 확인하세요.")

    df['_row_idx'] = range(len(df))
    df['REAL DATA'] = pd.to_numeric(df['REAL DATA'], errors='coerce')
    df = df.dropna(subset=['REAL DATA']).reset_index(drop=True)
    return df


def _get_group_cols(df: pd.DataFrame, group_by: str) -> list:
    wanted = GROUP_BY_COLS.get(group_by, GROUP_BY_COLS["eqp_ppid"])
    return [c for c in wanted if c in df.columns]


def _normalize_score(scores: np.ndarray) -> np.ndarray:
    """점수 정규화: 0(정상)~1(이상) 통일"""
    s_min, s_max = scores.min(), scores.max()
    if s_max == s_min:
        return np.zeros_like(scores, dtype=float)
    norm = 1.0 - (scores - s_min) / (s_max - s_min)
    return np.round(norm.astype(float), 6)


# ── ML 이상감지 ────────────────────────────────────────────────
def run_ml(
    df: pd.DataFrame,
    method: str,
    contamination: float,
    params: dict,
    group_by: str = "eqp_ppid",
) -> tuple[pd.DataFrame, list]:
    """그룹별 ML 이상감지 실행. (result_df, failed_groups) 반환"""

    group_cols = _get_group_cols(df, group_by)
    groups = df.groupby(group_cols) if group_cols else [('ALL', df)]

    chunks       = []
    failed_groups: list = []

    for key, grp in groups:
        grp = grp.copy()
        vals = grp['REAL DATA'].values.reshape(-1, 1)
        n    = len(vals)
        group_label = str(key)

        min_n = 10 if method == 'ml_elliptic' else 5
        if n < min_n:
            grp['isAnom']    = False
            grp['diff']      = 0.0
            grp['score']     = 0.0
            grp['norm_score'] = 0.0
            chunks.append(grp)
            continue

        cont     = float(np.clip(contamination, 1 / n, 0.5))
        scaler   = StandardScaler()
        vs       = scaler.fit_transform(vals)
        mean_val = float(np.mean(vals))

        try:
            if method == 'ml_isoforest':
                mdl = IsolationForest(
                    contamination=cont,
                    n_estimators=int(params.get('n_estimators', 100)),
                    random_state=42, n_jobs=-1,
                )
                preds  = mdl.fit_predict(vs)
                scores = mdl.score_samples(vs)

            elif method == 'ml_lof':
                k = min(int(params.get('n_neighbors', 20)), n - 1)
                mdl = LocalOutlierFactor(
                    n_neighbors=k,
                    contamination=cont,
                    novelty=False, n_jobs=-1,
                )
                preds  = mdl.fit_predict(vs)
                scores = mdl.negative_outlier_factor_

            elif method == 'ml_ocsvm':
                mdl = OneClassSVM(
                    nu=float(np.clip(cont, 1e-4, 0.5)),
                    kernel='rbf', gamma='scale',
                )
                preds  = mdl.fit_predict(vs)
                scores = mdl.score_samples(vs)

            elif method == 'ml_elliptic':
                mdl = EllipticEnvelope(contamination=cont, random_state=42)
                preds  = mdl.fit_predict(vs)
                scores = mdl.score_samples(vs)

            else:
                raise ValueError(f"지원하지 않는 방식: {method}")

            grp['isAnom']     = preds == -1
            grp['diff']       = np.round(np.abs(vals.flatten() - mean_val), 4)
            grp['score']      = np.round(scores, 6)
            grp['norm_score'] = _normalize_score(scores)

        except Exception as e:
            failed_groups.append({'group': group_label, 'error': str(e)})
            grp['isAnom']     = False
            grp['diff']       = 0.0
            grp['score']      = 0.0
            grp['norm_score'] = 0.0

        chunks.append(grp)

    result_df = pd.concat(chunks, ignore_index=True) if chunks else df.assign(
        isAnom=False, diff=0.0, score=0.0, norm_score=0.0
    )
    return result_df, failed_groups


# ── 통계 계산 ──────────────────────────────────────────────────
def calc_stats(df: pd.DataFrame, group_cols: list) -> List[dict]:
    if not group_cols or not all(c in df.columns for c in group_cols):
        return []

    stats = []
    for key, grp in df.groupby(group_cols):
        vals  = grp['REAL DATA'].values
        anoms = grp['isAnom'].sum()
        entry = dict(zip(group_cols, key if isinstance(key, tuple) else (key,)))
        entry.update({
            'count': len(grp),
            'anoms': int(anoms),
            'mean':  round(float(np.mean(vals)), 4),
            'std':   round(float(np.std(vals)),  4),
            'min':   round(float(np.min(vals)),  4),
            'max':   round(float(np.max(vals)),  4),
        })
        stats.append(entry)
    return stats


def _build_response(method: str, result_df: pd.DataFrame,
                    failed_groups: list, group_cols: list) -> dict:
    result_df = result_df.where(pd.notnull(result_df), other=None)
    records   = result_df.drop(columns=['_row_idx'], errors='ignore').to_dict(orient='records')
    stats     = calc_stats(result_df, group_cols)
    total     = len(records)
    anoms     = int(result_df['isAnom'].sum())
    return {
        "ok":           True,
        "method_label": ML_METHOD_LABELS.get(method, method),
        "results":      records,
        "stats":        stats,
        "failed_groups": failed_groups,
        "summary": {
            "total": total,
            "anoms": anoms,
            "rate":  round(anoms / total * 100, 2) if total else 0,
        },
    }


# ── 정적 파일 및 루트 ──────────────────────────────────────────
@app.get("/")
def root():
    return FileResponse(os.path.join(BASE_DIR, "index.html"))


# ════════════════════════════════════════════════════════════════════
# [보안수정 C] /run-script 엔드포인트 인증 추가
#   현재: 인증 없음 → 사내망 누구든 curl 한 줄로 서버에서 코드 실행 가능
#   수정: API 키 헤더 검증 추가 (최소한의 보호)
#         예)
#           from fastapi.security import APIKeyHeader
#           api_key_header = APIKeyHeader(name="X-API-Key")
#           API_KEY = os.environ.get("FDC_API_KEY", "")  # 환경변수로 관리
#
#           @app.post("/run-script")
#           def run_script(req: RunScriptRequest,
#                          key: str = Depends(api_key_header)):
#               if key != API_KEY:
#                   raise HTTPException(403, "인증 실패")
# ════════════════════════════════════════════════════════════════════
@app.post("/run-script")
def run_script(req: RunScriptRequest):
    """사용자 Python 스크립트 실행 → DataFrame 반환"""

    # ════════════════════════════════════════════════════════════════
    # [보안수정 C] 이 위치에 API 키 / 토큰 인증 로직 삽입
    # ════════════════════════════════════════════════════════════════

    # ════════════════════════════════════════════════════════════════
    # [보안수정 D] 실행 시간 제한 강제 적용
    #   현재: req.timeout 필드가 있지만 실제로 강제되지 않음
    #          → 무한루프 스크립트로 서버 프로세스 점거 가능
    #   수정: concurrent.futures 또는 threading으로 timeout 강제
    #         예)
    #           import concurrent.futures
    #           with concurrent.futures.ThreadPoolExecutor() as pool:
    #               fut = pool.submit(exec, compile(req.script,...), ns)
    #               try:
    #                   fut.result(timeout=req.timeout)
    #               except concurrent.futures.TimeoutError:
    #                   raise HTTPException(408, "스크립트 실행 시간 초과")
    # ════════════════════════════════════════════════════════════════

    # ════════════════════════════════════════════════════════════════
    # [보안수정 E] 금지 키워드 사전 차단
    #   현재: 어떤 코드든 exec() 에 전달됨
    #          → os.system(), subprocess, __import__ 등으로 서버 명령 실행 가능
    #   수정: 실행 전 스크립트 텍스트에서 위험 패턴 검사
    #         예)
    #           import re
    #           BLOCKED = [r'\bos\.system\b', r'\bsubprocess\b', r'\beval\b',
    #                      r'\bexec\b', r'__import__', r'\bopen\s*\(',
    #                      r'\bshutil\b', r'\bpathlib\b']
    #           for pat in BLOCKED:
    #               if re.search(pat, req.script):
    #                   raise HTTPException(400, f"허용되지 않는 코드 패턴: {pat}")
    # ════════════════════════════════════════════════════════════════

    # ════════════════════════════════════════════════════════════════
    # [보안수정 F] 네임스페이스에서 os 제거, __builtins__ 제한
    #   현재: os 모듈 직접 제공 → os.listdir(), os.remove() 등 파일시스템 전체 접근
    #          builtins 미제한 → open(), __import__() 로 추가 모듈 로드 가능
    #   수정:
    #     ns = {
    #         "__builtins__": {},   # ← builtins 차단 (open, import 등 비활성화)
    #         "pd": pd,
    #         "np": np,
    #         "io": io,
    #         # os 제거
    #     }
    # ════════════════════════════════════════════════════════════════
    ns: dict = {
        "pd": pd,
        "np": np,
        "io": io,
        "os": os,   # ← [보안수정 F] os 제거 필요 (파일시스템 접근 차단)
    }

    # ════════════════════════════════════════════════════════════════
    # [보안수정 F] 허용 라이브러리 화이트리스트 검토
    #   - requests : 사내망 내부 서버로의 무단 HTTP 요청 가능 → 필요 여부 재검토
    #   - sqlite3  : 서버 로컬 DB 파일 직접 접근 가능 → 허용 범위 확인
    #   보안 정책에 따라 DB 전용 라이브러리만 남기고 나머지 제거 권장
    # ════════════════════════════════════════════════════════════════
    for lib in ("sqlite3", "cx_Oracle", "pymysql", "psycopg2", "pyodbc",
                "requests",   # ← [보안수정 F] 무단 외부 HTTP 요청 가능, 허용 여부 검토
                "json", "re", "datetime"):
        try:
            ns[lib] = __import__(lib)
        except ImportError:
            pass

    # ════════════════════════════════════════════════════════════════
    # [보안수정 G] 감사 로그 (Audit Log) 기록
    #   현재: 누가 어떤 코드를 언제 실행했는지 기록 없음
    #          → 보안 사고 발생 시 추적 불가
    #   수정: 실행 전 로그 파일에 기록
    #         예)
    #           import logging, datetime
    #           audit_log = logging.getLogger("audit")
    #           audit_log.info({
    #               "time": datetime.datetime.now().isoformat(),
    #               "client_ip": request.client.host,   # Request 객체 주입 필요
    #               "script_hash": hashlib.sha256(req.script.encode()).hexdigest(),
    #               "script_preview": req.script[:200],
    #           })
    # ════════════════════════════════════════════════════════════════

    try:
        exec(compile(req.script, "<script>", "exec"), ns)
    except Exception:
        raise HTTPException(400, f"스크립트 오류:\n{traceback.format_exc()}")

    result = ns.get("df")
    if result is None:
        raise HTTPException(400, "스크립트에서 'df' 변수를 정의해야 합니다.\n예: df = pd.read_csv(...)")
    if not isinstance(result, pd.DataFrame):
        raise HTTPException(400, f"'df'는 pandas DataFrame이어야 합니다. 현재 타입: {type(result).__name__}")
    if result.empty:
        raise HTTPException(400, "결과 DataFrame이 비어 있습니다.")

    tsv = result.to_csv(sep="\t", index=False)
    return {
        "ok":   True,
        "rows": len(result),
        "cols": result.columns.tolist(),
        "data": tsv,
    }


@app.get("/health")
def health():
    return {
        "status":  "ok",
        "version": "1.1.0",
        "methods": list(ML_METHOD_LABELS.keys()),
        "group_by_options": list(GROUP_BY_COLS.keys()),
    }


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    if req.method not in ML_METHOD_LABELS:
        raise HTTPException(400, f"지원하지 않는 방식: {req.method}. 사용 가능: {list(ML_METHOD_LABELS)}")

    try:
        df = parse_dataframe(req.data, req.col_map)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if df.empty:
        raise HTTPException(400, "유효한 숫자 데이터가 없습니다.")

    group_by   = req.group_by or "eqp_ppid"
    cont       = float(np.clip(req.threshold, 0.001, 0.5))
    result_df, failed = run_ml(df, req.method, cont, req.params or {}, group_by)
    group_cols = _get_group_cols(df, group_by)

    return _build_response(req.method, result_df, failed, group_cols)


@app.post("/compare")
def compare(req: AnalyzeRequest):
    """② 4종 동시 비교 — 앙상블(다수결) 결과 포함"""
    try:
        df = parse_dataframe(req.data, req.col_map)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if df.empty:
        raise HTTPException(400, "유효한 숫자 데이터가 없습니다.")

    group_by   = req.group_by or "eqp_ppid"
    cont       = float(np.clip(req.threshold, 0.001, 0.5))
    group_cols = _get_group_cols(df, group_by)

    per_method: dict = {}
    t_start = time.time()

    for method in ML_METHOD_LABELS:
        rdf, failed = run_ml(df, method, cont, req.params or {}, group_by)
        per_method[method] = {
            "result_df":     rdf,
            "failed_groups": failed,
        }

    elapsed_ms = round((time.time() - t_start) * 1000)

    vote_df = df[['_row_idx']].copy()
    for method, d in per_method.items():
        vote_df[method] = d['result_df']['isAnom'].values

    vote_cols = list(ML_METHOD_LABELS.keys())
    vote_df['vote_count']    = vote_df[vote_cols].sum(axis=1)
    vote_df['ensemble_anom'] = vote_df['vote_count'] >= 2

    method_summaries = {}
    for method, d in per_method.items():
        rdf   = d['result_df']
        total = len(rdf)
        anoms = int(rdf['isAnom'].sum())
        method_summaries[method] = {
            "label":  ML_METHOD_LABELS[method],
            "total":  total,
            "anoms":  anoms,
            "rate":   round(anoms / total * 100, 2) if total else 0,
            "failed": d['failed_groups'],
            "stats":  calc_stats(rdf, group_cols),
        }

    ensemble_rows = []
    base_cols = [c for c in ['날짜', 'EQP ID', 'LOTID', 'CH', 'PPID', 'REAL DATA'] if c in df.columns]
    for i, row in df.iterrows():
        rec = {c: row[c] for c in base_cols}
        v   = vote_df.iloc[i]
        rec['vote_count']    = int(v['vote_count'])
        rec['ensemble_anom'] = bool(v['ensemble_anom'])
        for method in ML_METHOD_LABELS:
            rdf = per_method[method]['result_df']
            rec[f'{method}_anom']  = bool(rdf.at[i, 'isAnom'])
            rec[f'{method}_score'] = float(rdf.at[i, 'norm_score'])
        ensemble_rows.append(rec)

    ens_total = len(ensemble_rows)
    ens_anoms = int(vote_df['ensemble_anom'].sum())

    return {
        "ok":             True,
        "elapsed_ms":     elapsed_ms,
        "group_by":       group_by,
        "method_summaries": method_summaries,
        "ensemble": {
            "total": ens_total,
            "anoms": ens_anoms,
            "rate":  round(ens_anoms / ens_total * 100, 2) if ens_total else 0,
            "rows":  ensemble_rows,
        },
    }


app.mount("/", StaticFiles(directory=BASE_DIR, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    print("=" * 55)
    print("  FDC ML 이상감지 서버 v1.1.0 시작")
    print("  http://localhost:8000  ← 브라우저에서 열기")
    print("  종료: Ctrl+C")
    print("=" * 55)
    # ════════════════════════════════════════════════════════════════
    # [보안수정 H] 서버 바인딩 주소 제한
    #   현재: host="0.0.0.0" → 서버의 모든 네트워크 인터페이스로 외부 노출
    #   수정: 개인 PC 단독 사용 시 → host="127.0.0.1" (localhost만 허용)
    #         팀 내부 공유 시 → host="사내 IP" + 방화벽으로 허가된 IP만 허용
    # ════════════════════════════════════════════════════════════════
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)   # ← [보안수정 H]
