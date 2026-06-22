"""서버 실행 스크립트"""
from app import app, init_db

if __name__ == '__main__':
    init_db()
    print("=" * 50)
    print(" 반도체 부품 재고관리 시스템 시작")
    print("=" * 50)
    print(" 주소: http://0.0.0.0:5000")
    print(" 기본 계정: admin / admin1234")
    print(" 종료: Ctrl+C")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=False)
