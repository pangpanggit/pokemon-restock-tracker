#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
포켓몬 카드 리스톡 체커
- Pokémon Center 카테고리 전체 스캔 (__NEXT_DATA__ 파싱)
- Target / Walmart 개별 상품 페이지 체크
- 관심 상품(watchlist): 국내외 아무 쇼핑몰 URL 감시 → 재고 시 폰 알림 + PC 브라우저 자동 오픈
- status.json 갱신 → git push → GitHub Pages 대시보드 반영
- 품절→재고 전환 시 ntfy 폰 푸시 알림

사용법:
  python check.py --once          # 1회 체크
  python check.py --loop          # 상시 감시 (config의 주기 사용)
  python check.py --test-notify   # 폰 알림 테스트
  python check.py --probe URL     # 관심 상품 URL 판정 점검
"""
import argparse
import json
import random
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE
STATUS_PATH = ROOT / "status.json"
CONFIG_PATH = HERE / "config.yaml"
KST = timezone(timedelta(hours=9))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
BASE_HEADERS = {
    "User-Agent": UA,
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>',
    re.S)

_session = requests.Session()
_session.headers.update(BASE_HEADERS)
_playwright_ctx = {"pw": None, "browser": None, "page": None}


def log(msg):
    print(f"[{datetime.now(KST).strftime('%m-%d %H:%M:%S')}] {msg}", flush=True)


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def slugify(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s


# ------------------------------------------------------------------
# 페이지 가져오기: requests 우선, Cloudflare에 막히면 Playwright 폴백
# ------------------------------------------------------------------
def _looks_blocked(status_code, text):
    if status_code in (403, 429, 503):
        return True
    lowered = text[:4000].lower()
    return any(t in lowered for t in (
        "cf-chl", "challenge-platform", "just a moment",
        "attention required", "px-captcha", "robot or human"))


def _fetch_via_playwright(url, wait_selector=None):
    """실제 크로미움 브라우저로 페이지 로드 (봇 차단 완화용 폴백)."""
    ctx = _playwright_ctx
    if ctx["page"] is None:
        from playwright.sync_api import sync_playwright
        ctx["pw"] = sync_playwright().start()
        ctx["browser"] = ctx["pw"].chromium.launch_persistent_context(
            user_data_dir=str(HERE / ".pw-profile"),
            headless=True,
            user_agent=UA,
        )
        ctx["page"] = ctx["browser"].new_page()
        log("Playwright 브라우저 기동 (폴백 모드)")
    page = ctx["page"]
    page.goto(url, timeout=45000, wait_until="domcontentloaded")
    if wait_selector:
        try:
            page.wait_for_selector(wait_selector, timeout=15000)
        except Exception:
            pass
    time.sleep(1.5)
    return page.content()


def fetch_html(url, wait_selector=None):
    try:
        r = _session.get(url, timeout=25)
        if not _looks_blocked(r.status_code, r.text):
            return r.text
        log(f"  requests 차단 감지({r.status_code}) → Playwright 폴백: {url}")
    except requests.RequestException as e:
        log(f"  requests 실패({e.__class__.__name__}) → Playwright 폴백")
    return _fetch_via_playwright(url, wait_selector)


# ------------------------------------------------------------------
# Pokémon Center
# ------------------------------------------------------------------
def parse_pc_products(html):
    m = NEXT_DATA_RE.search(html)
    if not m:
        raise ValueError("__NEXT_DATA__ 를 찾지 못함 (페이지 구조 변경?)")
    data = json.loads(m.group(1))
    results = data["props"]["initialState"]["search"]["results"]
    products = results.get("products") or []
    if isinstance(products, dict):
        products = list(products.values())
    total = results.get("total", len(products))
    return total, products


def check_pokemoncenter(cfg):
    pc_cfg = cfg["pokemoncenter"]
    kw = [k.lower() for k in pc_cfg.get("priority_keywords", [])]
    out = {}
    for cat in pc_cfg["categories"]:
        cat_id, label = cat["id"], cat["label"]
        items, seen, page = [], set(), 1
        total = None
        while page <= 10:
            url = f"https://www.pokemoncenter.com/category/{cat_id}"
            if page > 1:
                url += f"?page={page}"
            html = fetch_html(url, wait_selector="script#__NEXT_DATA__")
            total, products = parse_pc_products(html)
            new_count = 0
            for p in products:
                code = p.get("code")
                if not code or code in seen:
                    continue
                seen.add(code)
                new_count += 1
                name = p.get("name", "")
                price = (p.get("listPrice") or {}).get("display")
                img = ""
                imgs = p.get("images") or []
                if imgs:
                    img = (imgs[0] or {}).get("thumbnail") or ""
                items.append({
                    "code": code,
                    "name": name,
                    "price": price,
                    "in_stock": not p.get("outOfStock", True),
                    "url": f"https://www.pokemoncenter.com/product/{code}/{slugify(name)}",
                    "img": img,
                    "release_date": (p.get("releaseDate") or "")[:10],
                    "is_priority": any(k in name.lower() for k in kw),
                })
            log(f"  [PC/{cat_id}] p{page}: 누적 {len(items)}/{total}")
            if len(items) >= (total or 0) or new_count == 0:
                break
            page += 1
            time.sleep(1.0)
        out[cat_id] = {"label": label, "items": items}
        time.sleep(1.0)
    return out


# ------------------------------------------------------------------
# Target / Walmart (개별 상품 페이지 휴리스틱)
# ------------------------------------------------------------------
def _availability_from_html(html, site):
    """페이지 텍스트에서 재고 신호 추출. True/False/None(판단불가)"""
    h = html.lower()
    if site == "target":
        if '"availability_status":"out_of_stock"' in h or '"availability_status": "out_of_stock"' in h:
            return False
        if '"availability_status":"in_stock"' in h or '"availability_status": "in_stock"' in h:
            return True
    if site == "walmart":
        if '"availabilitystatus":"out_of_stock"' in h:
            return False
        if '"availabilitystatus":"in_stock"' in h:
            return True
    # 공통 텍스트 휴리스틱
    if any(t in h for t in ("sold out", "out of stock", "currently unavailable")):
        return False
    if "add to cart" in h:
        return True
    return None


def check_product_site(site, products):
    items = []
    for p in products or []:
        name, url = p.get("name", "(이름없음)"), p["url"]
        in_stock, err = None, None
        try:
            html = fetch_html(url)
            in_stock = _availability_from_html(html, site)
        except Exception as e:
            err = str(e)[:200]
            log(f"  [{site}] 체크 실패: {name} — {err}")
        items.append({
            "code": url,
            "name": name,
            "price": p.get("price"),
            "in_stock": in_stock,
            "url": url,
            "img": p.get("img", ""),
            "release_date": "",
            "is_priority": True,
            "error": err,
        })
        time.sleep(2.0)
    return items


# ------------------------------------------------------------------
# 관심 상품 (watchlist) — 아무 쇼핑몰 URL이나 감시
# ------------------------------------------------------------------
SHOP_LABELS = (
    ("smartstore.naver.com", "네이버 스마트스토어"),
    ("brand.naver.com", "네이버 브랜드스토어"),
    ("shopping.naver.com", "네이버쇼핑"),
    ("coupang.com", "쿠팡"),
    ("11st.co.kr", "11번가"),
    ("gmarket.co.kr", "G마켓"),
    ("auction.co.kr", "옥션"),
    ("lotteon.com", "롯데ON"),
    ("ssg.com", "SSG"),
    ("emart", "이마트"),
    ("homeplus", "홈플러스"),
    ("kakao", "카카오"),
    ("pokemonkorea", "포켓몬코리아"),
    ("pokemoncenter-online.com", "포켓몬센터 온라인(JP)"),
    ("pokemoncenter.com", "Pokémon Center"),
    ("amazon.", "Amazon"),
    ("target.com", "Target"),
    ("walmart.com", "Walmart"),
)

# 구조화 데이터(JSON) 신호 — 텍스트보다 정확해서 먼저 본다
JSON_OUT = (
    r'"productstatustype"\s*:\s*"(outofstock|suspension|close|prohibition)"',
    r'"availability"\s*:\s*"(https?://schema\.org/)?(outofstock|soldout|discontinued)"',
    r'"(issoldout|soldout|outofstock)"\s*:\s*true',
    r'"availabilitystatus"\s*:\s*"out_of_stock"',
    r'"availability_status"\s*:\s*"out_of_stock"',
)
JSON_IN = (
    r'"productstatustype"\s*:\s*"sale"',
    r'"availability"\s*:\s*"(https?://schema\.org/)?(instock|limitedavailability|onlineonly)"',
    r'"(issoldout|soldout|outofstock)"\s*:\s*false',
    r'"availabilitystatus"\s*:\s*"in_stock"',
    r'"availability_status"\s*:\s*"in_stock"',
)
# 화면 텍스트 신호 (script/style 제거 후 검사)
TEXT_OUT = ("일시품절", "품절", "재고 없음", "재고없음", "판매종료", "판매 종료",
            "판매중지", "구매불가", "구매 불가", "재입고 알림", "입고알림",
            "sold out", "out of stock", "currently unavailable")
TEXT_IN = ("바로구매", "바로 구매", "구매하기", "장바구니 담기",
           "add to cart", "buy now")
_TAG_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.S | re.I)


def shop_label(url):
    u = url.lower()
    for key, label in SHOP_LABELS:
        if key in u:
            return label
    m = re.match(r"https?://(?:www\.|m\.)?([^/]+)", u)
    return m.group(1) if m else "기타"


def detect_stock(html, item=None):
    """(in_stock, 근거) 반환. in_stock은 True/False/None(판단불가).

    우선순위: ① 상품별 수동 문구 → ② JSON 구조화 신호 → ③ 화면 텍스트.
    '품절'은 정책 안내문 등에 섞여 나오기 쉬워서, 상품마다
    `python check.py --probe URL`로 실제 판정을 꼭 확인하세요.
    """
    item = item or {}
    raw = html.lower()
    text = re.sub(r"<[^>]+>", " ", _TAG_RE.sub(" ", html)).lower()
    text = re.sub(r"\s+", " ", text)

    for t in item.get("sold_out_text") or []:
        if t.lower() in text or t.lower() in raw:
            return False, f"수동문구(품절): {t}"
    for t in item.get("in_stock_text") or []:
        if t.lower() in text or t.lower() in raw:
            return True, f"수동문구(재고): {t}"

    for pat in JSON_OUT:
        m = re.search(pat, raw)
        if m:
            return False, f"JSON: {m.group(0)[:60]}"
    for pat in JSON_IN:
        m = re.search(pat, raw)
        if m:
            return True, f"JSON: {m.group(0)[:60]}"

    for t in TEXT_OUT:
        if t in text:
            return False, f"텍스트(품절): {t}"
    for t in TEXT_IN:
        if t in text:
            return True, f"텍스트(재고): {t}"
    return None, "신호 없음"


def signal_fingerprint(html):
    """페이지에서 잡힌 신호 묶음. 판정이 틀려도 '뭔가 바뀜'은 잡아내기 위한 보험."""
    raw = html.lower()
    text = re.sub(r"<[^>]+>", " ", _TAG_RE.sub(" ", html)).lower()
    hits = [m.group(0)[:50] for pat in JSON_OUT + JSON_IN for m in re.finditer(pat, raw)]
    hits += [t for t in TEXT_OUT + TEXT_IN if t in text]
    return "|".join(sorted(set(hits)))


def check_watch_item(p):
    """1개 상품 체크. JS 렌더링 사이트는 판단불가 시 브라우저로 재시도."""
    url = p["url"]
    html = fetch_html(url)
    in_stock, why = detect_stock(html, p)
    if in_stock is None:
        html = _fetch_via_playwright(url)
        in_stock, why = detect_stock(html, p)
        why = "[브라우저] " + why
    return in_stock, why, signal_fingerprint(html)


def check_watchlist(cfg, old_items):
    prev = {it["code"]: it for it in old_items or []}
    items = []
    for p in (cfg.get("watchlist") or {}).get("products") or []:
        url = p["url"]
        name = p.get("name") or url
        in_stock, why, fp, err = None, "", None, None
        if not p.get("bought"):
            try:
                in_stock, why, fp = check_watch_item(p)
            except Exception as e:
                err = str(e)[:200]
                log(f"  [관심] 체크 실패: {name} — {err}")
        old = prev.get(url, {})
        items.append({
            "code": url,
            "name": name,
            "shop": p.get("shop") or shop_label(url),
            "price": p.get("price"),
            "in_stock": in_stock,
            "url": url,
            "img": p.get("img", ""),
            "release_date": "",
            "is_priority": bool(p.get("must", True)),
            "must": bool(p.get("must", True)),
            "bought": bool(p.get("bought")),
            "note": p.get("note", ""),
            "signal": why,
            "fp": fp,
            "error": err,
            "last_in_stock_at": old.get("last_in_stock_at"),
        })
        log(f"  [관심] {name[:40]} → "
            f"{'구매완료' if p.get('bought') else {True: '재고!', False: '품절', None: '판단불가'}[in_stock]}"
            f" ({why or err or '-'})")
        time.sleep(float((cfg.get("watchlist") or {}).get("gap_sec", 1.5)))
    return items


def probe(url):
    """URL 1개를 받아서 판정 근거를 출력 (설정 점검용)."""
    print(f"쇼핑몰: {shop_label(url)}")
    html = fetch_html(url)
    print(f"[requests] 판정: {detect_stock(html)}")
    try:
        html2 = _fetch_via_playwright(url)
        print(f"[브라우저] 판정: {detect_stock(html2)}")
        html = html2
    except Exception as e:
        print(f"[브라우저] 실패: {e}")
    raw = html.lower()
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _TAG_RE.sub(" ", html))).lower()
    print("\n발견된 신호:")
    for pat in JSON_OUT + JSON_IN:
        for m in re.finditer(pat, raw):
            print(f"  JSON  {m.group(0)[:70]}")
    for t in TEXT_OUT + TEXT_IN:
        i = text.find(t)
        if i >= 0:
            print(f"  TEXT  '{t}' … {text[max(0, i - 30):i + 30]!r}")
    print("\n→ 품절일 때와 재고 있을 때 판정이 다르게 나오는지 확인하세요."
          "\n  엉뚱한 문구에 걸리면 config의 sold_out_text / in_stock_text 로 고정하세요.")


# ------------------------------------------------------------------
# 알림 (ntfy)
# ------------------------------------------------------------------
def notify(cfg, title, message, click_url=None, priority="urgent", tags=None):
    n = cfg.get("ntfy", {})
    if not n.get("enabled"):
        return
    payload = {
        "topic": n["topic"],
        "title": title,
        "message": message,
        "priority": {"urgent": 5, "high": 4, "default": 3}.get(priority, 3),
        "tags": tags or ["rotating_light"],
    }
    if click_url:
        payload["click"] = click_url
        payload["actions"] = [{"action": "view", "label": "바로 주문 →", "url": click_url}]
    try:
        requests.post(n.get("server", "https://ntfy.sh"), json=payload, timeout=10)
        log(f"  🔔 알림 발송: {title}")
    except requests.RequestException as e:
        log(f"  알림 발송 실패: {e}")


# ------------------------------------------------------------------
# 상태 비교/저장/푸시
# ------------------------------------------------------------------
def flatten(status):
    """{key: item} 으로 평탄화"""
    flat = {}
    sites = status.get("sites", {})
    pc = sites.get("pokemoncenter", {})
    for cat_id, cat in (pc.get("categories") or {}).items():
        for it in cat.get("items", []):
            flat[f"pc:{it['code']}"] = it
    for site in ("target", "walmart", "watchlist"):
        for it in (sites.get(site, {}).get("items") or []):
            flat[f"{site}:{it['code']}"] = it
    return flat


def update_restock_log(old_status, new_status, restocked_keys, now_iso):
    """재입고 기록 (언제 떴고 몇 분 만에 빠졌는지) → 집중 시간대 잡는 근거 데이터."""
    events = list((old_status or {}).get("restock_log") or [])
    new_flat = flatten(new_status)
    open_ev = {e["key"]: e for e in events if not e.get("out_at")}
    for key in restocked_keys:
        it = new_flat[key]
        it["last_in_stock_at"] = now_iso
        events.append({"key": key, "name": it["name"], "shop": it.get("shop", key.split(":")[0]),
                       "url": it["url"], "in_at": now_iso, "out_at": None})
    for key, ev in open_ev.items():
        it = new_flat.get(key)
        if it is None or it.get("in_stock") is False:
            ev["out_at"] = now_iso
    new_status["restock_log"] = events[-100:]


def diff_and_notify(cfg, old_status, new_status, state=None):
    state = state if state is not None else {}
    old_flat = flatten(old_status) if old_status else {}
    new_flat = flatten(new_status)
    wcfg = cfg.get("watchlist") or {}
    restocked, fresh, changed = [], [], []
    for key, it in new_flat.items():
        if it.get("bought"):
            continue
        prev = old_flat.get(key)
        if prev is None:
            if not old_flat:
                continue  # 첫 실행: 전체가 신규이므로 조용히
            fresh.append(it)
            continue
        if it.get("in_stock") is True and prev.get("in_stock") is not True:
            restocked.append((key, it))
        elif (key.startswith("watchlist:") and it.get("must")
              and wcfg.get("notify_on_change", True)
              and prev.get("in_stock") == it.get("in_stock")
              and prev.get("fp") is not None and it.get("fp") is not None
              and prev.get("fp") != it.get("fp")):
            changed.append(it)

    now = time.time()
    last_alert = state.setdefault("last_alert", {})
    for key, it in restocked:
        star = "⭐ " if it.get("is_priority") else ""
        shop = f"[{it['shop']}] " if it.get("shop") else ""
        notify(cfg,
               f"🟢 재고 떴다! {star}{shop}{it['name'][:60]}",
               f"{it.get('price') or ''} — 지금 바로 주문하세요!",
               click_url=it["url"], priority="urgent",
               tags=["rotating_light", "moneybag"])
        last_alert[key] = now
        if key.startswith("watchlist:") and wcfg.get("open_browser", True):
            open_in_browser(it["url"])

    # 아직 재고 남아있는 '꼭 살 것' → 놓쳤을까봐 재알림
    repeat = float(wcfg.get("repeat_alert_minutes", 2)) * 60
    if repeat > 0:
        for key, it in new_flat.items():
            if (key.startswith("watchlist:") and it.get("must") and not it.get("bought")
                    and it.get("in_stock") is True
                    and key not in dict(restocked)
                    and now - last_alert.get(key, 0) >= repeat):
                notify(cfg, f"⏰ 아직 재고 있음! {it['name'][:60]}",
                       "구매했으면 config.yaml에서 bought: true 로 바꿔주세요",
                       click_url=it["url"], priority="urgent", tags=["alarm_clock"])
                last_alert[key] = now

    for it in changed:
        notify(cfg, f"👀 페이지 변화 감지: {it['name'][:60]}",
               f"판정은 '{'재고' if it.get('in_stock') else '품절/불명'}'인데 페이지 신호가 바뀌었어요. 직접 확인!\n"
               f"신호: {it.get('fp') or '-'}"[:300],
               click_url=it["url"], priority="high", tags=["eyes"])

    if cfg.get("pokemoncenter", {}).get("notify_new_products"):
        for it in fresh:
            if it.get("is_priority"):
                notify(cfg,
                       f"🆕 신상품 감지: {it['name'][:60]}",
                       f"{it.get('price') or ''} — 재고: {'있음!' if it.get('in_stock') else '아직 품절'}",
                       click_url=it["url"], priority="high", tags=["new"])
    return [k for k, _ in restocked], len(fresh)


def open_in_browser(url):
    """체커 PC의 기본 브라우저(평소 로그인해둔 그 브라우저)로 상품 페이지 즉시 열기."""
    try:
        import webbrowser
        webbrowser.open(url, new=2)
        log(f"  🌐 브라우저로 열기: {url}")
    except Exception as e:
        log(f"  브라우저 열기 실패: {e}")


def content_signature(status):
    """generated_at 제외한 내용 비교용 서명"""
    flat = flatten(status)
    return json.dumps(
        sorted((k, v.get("in_stock"), v.get("price")) for k, v in flat.items()),
        ensure_ascii=False)


def save_status(status):
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=1)


def git_push(reason):
    try:
        subprocess.run(["git", "add", "status.json"],
                       cwd=ROOT, check=True, capture_output=True)
        r = subprocess.run(
            ["git", "commit", "-m",
             f"status: {reason} ({datetime.now(KST).strftime('%m-%d %H:%M')} KST)"],
            cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            return  # 변경 없음
        subprocess.run(["git", "push"], cwd=ROOT, check=True,
                       capture_output=True, timeout=60)
        log(f"  ⬆️ git push 완료 ({reason})")
    except Exception as e:
        log(f"  git push 실패: {e} — 대시보드 갱신은 안 되지만 감시는 계속합니다")


# ------------------------------------------------------------------
# 메인 사이클
# ------------------------------------------------------------------
def run_cycle(cfg, state, full=True):
    """full=True: 전체 스캔 / False: 관심 상품(watchlist)만 빠르게 재확인"""
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    old_status = None
    if STATUS_PATH.exists():
        try:
            old_status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
            if old_status.get("initial_sample"):
                old_status = None  # 샘플 데이터는 비교 대상 아님
        except Exception:
            pass
    old_sites = (old_status or {}).get("sites", {})

    status = {
        "version": 1,
        "generated_at": now_iso,
        "dashboard": {"title": (cfg.get("dashboard") or {}).get("title",
                                                                "포켓몬 리스톡 트래커")},
        "ntfy_topic": (cfg.get("ntfy") or {}).get("topic", ""),
        "sites": {},
    }

    # 관심 상품 — 매 사이클 체크 (가장 중요)
    if (cfg.get("watchlist") or {}).get("enabled", True) and \
            (cfg.get("watchlist") or {}).get("products"):
        items = check_watchlist(cfg, (old_sites.get("watchlist") or {}).get("items"))
        status["sites"]["watchlist"] = {"label": "관심 상품", "ok": True,
                                        "checked_at": now_iso, "items": items}

    # Pokémon Center
    if cfg.get("pokemoncenter", {}).get("enabled"):
        if not full and "pokemoncenter" in old_sites:
            status["sites"]["pokemoncenter"] = old_sites["pokemoncenter"]
        else:
            try:
                cats = check_pokemoncenter(cfg)
                status["sites"]["pokemoncenter"] = {
                    "label": "Pokémon Center", "ok": True,
                    "checked_at": now_iso, "categories": cats}
            except Exception as e:
                log(f"  [PC] 체크 실패: {e}")
                prev = old_sites.get("pokemoncenter", {})
                status["sites"]["pokemoncenter"] = {
                    "label": "Pokémon Center", "ok": False,
                    "checked_at": now_iso, "error": str(e)[:300],
                    "categories": prev.get("categories", {})}

    # Target / Walmart
    for site, label in (("target", "Target"), ("walmart", "Walmart")):
        scfg = cfg.get(site, {})
        if not scfg.get("enabled"):
            continue
        if not full and site in old_sites:
            status["sites"][site] = old_sites[site]
            continue
        items = check_product_site(site, scfg.get("products"))
        status["sites"][site] = {"label": label, "ok": True,
                                 "checked_at": now_iso, "items": items}

    restocked, fresh = diff_and_notify(cfg, old_status, status, state)
    update_restock_log(old_status, status, restocked, now_iso)
    save_status(status)

    # push 판단: 내용 변경 or 하트비트
    sig = content_signature(status)
    heartbeat = (cfg.get("git") or {}).get("heartbeat_minutes", 15) * 60
    should_push = (sig != state.get("last_sig")
                   or time.time() - state.get("last_push", 0) > heartbeat)
    if (cfg.get("git") or {}).get("auto_push") and should_push:
        reason = f"재고변화 {len(restocked)}건" if sig != state.get("last_sig") else "heartbeat"
        git_push(reason)
        state["last_push"] = time.time()
    state["last_sig"] = sig

    total_items = len(flatten(status))
    in_stock = sum(1 for v in flatten(status).values() if v.get("in_stock") is True)
    log(f"{'전체' if full else '관심상품'} 사이클 완료: {total_items}개 추적, 재고 {in_stock}개, "
        f"입고알림 {len(restocked)}건, 신상품 {fresh}건")


def in_hot_window(cfg, windows=None):
    now = datetime.now(KST)
    cur = now.hour * 60 + now.minute
    if windows is None:
        windows = (cfg.get("intervals") or {}).get("hot_windows", [])
    for w in windows:
        try:
            a, b = w.split("-")
            h1, m1 = map(int, a.split(":"))
            h2, m2 = map(int, b.split(":"))
            start, end = h1 * 60 + m1, h2 * 60 + m2
            if start <= end:
                if start <= cur <= end:
                    return True
            else:  # 자정 넘김
                if cur >= start or cur <= end:
                    return True
        except ValueError:
            continue
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="1회만 체크")
    ap.add_argument("--loop", action="store_true", help="상시 감시 루프")
    ap.add_argument("--test-notify", action="store_true", help="알림 테스트")
    ap.add_argument("--probe", metavar="URL", help="상품 URL 1개의 재고 판정 근거 출력")
    args = ap.parse_args()

    if args.probe:
        probe(args.probe)
        return

    cfg = load_config()
    if "CHANGE-ME" in (cfg.get("ntfy") or {}).get("topic", ""):
        log("⚠️  config.yaml의 ntfy topic을 아직 안 바꿨어요! (알림이 남들과 섞일 수 있음)")

    if args.test_notify:
        notify(cfg, "✅ 알림 테스트", "이 알림이 보이면 세팅 성공! 이제 재고 뜨면 이렇게 옵니다.",
               click_url=(cfg.get("dashboard") or {}).get("url"),
               priority="high", tags=["tada"])
        return

    state = {"last_sig": None, "last_push": 0}
    if not args.loop:
        run_cycle(cfg, state)
        return

    log("상시 감시 시작 (Ctrl+C로 종료)")
    last_full = 0.0
    while True:
        started = time.time()
        iv = cfg.get("intervals") or {}
        hot = in_hot_window(cfg)
        full_every = iv.get("hot_sec", 30) if hot else iv.get("normal_sec", 300)
        full = started - last_full >= full_every
        try:
            cfg = load_config()  # 루프 중 config 수정 반영
            run_cycle(cfg, state, full=full)
            if full:
                last_full = started
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log(f"사이클 오류(계속 진행): {e.__class__.__name__}: {e}")
        # 관심 상품은 전체 스캔보다 촘촘하게 돈다 (+랜덤 지터: 일정한 패턴 = 봇 차단 표적)
        w = cfg.get("watchlist") or {}
        has_watch = bool(w.get("products")) and w.get("enabled", True)
        if has_watch:
            w_hot = hot or in_hot_window(cfg, w.get("hot_windows") or [])
            interval = w.get("hot_interval_sec", 15) if w_hot else w.get("interval_sec", 45)
        else:
            interval = full_every
        interval *= random.uniform(0.85, 1.25)
        wait = max(5, interval - (time.time() - started))
        log(f"[{'🔥집중' if hot else '평상'}] {int(wait)}초 후 다음 체크")
        time.sleep(wait)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n종료합니다.")
        sys.exit(0)
