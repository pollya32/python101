r"""
탐색기 보기 설정 되돌리기 (탐색기_보기설정_일괄변경.py 실행 전 상태로)

동작
  1. 현재 폴더 보기 설정(레지스트리 Bags/BagMRU)을 .reg 파일로 백업
  2. 탐색기를 잠시 종료한 뒤 폴더별 보기 설정을 모두 삭제
  3. 'BagMRU Size'를 기본값(5000)으로 되돌림
  4. 탐색기를 다시 실행

주의
  - 탐색기에는 'D 드라이브 폴더만 골라서 지우는' 방법이 없어서,
    D 드라이브뿐 아니라 PC의 모든 폴더 보기 설정이 윈도우 기본값으로 초기화됩니다.
  - 열려 있는 탐색기 창은 모두 닫히고, 작업 표시줄이 잠깐 사라졌다 돌아옵니다.
  - 백업 파일(.reg)을 더블클릭하면 초기화 전 상태로 다시 복원할 수 있습니다.

사용법
  python 탐색기_보기설정_되돌리기.py         # 확인 후 실행
  python 탐색기_보기설정_되돌리기.py --yes   # 확인 없이 실행
"""

import argparse
import os
import subprocess
import time
import winreg
from datetime import datetime

SHELL_KEY = r"Software\Classes\Local Settings\Software\Microsoft\Windows\Shell"
BAG_KEYS = [
    SHELL_KEY + r"\BagMRU",
    SHELL_KEY + r"\Bags",
    r"Software\Microsoft\Windows\Shell\BagMRU",
    r"Software\Microsoft\Windows\Shell\Bags",
]


def key_exists(path):
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, path))
        return True
    except FileNotFoundError:
        return False


def run(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def backup(keys):
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "백업_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(folder)
    for i, key in enumerate(keys, 1):
        out = os.path.join(folder, f"{i}_{key.split(chr(92))[-1]}.reg")
        result = run("reg", "export", "HKCU\\" + key, out, "/y")
        if result.returncode != 0:
            raise SystemExit(f"백업 실패: {key}\n{result.stderr}")
    return folder


def main():
    parser = argparse.ArgumentParser(description="탐색기 보기 설정 되돌리기")
    parser.add_argument("--yes", action="store_true", help="확인 없이 실행")
    args = parser.parse_args()

    print("PC의 모든 폴더 보기 설정을 윈도우 기본값으로 초기화합니다.")
    print("열려 있는 탐색기 창은 모두 닫힙니다.")
    if not args.yes and input("계속할까요? (y/N): ").strip().lower() != "y":
        print("취소했습니다.")
        return

    keys = [k for k in BAG_KEYS if key_exists(k)]
    if keys:
        print("백업 위치:", backup(keys))

    # 탐색기가 실행 중이면 종료할 때 설정을 다시 써 버리므로 먼저 종료
    run("taskkill", "/f", "/im", "explorer.exe")
    time.sleep(1)

    for key in keys:
        run("reg", "delete", "HKCU\\" + key, "/f")

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SHELL_KEY, 0, winreg.KEY_SET_VALUE) as k:
        try:
            winreg.DeleteValue(k, "BagMRU Size")
        except FileNotFoundError:
            pass

    subprocess.Popen("explorer.exe")
    print("완료: 폴더 보기 설정을 초기화했습니다.")


if __name__ == "__main__":
    main()
