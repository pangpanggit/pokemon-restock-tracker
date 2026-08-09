# 🎴 포켓몬 카드 리스톡 트래커

Pokémon Center / Target / Walmart 재고를 자동 감시해서
**GitHub Pages 대시보드 + 폰 푸시 알림**으로 알려주는 도구입니다.

```
[항상 켜진 PC의 체커]──재고 스캔──▶ status.json ──git push──▶ [GitHub Pages 대시보드]
        │                                                        ▲ 누구나 접속 (공유 OK)
        └──재고 뜨면──▶ ntfy 푸시 ──▶ 📱 내 폰 + 친구들 폰
```

- **대시보드**: 30주년(30th Celebration) 상품 상단 고정 + ETB/부스터팩/박스세트 전체 목록, Sold Out/재고 상태, 바로 주문 버튼
- **알림**: 품절→재고 전환 순간 폰 푸시 (ntfy, 무료). 대시보드를 열어두면 브라우저 알림+소리도 가능
- **감시 주기**: 평상시 5분, 집중 시간대(설정 가능)엔 25초

---

## 1단계 — GitHub에 올리고 대시보드 켜기 (5분)

1. [github.com](https://github.com) 로그인 → 오른쪽 위 **+** → **New repository**
   - 이름: `pokemon-restock-tracker` (아무거나 OK), **Public**, Create
2. 이 폴더 전체를 업로드:
   - **쉬운 방법**: repo 페이지에서 `uploading an existing file` 클릭 → 이 폴더의 파일 전부 드래그 → Commit
   - **git 방법**:
     ```bash
     cd pokemon-restock-tracker
     git init && git add -A && git commit -m "init"
     git branch -M main
     git remote add origin https://github.com/내아이디/pokemon-restock-tracker.git
     git push -u origin main
     ```
3. repo → **Settings → Pages** → Source: `Deploy from a branch`, Branch: `main`, 폴더: **/ (root)** → Save
4. 1~2분 후 `https://내아이디.github.io/pokemon-restock-tracker/` 접속 → 대시보드 확인!
   - 이 주소를 친구들한테 공유하면 됩니다 📤

## 2단계 — 폰 알림 세팅 (2분)

1. 폰에 **ntfy** 앱 설치 (App Store / Play Store, 무료)
2. `config.yaml`에서 topic 이름을 **나만의 유니크한 이름**으로 변경
   (예: `pkmn-kwang-x7k2m9`) — *topic은 비밀번호 같은 거예요. 아는 사람은 다 받아요.*
3. ntfy 앱 → **+ → Subscribe to topic** → 같은 이름 입력
4. 친구들도 같은 topic 구독하면 똑같이 알림 받음

## 3단계 — 항상 켜진 컴퓨터에 체커 설치 (10분)

> Windows 기준 (Mac/Linux는 `setup_mac.sh` / `run_checker.sh` 사용)
>
> **빠른 길**: Python과 Git만 설치돼 있으면, repo를 clone한 뒤 **`setup_windows.bat` 더블클릭 한 번**으로 아래 4~5단계가 자동으로 끝나요.

1. [Python 설치](https://www.python.org/downloads/) — 설치 시 **"Add Python to PATH" 체크 필수!**
2. 이 repo를 그 컴퓨터에 clone (또는 zip 다운로드 후 git 연결):
   ```bash
   git clone https://github.com/내아이디/pokemon-restock-tracker.git
   cd pokemon-restock-tracker
   pip install -r requirements.txt
   python -m playwright install chromium
   ```
3. **git push 인증** (대시보드 자동 갱신용):
   - 제일 쉬운 방법: [GitHub Desktop](https://desktop.github.com/) 설치 후 로그인 → 이 repo를 GitHub Desktop으로 clone
   - 또는 `git config credential.helper store` 후 첫 push 때 [Personal Access Token](https://github.com/settings/tokens) 입력
4. `config.yaml` 열어서 수정:
   - `ntfy.topic` → 내 토픽
   - `dashboard.url` → 내 Pages 주소
   - `intervals.hot_windows` → 집중 감시할 한국시간대
5. 테스트:
   ```bash
   python check.py --test-notify   # 폰에 알림 오면 성공
   python check.py --once          # 1회 스캔 (전체 상품 목록 채워짐)
   ```
6. 상시 가동: **`run_checker.bat` 더블클릭** (창 떠있는 동안 감시)
   - 부팅 시 자동 시작: `Win+R` → `shell:startup` → 이 bat 파일의 바로가기를 넣기
   - 절전 모드 끄기: 설정 → 시스템 → 전원 → "절전 안 함"

## Target / Walmart 상품 추가

`config.yaml`에서:

```yaml
target:
  enabled: true
  products:
    - name: "Pokemon TCG: 30th Celebration ETB"
      url: "https://www.target.com/p/-/A-12345678"
walmart:
  enabled: true
  products:
    - name: "Pokemon TCG: 30th Celebration ETB"
      url: "https://www.walmart.com/ip/123456789"
```

저장하면 다음 사이클부터 자동 반영됩니다 (재시작 불필요).

## ⚡ 빠른 주문 꿀팁 (제일 중요!)

재고는 보통 **몇 분 안에** 사라집니다. 미리 해두세요:

1. 세 사이트 모두 **계정 로그인 유지**
2. **배송지 + 결제카드 저장** (Pokémon Center는 PayPal 연결도 추천)
3. 알림 클릭 → 상품 페이지 → Add to Cart → Checkout → 저장된 정보로 결제 = **30초 컷**
4. 포켓몬센터는 트래픽 몰리면 대기열이 뜹니다 — 새로고침하지 말고 기다리세요 (새로고침하면 뒤로 밀림)

## ❓ 문제 해결

| 증상 | 해결 |
|---|---|
| 폰 알림이 안 옴 | `--test-notify`로 확인. topic 오타, 앱 알림 권한 확인 |
| 대시보드가 안 바뀜 | 체커 창에 `git push 실패` 있는지 확인 → 3단계-3 인증 다시 |
| `requests 차단 감지` 로그 | 정상! 자동으로 브라우저 모드로 전환됨 |
| 체커가 계속 죽음 | 창의 마지막 에러 메시지 확인. Python/패키지 재설치 |
| PC 상품 목록이 안 나옴 | 포켓몬센터가 페이지 구조를 바꿨을 수 있음 — 이슈로 알려주세요 |

## ⚠️ 알아둘 것

- 요청 간격을 너무 짧게(수 초 이하) 잡으면 IP가 차단될 수 있어요. 기본값 추천.
- 자동 감시는 각 사이트 약관상 회색지대입니다. **결제는 직접** 하는 구조라 계정 위험이 낮은 편이지만, 자기 책임으로 사용하세요.
- 이 대시보드는 공개 페이지입니다. 개인정보는 절대 안 들어가요 (상품 정보만).
