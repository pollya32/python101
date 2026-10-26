import sys
from pathlib import Path

# tests/ 에서 실행해도 app.py 옆의 모듈(common, fdc, parts …)을 찾도록
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
