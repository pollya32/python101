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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
class RunScriptRequest(BaseModel):
    script: str
    timeout: Optional[int] = 30


class AnalyzeRequest(BaseModel):
    data: str
    col_map: Dict[str, int]
    method: str
    threshold: float = 0.05
    params: Optional[Dict[str, Any]] = {}
    group_by: Optional[str] = "eqp_ppid"   # ④ 그룹화 기준


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
    """④ 그룹화 기준에 따른 컬럼 목록 반환"""
    wanted = GROUP_BY_COLS.get(group_by, GROUP_BY_COLS["eqp_ppid"])
    return [c for c in wanted if c in df.columns]


def _normalize_score(scores: np.ndarray) -> np.ndarray:
    """③ 점수 정규화: 모든 방식을 0(정상)~1(이상) 통일
    sklearn 공통 규약: 낮을수록 이상 → 반전하여 높을수록 이상이 되도록 변환"""
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

        # 샘플 부족 → 전부 정상 처리
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
            grp['norm_score'] = _normalize_score(scores)   # ③ 정규화 점수

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


# ── 엔드포인트 ─────────────────────────────────────────────────
@app.post("/run-script")
def run_script(req: RunScriptRequest):
    """사용자 Python 스크립트 실행 → DataFrame 반환 (로컬 전용)"""
    ns: dict = {"pd": pd, "np": np, "io": io, "os": os}

    for lib in ("sqlite3", "cx_Oracle", "pymysql", "psycopg2", "pyodbc",
                "requests", "json", "re", "datetime"):
        try:
            ns[lib] = __import__(lib)
        except ImportError:
            pass

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

    # 앙상블: 인덱스별로 방식들의 isAnom 투표
    vote_df = df[['_row_idx']].copy()
    for method, d in per_method.items():
        vote_df[method] = d['result_df']['isAnom'].values

    vote_cols = list(ML_METHOD_LABELS.keys())
    vote_df['vote_count']    = vote_df[vote_cols].sum(axis=1)
    vote_df['ensemble_anom'] = vote_df['vote_count'] >= 2   # ≥2개 방식 동의

    # 각 방식별 요약
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

    # 앙상블 결과 레코드 (index + vote_count + ensemble_anom + 각 방식 score)
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
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
