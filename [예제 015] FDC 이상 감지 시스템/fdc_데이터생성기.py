"""
FDC 샘플 데이터 생성기
실제 반도체 설비 FDC 센서 데이터를 시뮬레이션하여 CSV로 생성합니다.

이상 시나리오:
  pressure_drift  - 압력 드리프트 (APC Valve 오염)
  rf_instability  - RF 출력 불안정 (매칭 이상)
  temp_alarm      - ESC 온도 이상 (히터 이상)
  multi_sensor    - 복합 센서 이상 (Pump 성능 저하)
  normal          - 정상 데이터

사용법:
  python fdc_데이터생성기.py
  python fdc_데이터생성기.py --anomaly rf_instability --points 300
"""

import argparse
from datetime import datetime, timedelta

import numpy as np
import pandas as pd


# 센서 기준값 정의
SENSOR_NOMINAL = {
    "Chamber_Pressure": 2.000,   # Torr
    "RF_Forward_Power": 1000.0,  # W
    "RF_Reflected_Power": 8.0,   # W
    "ESC_Temperature": 20.0,     # °C
    "Gas_Flow_MFC": 100.0,       # sccm
    "APC_Valve": 65.0,           # %
    "Pump_Speed": 3000.0,        # RPM
}

SENSOR_NOISE = {
    "Chamber_Pressure": 0.003,
    "RF_Forward_Power": 2.0,
    "RF_Reflected_Power": 1.5,
    "ESC_Temperature": 0.5,
    "Gas_Flow_MFC": 0.5,
    "APC_Valve": 1.0,
    "Pump_Speed": 20.0,
}


def generate_fdc_data(
    n_points: int = 200,
    anomaly_type: str = "pressure_drift",
    output_path: str = "fdc_sample_data.csv",
    seed: int = 42,
) -> str:
    np.random.seed(seed)

    start_time = datetime(2024, 1, 15, 8, 0, 0)
    timestamps = [start_time + timedelta(minutes=5 * i) for i in range(n_points)]

    data = {
        "Timestamp": timestamps,
        "Equipment_ID": "CVD-EQ-001",
    }

    for sensor, nominal in SENSOR_NOMINAL.items():
        noise = SENSOR_NOISE[sensor]
        values = np.random.normal(nominal, noise, n_points)
        if sensor == "RF_Reflected_Power":
            values = np.abs(values)
        data[sensor] = values

    data["Recipe_Step"] = ["ETCH"] * n_points
    data["Lot_ID"] = [f"LOT{(i // 25) + 1:03d}" for i in range(n_points)]

    anomaly_start = n_points // 2

    if anomaly_type == "pressure_drift":
        # 압력 서서히 증가 → APC Valve 오염 시나리오
        drift_len = n_points - anomaly_start
        drift = np.linspace(0, 0.15, drift_len)
        data["Chamber_Pressure"][anomaly_start:] += drift
        valve_drift = np.linspace(0, 9.0, drift_len)
        data["APC_Valve"][anomaly_start:] += valve_drift

    elif anomaly_type == "rf_instability":
        # RF 출력 불안정 → 임피던스 매칭 이상
        instability_len = n_points - anomaly_start
        spike = np.random.normal(0, 18.0, instability_len)
        data["RF_Forward_Power"][anomaly_start:] += spike
        data["RF_Reflected_Power"][anomaly_start:] += np.abs(spike) * 0.35

    elif anomaly_type == "temp_alarm":
        # ESC 온도 상승 → 히터 이상
        temp_len = n_points - anomaly_start
        temp_rise = np.linspace(0, 6.5, temp_len)
        temp_noise = np.random.normal(0, 0.8, temp_len)
        data["ESC_Temperature"][anomaly_start:] += temp_rise + temp_noise

    elif anomaly_type == "multi_sensor":
        # Pump 성능 저하 → 압력·유량 연쇄 이상
        multi_len = n_points - anomaly_start
        pump_deg = np.linspace(0, -220, multi_len)
        data["Pump_Speed"][anomaly_start:] += pump_deg
        pressure_rise = np.linspace(0, 0.09, multi_len)
        data["Chamber_Pressure"][anomaly_start:] += pressure_rise
        flow_noise = np.random.normal(0, 2.8, multi_len)
        data["Gas_Flow_MFC"][anomaly_start:] += flow_noise

    # normal: 이상 없이 그대로

    df = pd.DataFrame(data)
    df.to_csv(output_path, index=False)

    print(f"FDC 샘플 데이터 생성 완료: {output_path}")
    print(f"  데이터 포인트 : {n_points}개")
    print(f"  기간          : {timestamps[0]} ~ {timestamps[-1]}")
    print(f"  이상 시나리오 : {anomaly_type}")
    if anomaly_type != "normal":
        print(f"  이상 발생 구간: {anomaly_start}번째 포인트 이후")

    return output_path


def main():
    parser = argparse.ArgumentParser(description="FDC 샘플 데이터 생성기")
    parser.add_argument("--points", type=int, default=200, help="데이터 포인트 수 (기본: 200)")
    parser.add_argument(
        "--anomaly",
        default="pressure_drift",
        choices=["pressure_drift", "rf_instability", "temp_alarm", "multi_sensor", "normal"],
        help="이상 시나리오 타입 (기본: pressure_drift)",
    )
    parser.add_argument("--output", default="fdc_sample_data.csv", help="출력 CSV 파일 경로")
    parser.add_argument("--seed", type=int, default=42, help="랜덤 시드 (재현성)")
    args = parser.parse_args()

    generate_fdc_data(
        n_points=args.points,
        anomaly_type=args.anomaly,
        output_path=args.output,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
