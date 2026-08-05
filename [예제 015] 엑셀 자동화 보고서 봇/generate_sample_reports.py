"""
샘플 원본 데이터 생성기
=====================================================
엑셀 자동화 봇(excel_report_bot.py) 테스트용 샘플 파일을 input/ 폴더에 만듭니다.
실행: python generate_sample_reports.py
"""
import os
import random
from datetime import date

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(BASE_DIR, 'input')

BRANCHES = {
    'branch_seoul.xlsx': '서울지점',
    'branch_busan.xlsx': '부산지점',
    'branch_daegu.xlsx': '대구지점',
}
STAFF = ['김민수', '이지은', '박서준', '최유나']
CATEGORIES = ['전자제품', '생활용품', '식품', '의류']
MONTHS = [1, 2, 3]

random.seed(42)


def make_month_sheet(branch_name: str, month: int) -> pd.DataFrame:
    rows = []
    for day in range(1, 21):
        for _ in range(random.randint(1, 3)):
            rows.append({
                '날짜': date(2026, month, day),
                '지점': branch_name,
                '담당자': random.choice(STAFF),
                '카테고리': random.choice(CATEGORIES),
                '매출액': random.randint(50, 500) * 1000,
                '비용': random.randint(10, 150) * 1000,
            })
    return pd.DataFrame(rows)


def main():
    os.makedirs(INPUT_DIR, exist_ok=True)
    for filename, branch_name in BRANCHES.items():
        path = os.path.join(INPUT_DIR, filename)
        with pd.ExcelWriter(path, engine='openpyxl') as writer:
            for month in MONTHS:
                df = make_month_sheet(branch_name, month)
                df.to_excel(writer, sheet_name=f'{month}월', index=False)
        print(f'생성됨: {path}')


if __name__ == '__main__':
    main()
