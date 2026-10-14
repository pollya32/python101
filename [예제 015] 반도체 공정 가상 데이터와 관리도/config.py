"""YAML 설정 파일 읽기 (generate_data.py, control_chart.py 공용)."""
from pathlib import Path

import yaml

HERE = Path(__file__).parent
DEFAULT_CONFIG = HERE / "configs" / "etch.yaml"
FAULT_TYPES = {"scale", "offset", "ramp"}


def load_config(path):
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    sensors = cfg["sensors"]
    step_names = {s["name"] for s in cfg["steps"]}

    for step in cfg["steps"]:
        missing = set(sensors) - set(step)
        if missing:
            raise ValueError(f"스텝 '{step['name']}'에 센서 설정값이 없습니다: {sorted(missing)}")
    for f in cfg.get("faults", []):
        if f["step"] not in step_names or f["sensor"] not in sensors or f["type"] not in FAULT_TYPES:
            raise ValueError(f"고장 '{f['name']}' 설정이 잘못되었습니다 (step/sensor/type 확인)")
    if sum(f["prob"] for f in cfg.get("faults", [])) > 1:
        raise ValueError("고장 확률(prob)의 합이 1을 넘습니다")
    for m in cfg.get("chart", {}).get("metrics", []):
        if m["step"] not in step_names or m["sensor"] not in sensors:
            raise ValueError(f"관리도 항목 {m} 의 step/sensor를 확인하세요")
    return cfg


def data_dir(cfg):
    return HERE / "data" / cfg["equipment"]


def output_dir(cfg):
    return HERE / "output" / cfg["equipment"]
