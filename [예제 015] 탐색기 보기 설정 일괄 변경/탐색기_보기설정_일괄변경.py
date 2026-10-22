r"""
윈도우 11 탐색기 보기 설정 일괄 변경
- 대상: D 드라이브(기본값)와 모든 하위 폴더
- 보기: 자세히
- 정렬 기준: 수정한 날짜 (최신 순)
- 분류 방법: 유형

동작 원리
  탐색기 창을 하나 띄운 뒤 폴더를 하나씩 이동하면서 보기 설정을 바꿉니다.
  탐색기는 폴더를 떠날 때 그 폴더의 보기 설정을 레지스트리(Bags)에 저장하므로,
  한 번 실행해 두면 이후에도 설정이 유지됩니다.

사용법 (윈도우 명령 프롬프트 / PowerShell)
  pip install pywin32
  python 탐색기_보기설정_일괄변경.py            # D:\ 전체
  python 탐색기_보기설정_일괄변경.py E:\사진     # 다른 폴더 지정
  python 탐색기_보기설정_일괄변경.py --dry-run   # 대상 폴더 개수만 확인

주의
  - 실행 중에는 열리는 탐색기 창을 닫거나 조작하지 마세요.
  - 폴더 수가 많으면 시간이 걸립니다 (폴더당 약 0.1~0.5초).
"""

import argparse
import os
import subprocess
import sys
import time
import winreg

import win32com.client

VIEW_DETAILS = 4                              # 자세히
SORT_COLUMNS = "prop:-System.DateModified;"   # 수정한 날짜, '-' = 내림차순(최신 순)
GROUP_BY = "System.ItemTypeText"              # 유형

SKIP_NAMES = {"System Volume Information", "$RECYCLE.BIN", "$Recycle.Bin", "Config.Msi"}

SHELL_KEY = r"Software\Classes\Local Settings\Software\Microsoft\Windows\Shell"


def collect_folders(root):
    folders = [root]
    for dirpath, dirnames, _ in os.walk(root, onerror=lambda e: None):
        dirnames[:] = [d for d in dirnames if d not in SKIP_NAMES and not d.startswith("$")]
        folders.extend(os.path.join(dirpath, d) for d in dirnames)
    return folders


def raise_bag_limit(count):
    """탐색기는 기본적으로 폴더 5000개까지만 보기 설정을 기억하므로 한도를 늘린다."""
    needed = max(count + 1000, 5000)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, SHELL_KEY) as key:
        try:
            current, _ = winreg.QueryValueEx(key, "BagMRU Size")
        except FileNotFoundError:
            current = 5000
        if current < needed:
            winreg.SetValueEx(key, "BagMRU Size", 0, winreg.REG_DWORD, needed)
            print(f"BagMRU Size: {current} -> {needed}")


def current_path(window):
    try:
        return os.path.normcase(window.Document.Folder.Self.Path.rstrip("\\"))
    except Exception:
        return None


def open_explorer(shell, root):
    before = {w.HWND for w in shell.Windows()}
    subprocess.Popen(["explorer.exe", root])
    for _ in range(100):
        time.sleep(0.1)
        for w in shell.Windows():
            try:
                if w.HWND not in before and w.Document is not None:
                    return w
            except Exception:
                pass
    sys.exit("탐색기 창을 찾지 못했습니다.")


def wait_until(window, path, timeout=10):
    target = os.path.normcase(path.rstrip("\\"))
    end = time.time() + timeout
    while time.time() < end:
        try:
            if not window.Busy and current_path(window) == target:
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


def apply_view(doc):
    doc.CurrentViewMode = VIEW_DETAILS
    doc.SortColumns = SORT_COLUMNS
    doc.GroupBy = GROUP_BY


def main():
    parser = argparse.ArgumentParser(description="탐색기 보기 설정 일괄 변경")
    parser.add_argument("root", nargs="?", default="D:\\", help="대상 폴더 (기본값 D:\\)")
    parser.add_argument("--dry-run", action="store_true", help="대상 폴더 개수만 출력")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    print(f"폴더 목록 수집 중: {root}")
    folders = collect_folders(root)
    print(f"대상 폴더: {len(folders)}개")
    if args.dry_run:
        return

    raise_bag_limit(len(folders))

    shell = win32com.client.Dispatch("Shell.Application")
    window = open_explorer(shell, root)

    failed = []
    for i, folder in enumerate(folders, 1):
        try:
            window.Navigate2(folder)
            if not wait_until(window, folder):
                failed.append(folder)
                continue
            apply_view(window.Document)
        except Exception:
            failed.append(folder)
        if i % 50 == 0 or i == len(folders):
            print(f"  {i}/{len(folders)}")

    # 마지막 폴더의 설정도 저장되도록 루트로 돌아간 뒤 창을 닫는다
    window.Navigate2(root)
    wait_until(window, root)
    window.Quit()

    print(f"완료: {len(folders) - len(failed)}개 성공, {len(failed)}개 실패")
    for f in failed[:20]:
        print("  실패:", f)


if __name__ == "__main__":
    main()
