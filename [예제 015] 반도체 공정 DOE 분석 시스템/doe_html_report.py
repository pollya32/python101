"""
반도체 공정 DOE — 인터랙티브 HTML 리포트 생성기
Plotly 기반 단일 HTML 파일 출력 (서버 불필요)
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import plotly.io as pio
from scipy.interpolate import griddata
import json
import os
from datetime import datetime

# doe_semiconductor 에서 상수 가져오기
from doe_semiconductor import (
    FACTORS, RESPONSES, WAFER_POINTS_25, WAFER_RADIUS, POINT_LABELS,
    run_doe_analysis, generate_excel_template, load_excel_data,
)


# ── 색상 팔레트 ─────────────────────────────────────────────────────────────
COLORS = {
    'primary':  '#1565C0',
    'success':  '#2E7D32',
    'warning':  '#E65100',
    'danger':   '#B71C1C',
    'neutral':  '#546E7A',
    'main_eff': '#1E88E5',
    'interact': '#E53935',
}


# ══════════════════════════════════════════════════════════════════════════════
#  개별 차트 생성 함수
# ══════════════════════════════════════════════════════════════════════════════

def _fig_design_matrix(design_df: pd.DataFrame, factors: list) -> go.Figure:
    coded = [f for f in factors if f in design_df.columns]
    data = design_df[coded].values
    run_labels = [f'Run {int(r)}' for r in design_df.get('Run', range(1, len(design_df)+1))]
    fig = go.Figure(go.Heatmap(
        z=data, x=coded, y=run_labels,
        colorscale=[[0,'#D32F2F'],[0.5,'#FFF9C4'],[1,'#388E3C']],
        zmid=0, zmin=-1.5, zmax=1.5,
        text=[[f'{v:+.0f}' for v in row] for row in data],
        texttemplate='%{text}', textfont={'size': 10},
        colorbar=dict(title='Coded Level'),
    ))
    fig.update_layout(
        title='실험 설계 행렬 (Coded Levels)',
        xaxis_title='Factor', yaxis_title='Run',
        height=max(300, len(design_df) * 28 + 80),
        yaxis=dict(autorange='reversed'),
    )
    return fig


def _fig_main_effects(result_df: pd.DataFrame, factors: list,
                       response: str) -> go.Figure:
    valid = [f for f in factors if f in result_df.columns]
    ncols = min(5, len(valid))
    nrows = (len(valid) + ncols - 1) // ncols
    fig = make_subplots(rows=nrows, cols=ncols,
                        subplot_titles=valid,
                        shared_yaxes=False)
    overall_mean = result_df[response].mean()
    for i, f in enumerate(valid):
        r, c = divmod(i, ncols)
        means = result_df.groupby(f)[response].mean()
        fig.add_trace(go.Scatter(
            x=list(means.index), y=list(means.values),
            mode='lines+markers',
            marker=dict(size=8, color=COLORS['main_eff']),
            line=dict(width=2, color=COLORS['main_eff']),
            name=f, showlegend=False,
        ), row=r+1, col=c+1)
        fig.add_hline(y=overall_mean, line_dash='dash',
                      line_color='red', opacity=0.5,
                      row=r+1, col=c+1)
    fig.update_layout(
        title=f'주효과 도표 — {response}',
        height=nrows * 220 + 60,
    )
    return fig


def _fig_interactions(result_df: pd.DataFrame, factors: list,
                       response: str, top_n: int = 6) -> go.Figure:
    from itertools import combinations
    pairs = [(a, b) for a, b in combinations(factors, 2)
             if a in result_df.columns and b in result_df.columns][:top_n]
    if not pairs:
        return go.Figure()
    ncols = min(3, len(pairs))
    nrows = (len(pairs) + ncols - 1) // ncols
    titles = [f'{a} × {b}' for a, b in pairs]
    fig = make_subplots(rows=nrows, cols=ncols, subplot_titles=titles)
    palette = [COLORS['primary'], COLORS['danger'],
               COLORS['success'], COLORS['warning']]
    for idx, (a, b) in enumerate(pairs):
        r, c = divmod(idx, ncols)
        b_vals = sorted(result_df[b].unique())
        for ci, bv in enumerate(b_vals):
            sub = result_df[result_df[b] == bv]
            means = sub.groupby(a)[response].mean()
            fig.add_trace(go.Scatter(
                x=list(means.index), y=list(means.values),
                mode='lines+markers',
                name=f'{b}={bv:+.0f}',
                line=dict(color=palette[ci % len(palette)], width=2),
                marker=dict(size=7),
                showlegend=(idx == 0),
                legendgroup=f'b{ci}',
            ), row=r+1, col=c+1)
    fig.update_layout(
        title=f'교호작용 도표 — {response}',
        height=nrows * 250 + 80,
    )
    return fig


def _fig_pareto(pareto_df: pd.DataFrame, response: str) -> go.Figure:
    if pareto_df.empty:
        return go.Figure()
    top = pareto_df.head(15)
    colors = [COLORS['danger'] if t == 'Main' else COLORS['primary']
              for t in top['Type']]
    fig = make_subplots(specs=[[{'secondary_y': True}]])
    fig.add_trace(go.Bar(
        x=top['Term'], y=top['Effect'],
        marker_color=colors, name='|Effect|',
        text=[f'{v:.1f}' for v in top['Effect']],
        textposition='outside',
    ), secondary_y=False)
    fig.add_trace(go.Scatter(
        x=top['Term'], y=top['Cumulative%'],
        mode='lines+markers', name='Cumulative %',
        line=dict(color='black', width=2),
        marker=dict(size=6),
    ), secondary_y=True)
    fig.add_hline(y=80, line_dash='dash', line_color='gray',
                  secondary_y=True, opacity=0.6)
    fig.update_layout(
        title=f'파레토 차트 — {response}',
        xaxis_tickangle=-40,
        legend=dict(orientation='h', y=1.1),
        height=420,
    )
    fig.update_yaxes(title_text='|Effect|', secondary_y=False)
    fig.update_yaxes(title_text='Cumulative %', secondary_y=True,
                     range=[0, 105])
    return fig


def _fig_wafer_map(result_df: pd.DataFrame, run_row: pd.Series,
                    vmin: float, vmax: float) -> go.Figure:
    pt_cols = [f'T_{lbl}' for lbl in POINT_LABELS]
    vals = run_row[pt_cols].values.astype(float)
    pts  = np.array(WAFER_POINTS_25)

    xi = np.linspace(-WAFER_RADIUS, WAFER_RADIUS, 150)
    yi = np.linspace(-WAFER_RADIUS, WAFER_RADIUS, 150)
    XI, YI = np.meshgrid(xi, yi)
    try:
        ZI = griddata(pts, vals, (XI, YI), method='cubic')
    except Exception:
        ZI = griddata(pts, vals, (XI, YI), method='nearest')
    mask = XI**2 + YI**2 > WAFER_RADIUS**2
    ZI[mask] = None

    run_no = int(run_row.get('Run', '?'))
    unif   = (vals.max() - vals.min()) / (2 * vals.mean() + 1e-9) * 100

    fig = go.Figure()
    # 보간된 컬러맵
    fig.add_trace(go.Heatmap(
        z=ZI, x=xi, y=yi,
        colorscale='RdYlGn',
        zmin=vmin, zmax=vmax,
        colorbar=dict(title='Thickness (Å)', thickness=12),
        hoverinfo='skip',
    ))
    # 25포인트 산점도
    fig.add_trace(go.Scatter(
        x=pts[:, 0], y=pts[:, 1],
        mode='markers+text',
        marker=dict(color=vals, colorscale='RdYlGn',
                    cmin=vmin, cmax=vmax,
                    size=14, line=dict(color='black', width=1)),
        text=[f'{v:.0f}' for v in vals],
        textposition='top center',
        textfont=dict(size=8),
        name='Measurement',
        hovertemplate='%{text} Å<extra></extra>',
    ))
    # 웨이퍼 외곽선
    theta_c = np.linspace(0, 2*np.pi, 200)
    fig.add_trace(go.Scatter(
        x=WAFER_RADIUS*np.cos(theta_c),
        y=WAFER_RADIUS*np.sin(theta_c),
        mode='lines', line=dict(color='black', width=2),
        showlegend=False, hoverinfo='skip',
    ))
    fig.update_layout(
        title=f'Run {run_no} — Mean={vals.mean():.0f} Å  UNI={unif:.2f}%',
        xaxis=dict(title='X (mm)', scaleanchor='y', range=[-160, 160]),
        yaxis=dict(title='Y (mm)', range=[-160, 160]),
        height=420, width=420,
        showlegend=False,
        plot_bgcolor='#ECEFF1',
    )
    return fig


def _fig_wafer_stat_map(result_df: pd.DataFrame,
                         stat: str = 'mean') -> go.Figure:
    pt_cols = [f'T_{lbl}' for lbl in POINT_LABELS]
    if stat == 'mean':
        vals = result_df[pt_cols].mean().values
        title = '25포인트 평균 두께 (Å)'
        cscale = 'RdYlGn'
    elif stat == 'std':
        vals = result_df[pt_cols].std().values
        title = '25포인트 1σ (Å)'
        cscale = 'YlOrRd'
    else:
        m = result_df[pt_cols].mean().values
        s = result_df[pt_cols].std().values
        vals = s / (m + 1e-9) * 100
        title = '25포인트 CoV (%)'
        cscale = 'YlOrRd'

    pts = np.array(WAFER_POINTS_25)
    xi  = np.linspace(-WAFER_RADIUS, WAFER_RADIUS, 150)
    yi  = np.linspace(-WAFER_RADIUS, WAFER_RADIUS, 150)
    XI, YI = np.meshgrid(xi, yi)
    try:
        ZI = griddata(pts, vals, (XI, YI), method='cubic')
    except Exception:
        ZI = griddata(pts, vals, (XI, YI), method='nearest')
    mask = XI**2 + YI**2 > WAFER_RADIUS**2
    ZI[mask] = None

    fig = go.Figure()
    fig.add_trace(go.Heatmap(
        z=ZI, x=xi, y=yi,
        colorscale=cscale,
        colorbar=dict(title=title, thickness=12),
        hoverinfo='skip',
    ))
    fig.add_trace(go.Scatter(
        x=pts[:, 0], y=pts[:, 1],
        mode='markers+text',
        marker=dict(color=vals, colorscale=cscale, size=14,
                    line=dict(color='black', width=1)),
        text=[f'{v:.1f}' for v in vals],
        textposition='top center',
        textfont=dict(size=8),
        hovertemplate='%{text}<extra></extra>',
        showlegend=False,
    ))
    theta_c = np.linspace(0, 2*np.pi, 200)
    fig.add_trace(go.Scatter(
        x=WAFER_RADIUS*np.cos(theta_c), y=WAFER_RADIUS*np.sin(theta_c),
        mode='lines', line=dict(color='black', width=2),
        showlegend=False, hoverinfo='skip',
    ))
    fig.update_layout(
        title=title,
        xaxis=dict(title='X (mm)', scaleanchor='y', range=[-160, 160]),
        yaxis=dict(title='Y (mm)', range=[-160, 160]),
        height=380, width=380,
        plot_bgcolor='#ECEFF1',
    )
    return fig


def _fig_response_box(result_df: pd.DataFrame,
                       responses: list) -> go.Figure:
    fig = go.Figure()
    for resp in responses:
        if resp not in result_df.columns:
            continue
        unit = RESPONSES.get(resp, {}).get('unit', '')
        fig.add_trace(go.Box(
            y=result_df[resp], name=f'{resp} ({unit})',
            boxpoints='all', jitter=0.3,
            marker=dict(size=6),
        ))
    fig.update_layout(
        title='응답 분포 (Box Plot)',
        yaxis_title='Value',
        height=380,
    )
    return fig


def _fig_optimization(opt_result: dict) -> go.Figure:
    preds   = opt_result['predicted_responses']
    params  = opt_result['optimal_factors']
    targets = {r: RESPONSES[r]['target'] for r in preds}

    resp_names = list(preds.keys())
    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=['예측값 vs 타겟', '최적 인자 설정값 (정규화 %)'],
    )
    # 왼쪽: 타겟 vs 예측
    x = list(range(len(resp_names)))
    fig.add_trace(go.Bar(
        x=resp_names, y=[targets[r] for r in resp_names],
        name='Target', marker_color=COLORS['success'], opacity=0.8,
    ), row=1, col=1)
    fig.add_trace(go.Bar(
        x=resp_names, y=[preds[r] for r in resp_names],
        name='Predicted', marker_color=COLORS['primary'], opacity=0.8,
    ), row=1, col=1)
    # 오른쪽: 인자 정규화
    factor_names, norm_vals, colors_f = [], [], []
    for f, v in params.items():
        if f in FACTORS:
            lo, hi = FACTORS[f]['low'], FACTORS[f]['high']
            nv = (v - lo) / (hi - lo + 1e-9) * 100
            color = COLORS['danger'] if FACTORS[f]['type'] == 'gas' \
                    else COLORS['primary']
        else:
            nv, color = 50.0, COLORS['neutral']
        factor_names.append(f)
        norm_vals.append(nv)
        colors_f.append(color)
    fig.add_trace(go.Bar(
        x=norm_vals, y=factor_names,
        orientation='h',
        marker_color=colors_f,
        text=[f'{v:.1f} {FACTORS.get(f,{}).get("unit","")}'
              for f, v in params.items()],
        textposition='outside',
        showlegend=False,
    ), row=1, col=2)
    fig.add_vline(x=50, line_dash='dash', line_color='gray',
                  opacity=0.5, row=1, col=2)
    fig.update_layout(
        title=f'최적화 결과 (Desirability = {opt_result["desirability"]:.4f})',
        height=420,
        barmode='group',
        xaxis2=dict(range=[0, 115], title='Normalized (%)'),
    )
    return fig


def _fig_correlation(result_df: pd.DataFrame,
                      factors: list, responses: list) -> go.Figure:
    cols = [c for c in factors + responses if c in result_df.columns]
    corr = result_df[cols].corr().round(2)
    fig = go.Figure(go.Heatmap(
        z=corr.values, x=corr.columns.tolist(), y=corr.index.tolist(),
        colorscale='RdBu', zmid=0, zmin=-1, zmax=1,
        text=corr.values.round(2),
        texttemplate='%{text}', textfont={'size': 9},
        colorbar=dict(title='r'),
    ))
    fig.update_layout(
        title='상관관계 히트맵',
        height=max(350, len(cols) * 30 + 80),
        yaxis=dict(autorange='reversed'),
    )
    return fig


def _fig_uniformity_radar(result_df: pd.DataFrame) -> go.Figure:
    """런별 25포인트 두께 레이더 차트 (상위/하위 런 비교)"""
    pt_cols = [f'T_{lbl}' for lbl in POINT_LABELS]
    if not all(c in result_df.columns for c in pt_cols):
        return go.Figure()
    if 'UNIFORMITY' not in result_df.columns:
        return go.Figure()

    best_idx  = result_df['UNIFORMITY'].idxmin()
    worst_idx = result_df['UNIFORMITY'].idxmax()
    labels    = POINT_LABELS + [POINT_LABELS[0]]

    fig = go.Figure()
    for ridx, name, color in [
        (best_idx,  f'Best  Run{int(result_df.loc[best_idx,"Run"])}', COLORS['success']),
        (worst_idx, f'Worst Run{int(result_df.loc[worst_idx,"Run"])}', COLORS['danger']),
    ]:
        vals_row = result_df.loc[ridx, pt_cols].values.tolist()
        vals_row += [vals_row[0]]
        fig.add_trace(go.Scatterpolar(
            r=vals_row, theta=labels,
            fill='toself', name=name,
            line_color=color, opacity=0.7,
        ))
    fig.update_layout(
        title='Best vs Worst 런 — 25포인트 레이더 차트',
        polar=dict(radialaxis=dict(visible=True)),
        height=420,
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
#  HTML 리포트 빌더
# ══════════════════════════════════════════════════════════════════════════════

def _fig_to_div(fig: go.Figure, div_id: str = '', full_html: bool = False) -> str:
    return pio.to_html(fig, include_plotlyjs=False,
                       full_html=False, div_id=div_id or None)


def generate_html_report(
    result_df: pd.DataFrame,
    design_df: pd.DataFrame,
    analysis_results: dict,
    opt_result: dict,
    output_path: str = 'doe_results/doe_report.html',
    design_type: str = '',
) -> str:
    """
    DOE 분석 결과를 인터랙티브 HTML 리포트로 출력.
    단일 파일로 생성되므로 서버 없이 브라우저에서 바로 열 수 있음.
    """
    used_factors = [f for f in FACTORS if f in result_df.columns
                    or any(f in str(c) for c in result_df.columns)]
    used_factors = [f for f in FACTORS
                    if f in result_df.columns]
    resp_list = [r for r in RESPONSES if r in result_df.columns]
    pt_cols   = [f'T_{lbl}' for lbl in POINT_LABELS
                 if f'T_{lbl}' in result_df.columns]
    has_wafer = len(pt_cols) == 25

    # ── 차트 생성 ─────────────────────────────────────────────────────────
    divs = {}
    divs['design_matrix'] = _fig_to_div(
        _fig_design_matrix(design_df, used_factors), 'design_matrix')
    divs['response_box'] = _fig_to_div(
        _fig_response_box(result_df, resp_list), 'response_box')
    divs['correlation'] = _fig_to_div(
        _fig_correlation(result_df, used_factors, resp_list), 'correlation')
    divs['optimization'] = _fig_to_div(
        _fig_optimization(opt_result), 'optimization')

    for resp in resp_list:
        ar = analysis_results.get(resp, {})
        divs[f'me_{resp}'] = _fig_to_div(
            _fig_main_effects(result_df, used_factors, resp), f'me_{resp}')
        divs[f'ia_{resp}'] = _fig_to_div(
            _fig_interactions(result_df, used_factors, resp), f'ia_{resp}')
        pareto = ar.get('pareto', pd.DataFrame())
        divs[f'pareto_{resp}'] = _fig_to_div(
            _fig_pareto(pareto, resp), f'pareto_{resp}')

    # 웨이퍼 맵
    if has_wafer:
        all_vals = result_df[pt_cols].values.flatten()
        vmin = float(np.nanpercentile(all_vals, 2))
        vmax = float(np.nanpercentile(all_vals, 98))
        wafer_run_divs = []
        for _, row in result_df.iterrows():
            fig_w = _fig_wafer_map(result_df, row, vmin, vmax)
            wafer_run_divs.append(_fig_to_div(fig_w))
        divs['wafer_mean'] = _fig_to_div(
            _fig_wafer_stat_map(result_df, 'mean'), 'wafer_mean')
        divs['wafer_std']  = _fig_to_div(
            _fig_wafer_stat_map(result_df, 'std'),  'wafer_std')
        divs['wafer_cov']  = _fig_to_div(
            _fig_wafer_stat_map(result_df, 'cov'),  'wafer_cov')
        divs['wafer_radar'] = _fig_to_div(
            _fig_uniformity_radar(result_df), 'wafer_radar')

    # ── 요약 카드 데이터 ──────────────────────────────────────────────────
    n_runs   = len(result_df)
    n_factors = len(used_factors)
    desirability = opt_result['desirability']

    summary_cards = ''
    for resp in resp_list:
        col = result_df[resp]
        target = RESPONSES[resp]['target']
        unit   = RESPONSES[resp]['unit']
        pred   = opt_result['predicted_responses'].get(resp, 0)
        summary_cards += f'''
        <div class="card">
          <div class="card-title">{resp}</div>
          <div class="card-value">{col.mean():.2f} <span class="card-unit">{unit}</span></div>
          <div class="card-sub">평균값 (전체 런)</div>
          <div class="card-sub">Target: {target} | Pred: {pred:.2f}</div>
        </div>'''

    opt_table_rows = ''
    for f, v in opt_result['optimal_factors'].items():
        unit = FACTORS.get(f, {}).get('unit', '')
        ftype = FACTORS.get(f, {}).get('type', '')
        badge = f'<span class="badge badge-gas">GAS</span>' if ftype=='gas' \
                else f'<span class="badge badge-proc">PROCESS</span>'
        opt_table_rows += f'''
          <tr>
            <td>{badge} {f}</td>
            <td><b>{v:.2f}</b> {unit}</td>
          </tr>'''

    pred_table_rows = ''
    for r, v in opt_result['predicted_responses'].items():
        unit   = RESPONSES.get(r, {}).get('unit', '')
        target = RESPONSES.get(r, {}).get('target', '')
        err    = abs(v - float(target)) / (float(target)+1e-9) * 100
        pred_table_rows += f'''
          <tr>
            <td>{r}</td>
            <td><b>{v:.2f}</b> {unit}</td>
            <td>{target} {unit}</td>
            <td>±{err:.1f}%</td>
          </tr>'''

    # ── 반응별 분석 섹션 ──────────────────────────────────────────────────
    response_sections = ''
    for resp in resp_list:
        ar   = analysis_results.get(resp, {})
        me   = ar.get('main_effects', pd.DataFrame())
        ie   = ar.get('interactions', pd.DataFrame())
        reg  = ar.get('regression', {})
        r2   = reg.get('r2', 0)

        sig_me = me[me['Significant']]['Factor'].tolist() if not me.empty else []
        top_ia = ie.head(3)[['Factors','Interaction_Effect']].to_dict('records') \
                 if not ie.empty else []

        sig_html = (', '.join(f'<b>{f}</b>' for f in sig_me)
                    if sig_me else '<i>없음</i>')
        ia_html  = ''.join(
            f'<li>{r["Factors"]}: {r["Interaction_Effect"]:+.3f}</li>'
            for r in top_ia
        )
        response_sections += f'''
        <!-- ═══ {resp} 탭 콘텐츠 ═══ -->
        <div class="tab-content" id="tab-{resp}" style="display:none;">
          <div class="stat-row">
            <div class="stat-box">
              <div class="stat-label">회귀 R²</div>
              <div class="stat-val">{r2:.4f}</div>
            </div>
            <div class="stat-box">
              <div class="stat-label">유의한 주효과</div>
              <div class="stat-val">{len(sig_me)}개</div>
            </div>
            <div class="stat-box">
              <div class="stat-label">교호작용 수</div>
              <div class="stat-val">{len(ie)}개</div>
            </div>
          </div>
          <p>유의한 주효과 (p&lt;0.05): {sig_html}</p>
          <details open><summary>주요 교호작용 Top3</summary><ul>{ia_html}</ul></details>
          <h3>주효과 도표</h3>
          {divs[f"me_{resp}"]}
          <h3>교호작용 도표</h3>
          {divs[f"ia_{resp}"]}
          <h3>파레토 차트</h3>
          {divs[f"pareto_{resp}"]}
        </div>'''

    # ── 웨이퍼 맵 섹션 ────────────────────────────────────────────────────
    wafer_section = ''
    if has_wafer:
        run_map_grid = '<div class="wafer-grid">' + \
                       ''.join(f'<div class="wafer-cell">{d}</div>'
                               for d in wafer_run_divs) + \
                       '</div>'
        wafer_section = f'''
        <section id="wafer">
          <h2>🔵 300mm 웨이퍼 두께 맵 (25포인트)</h2>

          <h3>런별 두께 맵</h3>
          {run_map_grid}

          <h3>전체 런 통계</h3>
          <div class="wafer-grid-3">
            <div>{divs["wafer_mean"]}</div>
            <div>{divs["wafer_std"]}</div>
            <div>{divs["wafer_cov"]}</div>
          </div>

          <h3>Best vs Worst 레이더 차트</h3>
          {divs["wafer_radar"]}
        </section>'''

    # ── 탭 버튼 생성 ──────────────────────────────────────────────────────
    tab_buttons = ''.join(
        f'<button class="tab-btn" onclick="showTab(\'{resp}\')">{resp}</button>'
        for resp in resp_list
    )

    # ── Plotly.js CDN URL ─────────────────────────────────────────────────
    plotly_cdn = 'https://cdn.plot.ly/plotly-latest.min.js'

    # ── HTML 조립 ─────────────────────────────────────────────────────────
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    html = f'''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>반도체 공정 DOE 분석 리포트</title>
<script src="{plotly_cdn}"></script>
<style>
  :root {{
    --primary: #1565C0; --success: #2E7D32;
    --bg: #F5F7FA; --card-bg: #FFFFFF;
    --text: #263238; --muted: #607D8B;
    --border: #CFD8DC; --radius: 8px;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', Arial, sans-serif;
         background: var(--bg); color: var(--text); }}

  /* ── 헤더 ── */
  .header {{
    background: linear-gradient(135deg, #1565C0 0%, #0D47A1 100%);
    color: white; padding: 32px 40px; margin-bottom: 28px;
  }}
  .header h1 {{ font-size: 1.9rem; font-weight: 700; }}
  .header .meta {{ font-size: 0.85rem; opacity: 0.8; margin-top: 8px; }}
  .header .badge-design {{
    display: inline-block; background: rgba(255,255,255,0.2);
    border-radius: 4px; padding: 2px 10px; font-size: 0.8rem; margin-top: 6px;
  }}

  /* ── 레이아웃 ── */
  .container {{ max-width: 1400px; margin: 0 auto; padding: 0 24px 48px; }}
  section {{ background: var(--card-bg); border-radius: var(--radius);
             box-shadow: 0 2px 8px rgba(0,0,0,0.07);
             padding: 28px 32px; margin-bottom: 24px; }}
  h2 {{ font-size: 1.25rem; color: var(--primary); border-bottom: 2px solid var(--primary);
        padding-bottom: 10px; margin-bottom: 20px; }}
  h3 {{ font-size: 1.05rem; color: var(--text); margin: 20px 0 10px; }}

  /* ── 요약 카드 ── */
  .cards {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }}
  .card {{
    flex: 1; min-width: 180px; background: var(--card-bg);
    border-radius: var(--radius); padding: 20px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    border-top: 4px solid var(--primary);
  }}
  .card-title {{ font-size: 0.8rem; color: var(--muted); text-transform: uppercase; }}
  .card-value {{ font-size: 1.8rem; font-weight: 700; color: var(--primary); margin: 6px 0 2px; }}
  .card-unit  {{ font-size: 1rem; color: var(--muted); }}
  .card-sub   {{ font-size: 0.78rem; color: var(--muted); }}

  /* ── 통계 박스 ── */
  .stat-row {{ display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }}
  .stat-box {{
    flex: 1; min-width: 120px; text-align: center;
    background: #EEF2F8; border-radius: 6px; padding: 14px 10px;
  }}
  .stat-label {{ font-size: 0.75rem; color: var(--muted); }}
  .stat-val   {{ font-size: 1.5rem; font-weight: 700; color: var(--primary); }}

  /* ── 탭 ── */
  .tab-btns {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 16px; }}
  .tab-btn {{
    padding: 7px 18px; border-radius: 20px; border: 2px solid var(--primary);
    background: transparent; color: var(--primary); cursor: pointer;
    font-size: 0.85rem; font-weight: 600; transition: all 0.2s;
  }}
  .tab-btn:hover, .tab-btn.active {{
    background: var(--primary); color: white;
  }}

  /* ── 테이블 ── */
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th {{ background: var(--primary); color: white; padding: 9px 12px; text-align: left; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid var(--border); }}
  tr:hover td {{ background: #EEF2F8; }}
  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
  @media (max-width: 768px) {{ .two-col {{ grid-template-columns: 1fr; }} }}

  /* ── 뱃지 ── */
  .badge {{ display: inline-block; border-radius: 3px; padding: 1px 7px;
            font-size: 0.72rem; font-weight: 700; }}
  .badge-gas  {{ background: #FFEBEE; color: #C62828; }}
  .badge-proc {{ background: #E3F2FD; color: #1565C0; }}

  /* ── 웨이퍼 그리드 ── */
  .wafer-grid   {{ display: flex; flex-wrap: wrap; gap: 12px; }}
  .wafer-cell   {{ min-width: 320px; flex: 1; }}
  .wafer-grid-3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }}
  @media (max-width: 900px) {{ .wafer-grid-3 {{ grid-template-columns: 1fr; }} }}

  /* ── 데시라빌리티 바 ── */
  .desirability-bar {{
    height: 24px; border-radius: 12px; background: #E0E0E0;
    overflow: hidden; margin: 10px 0;
  }}
  .desirability-fill {{
    height: 100%; border-radius: 12px;
    background: linear-gradient(90deg, #EF5350, #FFCA28, #66BB6A);
    transition: width 0.8s ease;
  }}

  /* ── 좌표 테이블 ── */
  .point-table {{ font-size: 0.8rem; }}
  .point-table td, .point-table th {{ padding: 5px 8px; }}

  footer {{ text-align: center; padding: 24px; color: var(--muted); font-size: 0.8rem; }}
</style>
</head>
<body>

<!-- ═══ 헤더 ═══ -->
<div class="header">
  <h1>반도체 공정 DOE 분석 리포트</h1>
  <div class="meta">생성 일시: {timestamp}</div>
  <div class="meta">300mm 웨이퍼 · 25포인트 계측 기준</div>
  <span class="badge-design">{design_type.upper()} 설계 · {n_runs} Runs · {n_factors} Factors</span>
</div>

<div class="container">

<!-- ═══ 요약 카드 ═══ -->
<div class="cards">
  <div class="card">
    <div class="card-title">총 실험 횟수</div>
    <div class="card-value">{n_runs} <span class="card-unit">Runs</span></div>
    <div class="card-sub">{design_type}</div>
  </div>
  <div class="card">
    <div class="card-title">인자 수</div>
    <div class="card-value">{n_factors} <span class="card-unit">개</span></div>
    <div class="card-sub">Gas + Process</div>
  </div>
  <div class="card">
    <div class="card-title">계측 포인트</div>
    <div class="card-value">25 <span class="card-unit">pt</span></div>
    <div class="card-sub">300mm wafer</div>
  </div>
  <div class="card">
    <div class="card-title">Desirability</div>
    <div class="card-value">{desirability:.4f}</div>
    <div class="desirability-bar">
      <div class="desirability-fill" style="width:{desirability*100:.1f}%"></div>
    </div>
  </div>
  {summary_cards}
</div>

<!-- ═══ 1. 실험 설계 ═══ -->
<section id="design">
  <h2>📋 실험 설계</h2>
  {divs["design_matrix"]}
</section>

<!-- ═══ 2. 응답 분포 ═══ -->
<section id="dist">
  <h2>📊 응답 분포</h2>
  <div class="two-col">
    <div>{divs["response_box"]}</div>
    <div>{divs["correlation"]}</div>
  </div>
</section>

<!-- ═══ 3. 반응별 분석 ═══ -->
<section id="analysis">
  <h2>🔬 반응별 통계 분석</h2>
  <div class="tab-btns">
    {tab_buttons}
  </div>
  {response_sections}
</section>

<!-- ═══ 4. 웨이퍼 맵 ═══ -->
{wafer_section}

<!-- ═══ 5. 25포인트 좌표 ═══ -->
<section id="coords">
  <h2>📍 25포인트 계측 좌표</h2>
  <table class="point-table">
    <thead>
      <tr><th>Point</th><th>X (mm)</th><th>Y (mm)</th>
          <th>Radius (mm)</th><th>Angle (°)</th></tr>
    </thead>
    <tbody>
      {''.join(
          f'<tr><td><b>{lbl}</b></td>'
          f'<td>{x:.1f}</td><td>{y:.1f}</td>'
          f'<td>{np.sqrt(x**2+y**2):.1f}</td>'
          f'<td>{np.degrees(np.arctan2(y,x)):.1f}</td></tr>'
          for lbl,(x,y) in zip(POINT_LABELS, WAFER_POINTS_25)
      )}
    </tbody>
  </table>
</section>

<!-- ═══ 6. 최적화 결과 ═══ -->
<section id="optimization">
  <h2>🎯 최적화 결과</h2>
  {divs["optimization"]}
  <div class="two-col" style="margin-top:24px;">
    <div>
      <h3>최적 인자 조건</h3>
      <table>
        <thead><tr><th>Factor</th><th>Optimal Value</th></tr></thead>
        <tbody>{opt_table_rows}</tbody>
      </table>
    </div>
    <div>
      <h3>예측 응답값</h3>
      <table>
        <thead><tr><th>Response</th><th>Predicted</th><th>Target</th><th>Error</th></tr></thead>
        <tbody>{pred_table_rows}</tbody>
      </table>
    </div>
  </div>
</section>

</div><!-- /container -->

<footer>
  반도체 공정 DOE 분석 시스템 · 300mm 웨이퍼 25포인트 기준 · {timestamp}
</footer>

<script>
// 탭 전환
function showTab(resp) {{
  document.querySelectorAll('.tab-content').forEach(el => el.style.display = 'none');
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  const el = document.getElementById('tab-' + resp);
  if (el) {{ el.style.display = 'block'; }}
  event.target.classList.add('active');
}}
// 첫 번째 탭 기본 열기
(function() {{
  const first = document.querySelector('.tab-btn');
  if (first) {{ first.click(); }}
}})();
</script>

</body>
</html>'''

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'  HTML 리포트: {output_path}')
    return output_path


# ══════════════════════════════════════════════════════════════════════════════
#  단독 실행 엔트리포인트
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='DOE HTML 리포트 생성')
    parser.add_argument('--design',
        choices=['full_factorial','ccd','box_behnken','plackett_burman'],
        default='plackett_burman')
    parser.add_argument('--data', default=None,
        help='측정 데이터 파일 (.csv 또는 .xlsx)')
    parser.add_argument('--template', action='store_true',
        help='Excel 입력 템플릿만 생성 후 종료')
    parser.add_argument('--output', default='doe_results')
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    if args.template:
        tpl = os.path.join(args.output, 'doe_input_template.xlsx')
        generate_excel_template(design_type=args.design, output_path=tpl)
        print(f'\n사용법:')
        print(f'  1. {tpl} 파일을 엑셀로 열기')
        print(f'  2. 노란색 셀에 측정값 입력 후 저장')
        print(f'  3. python3 doe_html_report.py --data {tpl}')
        import sys; sys.exit(0)

    # xlsx 로드
    data_path = args.data
    if data_path and data_path.endswith('.xlsx'):
        print(f'  Excel 데이터 로드: {data_path}')
        loaded_df = load_excel_data(data_path)
        tmp_csv = data_path.replace('.xlsx', '_loaded.csv')
        loaded_df.to_csv(tmp_csv, index=False)
        data_path = tmp_csv

    print('DOE 분석 실행 중...')
    result_df, analysis_results, opt_result = run_doe_analysis(
        design_type=args.design,
        data_csv=data_path,
        output_dir=args.output,
    )

    # design_df 재생성 (run_doe_analysis 내부와 동일)
    from doe_semiconductor import DOEDesigner
    designer = DOEDesigner(FACTORS, list(FACTORS.keys()))
    design_map = {
        'full_factorial': designer.full_factorial_2level,
        'ccd': designer.central_composite,
        'box_behnken': designer.box_behnken,
        'plackett_burman': designer.plackett_burman,
    }
    design_df = design_map[args.design]()

    out_html = os.path.join(args.output, 'doe_report.html')
    generate_html_report(
        result_df, design_df, analysis_results, opt_result,
        output_path=out_html,
        design_type=args.design,
    )
    print(f'\n브라우저에서 열기: {out_html}')
