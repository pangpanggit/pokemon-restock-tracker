@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==========================================
echo    포켓몬 리스톡 체커 - 원클릭 설치
echo ==========================================
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo [실패] Python이 설치돼 있지 않아요.
  echo        https://www.python.org/downloads/ 에서 설치하세요.
  echo        설치 첫 화면에서 "Add Python to PATH" 꼭 체크!
  pause
  exit /b 1
)
where git >nul 2>nul
if errorlevel 1 (
  echo [실패] Git이 설치돼 있지 않아요.
  echo        https://git-scm.com/download/win 에서 설치하세요.
  echo        ^(설치 옵션은 전부 기본값으로 Next만 눌러도 됩니다^)
  pause
  exit /b 1
)
echo [1/4] 파이썬 패키지 설치 중...
python -m pip install --quiet -r requirements.txt
if errorlevel 1 ( echo pip 설치 실패 & pause & exit /b 1 )
echo [2/4] 백업용 브라우저 엔진 설치 중... (1~2분 걸려요)
python -m playwright install chromium
echo [3/4] 폰 알림 테스트 발송!
python check.py --test-notify
echo      ^> 폰에 알림이 왔는지 확인하세요 (ntfy 앱에서 토픽 구독 필요)
echo [4/4] 첫 스캔 실행 - 전체 상품 수집 + 대시보드 갱신 (2~3분)...
python check.py --once
echo.
echo  ※ 위 로그에 "git push 실패"가 있다면:
echo    처음 한 번은 GitHub 로그인 창(브라우저)이 떠요. 로그인하면 다음부터 자동.
echo    창이 안 떴으면 이 폴더에서 git push 를 직접 한 번 실행해 보세요.
echo.
echo ============= 설치 끝! =============
echo 이제 run_checker.bat 더블클릭 → 상시 감시 시작!
echo 부팅 시 자동 시작: Win+R → shell:startup → 그 폴더에 run_checker.bat 바로가기 넣기
echo 절전 끄기: 설정 → 시스템 → 전원 → 화면/절전 "안 함"
pause
