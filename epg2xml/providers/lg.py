"""
LG U+ EPG provider for epg2xml
rev. 2025-05-26 – cloudscraper 지원·CF warm-up 포함
"""

import logging, time, os, importlib, requests
from datetime import date, datetime, timedelta
from typing import List, Dict, Any

from epg2xml.providers import EPGProgram, EPGProvider, no_endtime

log = logging.getLogger(__name__.rsplit(".", 1)[-1].upper())

# ────────────────────── 상수 ──────────────────────
G_CODE = {"0": 0, "1": 7, "2": 12, "3": 15, "4": 19, "": 0}
P_CATE: Dict[str, Any] = {
    "00": "영화", "01": "스포츠/취미", "02": "만화", "03": "드라마", "04": "교양/다큐",
    "05": "스포츠/취미", "06": "교육", "07": "어린이", "08": "연예/오락",
    "09": "공연/음악", "10": "게임", "11": "다큐", "12": "뉴스/정보",
    "13": "라이프", "15": "홈쇼핑", "16": "경제/부동산", "31": "기타", "": "기타"
}
# ────────────────────────────────────────────────


class LG(EPGProvider):
    """LG U+ IPTV EPG provider"""

    # ── 1. 초기화 ───────────────────────────────────
    def __init__(self, cfg: dict):
        super().__init__(cfg)

        # cloudscraper 우선, 없으면 requests
        try:
            cloudscraper = importlib.import_module("cloudscraper")
            session = cloudscraper.create_scraper(
                browser={"custom": "Chrome/123.0 Android 14"},
                delay=10,
            )
            log.info("LG  cloudscraper 세션으로 초기화")
        except ModuleNotFoundError:
            session = requests.Session()
            log.warning("LG  cloudscraper 미설치 → plain requests 사용")

        self.req = session

        # Unity WebRequest 지문
        self.req.headers.update({
            "User-Agent":      "UnityPlayer/2021.3.18f1 (UnityWebRequest/1.0)",
            "X-Unity-Version": "2021.3.18f1",
            "Accept":          "*/*",
            "Accept-Language": "ko-KR,en-US;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Referer":         "https://www.lguplus.com/",
        })

        # (선택) 브라우저에서 가져온 cf_clearance 쿠키 주입
        for k in ("CF_CLEARANCE", "CF_BM"):
            v = os.getenv(f"LG_{k}")
            if v:
                self.req.cookies.set(
                    "cf_clearance" if k == "CF_CLEARANCE" else "__cf_bm",
                    v, domain=".lguplus.com"
                )

        self.url_channels = ("https://www.lguplus.com/uhdc/fo/prdv/chnlgid/v1/"
                             "tv-channel-list")
        self.url_schedule = ("https://www.lguplus.com/uhdc/fo/prdv/chnlgid/v1/"
                             "tv-schedule-list")

        self.channel_genre_map: Dict[str, str] = {}
        self.genre_map_initialized = False

    # ── 2. 공통 GET 헬퍼 ────────────────────────────
    def _fetch_api_json(self, url: str, params: dict, why: str) -> dict | None:
        try:
            r = self.req.get(url, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error(f"LG API 실패: {why} → {e}")
            if getattr(e, "response", None) is not None and \
               e.response.text.startswith("<!DOCTYPE html"):
                log.debug("LG 응답이 HTML… Cloudflare 차단 의심")
            return None

    # ── 3. Cloudflare 쿠키 워밍-업 ───────────────────
    def _warm_up_cf(self) -> None:
        today = date.today().strftime("%Y%m%d")
        self._fetch_api_json(
            self.url_schedule,
            {"brdCntrTvChnlBrdDt": today,
             "urcBrdCntrTvChnlId": "5",
             "urcBrdCntrTvChnlGnreCd": ""},
            "warm-up"
        )
        time.sleep(1)

    # ── 4. 장르 맵 초기화 ───────────────────────────
    def _initialize_channel_genre_map(self, data: dict) -> None:
        for g in data.get("brdGnreDtoList", []):
            if isinstance(g, dict):
                cd, nm = g.get("urcBrdCntrTvChnlGnreCd"), g.get("urcBrdCntrTvChnlGnreNm")
                if cd and nm:
                    self.channel_genre_map[str(cd)] = str(nm)
        self.genre_map_initialized = True

    # ── 5. 채널 목록 수집 ───────────────────────────
    def get_svc_channels(self) -> List[dict]:
        today = date.today().strftime("%Y%m%d")

        self._warm_up_cf()  # CF 통과
        data = self._fetch_api_json(
            self.url_channels,
            {"BAS_DT": today, "CHNL_TYPE": "1"},
            "LG 채널 목록"
        )
        svc_channels: list[dict] = []
        if not data:
            log.warning("LG 채널 목록 응답 없음")
            return svc_channels

        if not self.genre_map_initialized:
            self._initialize_channel_genre_map(data)

        for ch in data.get("brdCntrTvChnlIDtoList", []):
            sid, name = ch.get("urcBrdCntrTvChnlId"), ch.get("urcBrdCntrTvChnlNm")
            if not sid or not name:
                continue
            obj = {
                "ServiceId": str(sid),
                "Name":      str(name),
                "No":        str(ch.get("urcBrdCntrTvChnlNo", "")),
                "Icon_url":  ch.get("bgImgUrl", ""),
                "EPG":       []
            }
            g_cd = str(ch.get("urcBrdCntrTvChnlGnreCd", ""))
            obj["Category"] = (
                self.channel_genre_map.get(g_cd, f"장르코드:{g_cd}") if g_cd else ""
            )
            svc_channels.append(obj)

        log.info(f"LG 채널 {len(svc_channels)}개 수집 완료")
        return svc_channels

    # ── 6. EPG 수집 ─────────────────────────────────
    @no_endtime
    def get_programs(self) -> None:
        if not self.req_channels:
            log.warning("LG 요청 채널 없음")
            return

        fetch_limit = int(self.cfg.get("FETCH_LIMIT", 2))
        for ch in self.req_channels:
            for d in range(fetch_limit):
                day = date.today() + timedelta(days=d)
                data = self._fetch_api_json(
                    self.url_schedule,
                    {"BAS_DT": day.strftime("%Y%m%d"),
                     "CHNL_TYPE": "1",
                     "CHNL_ID": ch.svcid},
                    f"{ch.name}/{day}"
                )
                if not data:
                    continue
                ch.programs.extend(
                    self.__epgs_of_day(ch.id, data.get("brdCntTvSchIDtoList", []))
                )

    # ── 7. 프로그램 → EPGProgram ────────────────────
    def __epgs_of_day(self, xmltv_id: str, raw: list) -> List[EPGProgram]:
        epgs = []
        for p in raw:
            if not isinstance(p, dict):
                continue
            e = EPGProgram(xmltv_id)
            e.title = (p.get("brdPgmTitNm") or "").strip() or "제목 없음"
            e.desc  = (p.get("brdPgmDscr")   or "").strip() or None

            dt, st = p.get("brdCntrTvChnlBrdDt"), p.get("epgStrtTme")
            if not (dt and st):
                continue
            try:
                e.stime = datetime.strptime(dt + st, "%Y%m%d%H:%M:%S")
            except ValueError:
                continue

            e.rating = G_CODE.get(str(p.get("brdWtchAgeGrdCd", "")), 0)
            extras = []
            if p.get("brdPgmRsolNm"): extras.append(p["brdPgmRsolNm"])
            if p.get("subtBrdYn") == "Y": extras.append("자막")
            if p.get("explBrdYn") == "Y": extras.append("화면해설")
            if p.get("silaBrdYn") == "Y": extras.append("수화")
            if extras: e.extras = " ".join(extras)

            cd = str(p.get("urcBrdCntrTvSchdGnreCd", ""))
            if cd in P_CATE: e.categories = [P_CATE[cd]]
            elif cd:         e.categories = [f"코드:{cd}"]

            epgs.append(e)
        return epgs
