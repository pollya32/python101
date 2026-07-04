"""
보안(암호 걸린) 엑셀 파일을 안전하게 열어 읽기
=====================================================
설치: pip install -r requirements.txt
실행: python 보안엑셀_안전하게_읽기.py "내파일.xlsx"

적용 대상
  - 일반 엑셀 파일
  - Office 자체 "열기 암호"가 걸린 파일 (본인이 비밀번호를 아는 경우)

적용 대상이 아님
  - 회사 DRM/보안 솔루션(Fasoo, MarkAny 등 EDM 에이전트)으로 암호화된 파일은
    반드시 회사가 제공하는 전용 뷰어/에이전트로만 열어야 합니다.
    이 스크립트는 그런 보호를 우회하지 않으며, 그럴 목적으로 쓰여서도 안 됩니다.

이 예제가 지키는 안전 원칙
  1. 비밀번호를 코드에 하드코딩하지 않고 getpass로 그때그때 입력받는다.
  2. 복호화 결과를 디스크에 평문 사본으로 저장하지 않고 메모리(BytesIO)에서만 다룬다.
  3. 사용이 끝난 비밀번호 변수는 즉시 지운다.
  4. 전체 내용을 무조건 출력하지 않고, 시트 구조(행/열/컬럼명)만 먼저 보여준다.
"""

import io
import sys
import getpass

import msoffcrypto
import pandas as pd


def is_encrypted(path):
    with open(path, "rb") as f:
        office_file = msoffcrypto.OfficeFile(f)
        return office_file.is_encrypted()


def decrypt_to_memory(path, password):
    """암호 걸린 엑셀을 메모리 버퍼로 복호화한다 (디스크에 쓰지 않음)."""
    decrypted = io.BytesIO()
    with open(path, "rb") as f:
        office_file = msoffcrypto.OfficeFile(f)
        office_file.load_key(password=password)
        office_file.decrypt(decrypted)
    decrypted.seek(0)
    return decrypted


def open_excel_safely(path, max_attempts=3):
    """암호 유무를 자동 판단해 안전하게 엑셀을 열고 {시트명: DataFrame} 을 반환한다."""
    if not is_encrypted(path):
        return pd.read_excel(path, sheet_name=None)

    for attempt in range(1, max_attempts + 1):
        password = getpass.getpass(f"'{path}' 열기 암호 입력 ({attempt}/{max_attempts}): ")
        try:
            buffer = decrypt_to_memory(path, password)
            sheets = pd.read_excel(buffer, sheet_name=None)
            return sheets
        except Exception:
            print("암호가 올바르지 않거나 파일을 열 수 없습니다. 다시 시도해 주세요.")
        finally:
            del password  # 비밀번호는 시도 직후 메모리에서 제거

    raise RuntimeError("비밀번호 입력 횟수를 초과했습니다.")


def main():
    if len(sys.argv) < 2:
        print("사용법: python 보안엑셀_안전하게_읽기.py <엑셀파일경로>")
        return

    path = sys.argv[1]
    sheets = open_excel_safely(path)

    print(f"\n총 {len(sheets)}개 시트를 읽었습니다.\n")
    for name, df in sheets.items():
        print(f"[시트: {name}] {df.shape[0]}행 x {df.shape[1]}열")
        print(f"  컬럼: {list(df.columns)}")

    print("\n특정 시트의 내용을 보려면 open_excel_safely() 결과를 직접 활용하세요.")
    print("예) sheets = open_excel_safely(path); print(sheets['Sheet1'].head())")


if __name__ == "__main__":
    main()
