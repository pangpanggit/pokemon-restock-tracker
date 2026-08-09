#!/usr/bin/env bash
# ── 포켓몬 리스톡 체커 (Mac/Linux) ─────────────────────
# ./run_checker.sh 실행하면 감시 시작. 크래시 시 10초 후 자동 재시작.
cd "$(dirname "$0")"
while true; do
  python3 check.py --loop
  echo "[체커 중단됨 - 10초 후 재시작]"
  sleep 10
done
