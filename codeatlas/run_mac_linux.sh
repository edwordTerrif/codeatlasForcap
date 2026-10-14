#!/usr/bin/env bash
# CodeAtlas 실행 (macOS / Linux): 가상환경을 만들고 서버를 띄운다
set -e
cd "$(dirname "$0")/backend"
if [ ! -d .venv ]; then
  echo "[1/3] 파이썬 가상환경을 만드는 중..."
  python3 -m venv .venv
fi
source .venv/bin/activate
echo "[2/3] 필요한 패키지를 설치하는 중..."
pip install -q -r requirements.txt
echo "[3/3] 서버를 시작합니다: http://localhost:8000  (끄려면 Ctrl+C)"
python -m uvicorn app.main:app --port 8000
