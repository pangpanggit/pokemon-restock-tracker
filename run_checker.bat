@echo off
REM ── 포켓몬 리스톡 체커 (Windows) ─────────────────────
REM 항상 켜두는 컴퓨터에서 이 파일을 더블클릭하면 감시 시작.
REM 크래시가 나도 10초 후 자동 재시작합니다.
chcp 65001 >nul
cd /d "%~dp0"
:loop
python check.py --loop
echo.
echo [체커 중단됨 - 10초 후 재시작]
timeout /t 10 /nobreak >nul
goto loop
