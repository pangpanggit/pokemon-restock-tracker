#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
포켓몬 카드 리스톡 체커
- Pokémon Center 카테고리 전체 스캔 (__NEXT_DATA__ 파싱)
- Target / Walmart 개별 상품 페이지 체크
- status.json 갱신 → git push → GitHub Pages 대시보드 반영
- 품절→재고 전환 시 ntfy 폰 푸시 알림

사용법:
  python check.py --once          # 1회 체크
  python check.py --loop          # 상시 감시 (config의 주기 사용)
  python check.py --test-notify   # 폰 알림 테스트
"""
import argparse
import json
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
    for site in ("target", "walmart"):
        for it in (sites.get(site, {}).get("items") or []):
            flat[f"{site}:{it['code']}"] = it
    return flat


def diff_and_notify(cfg, old_status, new_status):
    old_flat = flatten(old_status) if old_status else {}
    new_flat = flatten(new_status)
    dash_url = (cfg.get("dashboard") or {}).get("url")
    restocked, fresh = [], []
    for key, it in new_flat.items():
        prev = old_flat.get(key)
        if prev is None:
            if not old_flat:
                continue  # 첫 실행: 전체가 신규이므로 조용히
            fresh.append(it)
            continue
        if it.get("in_stock") is True and prev.get("in_stock") is not True:
            restocked.append(it)
    for it in restocked:
        star = "⭐ " if it.get("is_priority") else ""
        notify(cfg,
               f"🟢 재고 떴다! {star}{it['name'][:60]}",
               f"{it.get('price') or ''} — 지금 바로 주문하세요!",
               click_url=it["url"], priority="urgent",
               tags=["rotating_light", "moneybag"])
    if cfg.get("pokemoncenter", {}).get("notify_new_products"):
        for it in fresh:
            if it.get("is_priority"):
                notify(cfg,
                       f"🆕 신상품 감지: {it['name'][:60]}",
                       f"{it.get('price') or ''} — 재고: {'있음!' if it.get('in_stock') else '아직 품절'}",
                       click_url=it["url"], priority="high", tags=["new"])
    return len(restocked), len(fresh)


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
def run_cycle(cfg, state):
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    old_status = None
    if STATUS_PATH.exists():
        try:
            old_status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
            if old_status.get("initial_sample"):
                old_status = None  # 샘플 데이터는 비교 대상 아님
        except Exception:
            pass

    status = {
        "version": 1,
        "generated_at": now_iso,
        "dashboard": {"title": (cfg.get("dashboard") or {}).get("title",
                                                                "포켓몬 리스톡 트래커")},
        "ntfy_topic": (cfg.get("ntfy") or {}).get("topic", ""),
        "sites": {},
    }

    # Pokémon Center
    if cfg.get("pokemoncenter", {}).get("enabled"):
        try:
            cats = check_pokemoncenter(cfg)
            status["sites"]["pokemoncenter"] = {
                "label": "Pokémon Center", "ok": True,
                "checked_at": now_iso, "categories": cats}
        except Exception as e:
            log(f"  [PC] 체크 실패: {e}")
            prev = (old_status or {}).get("sites", {}).get("pokemoncenter", {})
            status["sites"]["pokemoncenter"] = {
                "label": "Pokémon Center", "ok": False,
                "checked_at": now_iso, "error": str(e)[:300],
                "categories": prev.get("categories", {})}

    # Target / Walmart
    for site, label in (("target", "Target"), ("walmart", "Walmart")):
        scfg = cfg.get(site, {})
        if not scfg.get("enabled"):
            continue
        items = check_product_site(site, scfg.get("products"))
        status["sites"][site] = {"label": label, "ok": True,
                                 "checked_at": now_iso, "items": items}

    restocked, fresh = diff_and_notify(cfg, old_status, status)
    save_status(status)

    # push 판단: 내용 변경 or 하트비트
    sig = content_signature(status)
    heartbeat = (cfg.get("git") or {}).get("heartbeat_minutes", 15) * 60
    should_push = (sig != state.get("last_sig")
                   or time.time() - state.get("last_push", 0) > heartbeat)
    if (cfg.get("git") or {}).get("auto_push") and should_push:
        reason = f"재고변화 {restocked}건" if sig != state.get("last_sig") else "heartbeat"
        git_push(reason)
        state["last_push"] = time.time()
    state["last_sig"] = sig

    total_items = len(flatten(status))
    in_stock = sum(1 for v in flatten(status).values() if v.get("in_stock") is True)
    log(f"사이클 완료: {total_items}개 추적, 재고 {in_stock}개, "
        f"입고알림 {restocked}건, 신상품 {fresh}건")


def in_hot_window(cfg):
    now = datetime.now(KST)
    cur = now.hour * 60 + now.minute
    for w in (cfg.get("intervals") or {}).get("hot_windows", []):
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
    args = ap.parse_args()

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
    while True:
        started = time.time()
        try:
            cfg = load_config()  # 루프 중 config 수정 반영
            run_cycle(cfg, state)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log(f"사이클 오류(계속 진행): {e.__class__.__name__}: {e}")
        iv = cfg.get("intervals") or {}
        interval = iv.get("hot_sec", 30) if in_hot_window(cfg) else iv.get("normal_sec", 300)
        elapsed = time.time() - started
        wait = max(5, interval - elapsed)
        mode = "🔥집중" if in_hot_window(cfg) else "평상"
        log(f"[{mode}] {int(wait)}초 후 다음 체크")
        time.sleep(wait)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n종료합니다.")
        sys.exit(0)
