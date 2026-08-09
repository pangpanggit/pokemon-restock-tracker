#!/usr/bin/env bash
# 포켓몬 리스톡 체커 - 원클릭 설치 (Mac/Linux)
cd "$(dirname "$0")"
set -e
echo "=========================================="
echo "   포켓몬 리스톡 체커 - 원클릭 설치"
echo "=========================================="
command -v python3 >/dev/null || { echo "[실패] python3가 필요해요"; exit 1; }
command -v git >/dev/null || { echo "[실패] git이 필요해요 (xcode-select --install)"; exit 1; }
echo "[1/4] 파이썬 패키지 설치 중..."
python3 -m pip install --quiet -r requirements.txt
echo "[2/4] 백업용 브라우저 엔진 설치 중... (1~2분)"
python3 -m playwright install chromium
echo "[3/4] 폰 알림 테스트 발송!"
python3 check.py --test-notify
echo "     > 폰에 알림 왔는지 확인 (ntfy 앱에서 토픽 구독 필요)"
echo "[4/4] 첫 스캔 실행 - 전체 상품 수집 + 대시보드 갱신 (2~3분)..."
python3 check.py --once
echo ""
echo "※ 'git push 실패'가 보이면 GitHub 인증이 필요해요 (README 참고)"
echo "============= 설치 끝! ============="
echo "상시 감시 시작: ./run_checker.sh"
