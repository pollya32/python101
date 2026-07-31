#!/bin/bash
echo "============================================="
echo "  FDC ML 이상감지 서버 설치 및 시작"
echo "============================================="

pip install -r requirements.txt --quiet

echo ""
echo "서버 시작 중... http://localhost:8000"
echo "종료: Ctrl+C"
echo ""

python server.py
