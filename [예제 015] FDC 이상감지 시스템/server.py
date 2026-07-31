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
import warnings
warnings.filterwarnings("ignore")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="FDC ML Anomaly Detection API", version="1.0.0")

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


# ── 요청 모델 ──────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    data: str                          # TSV 텍스트 (헤더 포함)
    col_map: Dict[str, int]            # {date, eqp, lot, ch, ppid, value} → 열 인덱스
    method: str                        # ml_isoforest | ml_lof | ml_ocsvm | ml_elliptic
    threshold: float = 0.05           # 오염률 contamination (0.001 ~ 0.5)
    params: Optional[Dict[str, Any]] = {}


# ── 데이터 파싱 ────────────────────────────────────────────
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


# ── ML 이상감지 ────────────────────────────────────────────
def run_ml(df: pd.DataFrame, method: str, contamination: float, params: dict) -> pd.DataFrame:
    """EQP ID × PPID 그룹별 ML 이상감지 실행"""

    group_cols = [c for c in ['EQP ID', 'PPID'] if c in df.columns]
    groups = df.groupby(group_cols) if group_cols else [('ALL', df)]

    chunks = []
    for _, grp in groups:
        grp = grp.copy()
        vals = grp['REAL DATA'].values.reshape(-1, 1)
        n = len(vals)

        # 샘플 5개 미만 → 이상감지 불가, 전부 정상 처리
        if n < 5:
            grp['isAnom'] = False
            grp['diff']   = 0.0
            grp['score']  = 0.0
            chunks.append(grp)
            continue

        cont = float(np.clip(contamination, 1 / n, 0.5))

        scaler = StandardScaler()
        vs = scaler.fit_transform(vals)

        mean_val = float(np.mean(vals))

        try:
            if method == 'ml_isoforest':
                mdl = IsolationForest(
                    contamination=cont,
                    n_estimators=int(params.get('n_estimators', 100)),
                    random_state=42, n_jobs=-1
                )
                preds  = mdl.fit_predict(vs)
                scores = mdl.score_samples(vs)

            elif method == 'ml_lof':
                k = min(int(params.get('n_neighbors', 20)), n - 1)
                mdl = LocalOutlierFactor(
                    n_neighbors=k,
                    contamination=cont,
                    novelty=False, n_jobs=-1
                )
                preds  = mdl.fit_predict(vs)
                scores = mdl.negative_outlier_factor_

            elif method == 'ml_ocsvm':
                mdl = OneClassSVM(
                    nu=float(np.clip(cont, 1e-4, 0.5)),
                    kernel='rbf', gamma='scale'
                )
                preds  = mdl.fit_predict(vs)
                scores = mdl.score_samples(vs)

            elif method == 'ml_elliptic':
                if n < 10:
                    grp['isAnom'] = False
                    grp['diff']   = 0.0
                    grp['score']  = 0.0
                    chunks.append(grp)
                    continue
                mdl = EllipticEnvelope(contamination=cont, random_state=42)
                preds  = mdl.fit_predict(vs)
                scores = mdl.score_samples(vs)

            else:
                raise ValueError(f"지원하지 않는 방식: {method}")

            # sklearn 규약: -1 = 이상, 1 = 정상
            grp['isAnom'] = preds == -1
            grp['diff']   = np.round(np.abs(vals.flatten() - mean_val), 4)
            grp['score']  = np.round(scores, 6)

        except Exception:
            grp['isAnom'] = False
            grp['diff']   = 0.0
            grp['score']  = 0.0

        chunks.append(grp)

    return pd.concat(chunks, ignore_index=True)


# ── 통계 계산 ──────────────────────────────────────────────
def calc_stats(df: pd.DataFrame) -> List[dict]:
    if 'EQP ID' not in df.columns or 'PPID' not in df.columns:
        return []

    stats = []
    for (eqp, ppid), grp in df.groupby(['EQP ID', 'PPID']):
        vals  = grp['REAL DATA'].values
        anoms = grp['isAnom'].sum()
        stats.append({
            'eqp':   eqp,
            'ppid':  ppid,
            'count': len(grp),
            'anoms': int(anoms),
            'mean':  round(float(np.mean(vals)), 4),
            'std':   round(float(np.std(vals)),  4),
            'min':   round(float(np.min(vals)),  4),
            'max':   round(float(np.max(vals)),  4),
        })
    return stats


# ── 정적 파일 및 루트 ──────────────────────────────────────
@app.get("/")
def root():
    return FileResponse(os.path.join(BASE_DIR, "index.html"))


# ── 엔드포인트 ─────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0", "methods": list(ML_METHOD_LABELS.keys())}


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    if req.method not in ML_METHOD_LABELS:
        raise HTTPException(400, f"지원하지 않는 방식입니다: {req.method}. 사용 가능: {list(ML_METHOD_LABELS)}")

    try:
        df = parse_dataframe(req.data, req.col_map)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if df.empty:
        raise HTTPException(400, "유효한 숫자 데이터가 없습니다.")

    contamination = float(np.clip(req.threshold, 0.001, 0.5))
    result_df = run_ml(df, req.method, contamination, req.params or {})

    # NaN → None (JSON 직렬화)
    result_df = result_df.where(pd.notnull(result_df), other=None)

    records = result_df.drop(columns=['_row_idx'], errors='ignore').to_dict(orient='records')
    stats   = calc_stats(result_df)

    total = len(records)
    anoms = int(result_df['isAnom'].sum())

    return {
        "ok": True,
        "method_label": ML_METHOD_LABELS[req.method],
        "results": records,
        "stats":   stats,
        "summary": {
            "total": total,
            "anoms": anoms,
            "rate":  round(anoms / total * 100, 2) if total else 0,
        }
    }


app.mount("/", StaticFiles(directory=BASE_DIR, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    print("=" * 55)
    print("  FDC ML 이상감지 서버 시작")
    print("  http://localhost:8000  ← 브라우저에서 열기")
    print("  종료: Ctrl+C")
    print("=" * 55)
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
