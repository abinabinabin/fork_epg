"""
LG U+ EPG provider for epg2xml
2025-05-26 수정본
"""

import logging
from datetime import date, datetime, timedelta
from typing import List, Dict, Any

from epg2xml.providers import EPGProgram, EPGProvider, no_endtime

log = logging.getLogger(__name__.rsplit(".", 1)[-1].upper())

# ────────────────────────────────────────────────────────────
G_CODE = {"0": 0, "1": 7, "2": 12, "3": 15, "4": 19, "": 0}
P_CATE: Dict[str, Any] = {
    "00": "영화", "01": "스포츠/취미", "02": "만화", "03": "드라마", "04": "교양/다큐",
    "05": "스포츠/취미", "06": "교육", "07": "어린이", "08": "연예/오락",
    "09": "공연/음악", "10": "게임", "11": "다큐", "12": "뉴스/정보",
    "13": "라이프", "15": "홈쇼핑", "16": "경제/부동산", "31": "기타", "": "기타"
}
# ────────────────────────────────────────────────────────────


class LG(EPGProvider):
    """LG U+ IPTV EPG provider"""

    # ── 1. 초기화 ──────────────────────────────────────
    def __init__(self, cfg: dict):
        super().__init__(cfg)          # self.req = requests.Session()

        # LG 공식 API 엔드포인트
        self.url_channels = ("https://www.lguplus.com/uhdc/fo/prdv/chnlgid/v1/"
                             "tv-channel-list")
        self.url_schedule = ("https://www.lguplus.com/uhdc/fo/prdv/chnlgid/v1/"
                             "tv-schedule-list")

        # Unity 지문을 그대로 세션 기본 헤더에 주입
        self.req.headers.update({
            "User-Agent":      "UnityPlayer/2021.3.18f1 (UnityWebRequest/1.0)",
            "X-Unity-Version": "2021.3.18f1",
            "Accept":          "*/*",
            "Accept-Language": "ko-KR,en-US;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Referer":         "https://www.lguplus.com/",
        })

        self.channel_genre_map: Dict[str, str] = {}
        self.genre_map_initialized = False

    # ── 2. 공통 요청 헬퍼 ──────────────────────────────
    def _fetch_api_json(self, url: str, params: dict, why: str) -> dict | None:
        """GET 요청 → JSON 반환 (실패 시 None)"""
        try:
            r = self.req.get(url, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error(f"LG  API 실패: {why} → {e}")
            # Cloudflare 차단 여부 힌트
            if getattr(e, "response", None) is not None and e.response.text.startswith("<!DOCTYPE html"):
                log.debug("LG  응답이 HTML… Cloudflare 우회 실패 가능성 ↑")
            return None

    # ── 3. 채널-장르 맵 초기화 ────────────────────────
    def _initialize_channel_genre_map(self, data: dict) -> None:
        raw = data.get("brdGnreDtoList")
        if not isinstance(raw, list):
            return
        for g in raw:
            if isinstance(g, dict):
                cd, nm = g.get("urcBrdCntrTvChnlGnreCd"), g.get("urcBrdCntrTvChnlGnreNm")
                if cd and nm:
                    self.channel_genre_map[str(cd)] = str(nm)
        self.genre_map_initialized = True

    # ── 4. 채널 목록 수집 ─────────────────────────────
    def get_svc_channels(self) -> List[dict]:
        today = date.today().strftime("%Y%m%d")
        data = self._fetch_api_json(
            self.url_channels,
            {"BAS_DT": today, "CHNL_TYPE": "1"},
            "LG 채널 목록"
        )
        svc_channels: list[dict] = []
        if not data:
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

    # ── 5. EPG 수집 ──────────────────────────────────
    @no_endtime
    def get_programs(self) -> None:
        if not self.req_channels:
            log.warning("LG  요청 채널 없음")
            return

        fetch_limit = int(self.cfg.get("FETCH_LIMIT", 2))
        for ch in self.req_channels:
            for d in range(fetch_limit):
                day = date.today() + timedelta(days=d)
                data = self._fetch_api_json(
                    self.url_schedule,
                    {
                        "BAS_DT":   day.strftime("%Y%m%d"),
                        "CHNL_TYPE":"1",
                        "CHNL_ID":  ch.svcid         # 서비스ID
                    },
                    f"{ch.name}/{day}"
                )
                if not data:
                    continue
                epg_list = data.get("brdCntTvSchIDtoList", [])
                ch.programs.extend(self.__epgs_of_day(ch.id, epg_list))

    # ── 6. 하루치 프로그램 → EPGProgram 객체 변환 ──────
    def __epgs_of_day(self, xmltv_id: str, raw_list: list) -> List[EPGProgram]:
        epgs: list[EPGProgram] = []
        for p in raw_list:
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

            cat_cd = str(p.get("urcBrdCntrTvSchdGnreCd", ""))
            if cat_cd in P_CATE: e.categories = [P_CATE[cat_cd]]
            elif cat_cd:         e.categories = [f"코드:{cat_cd}"]

            epgs.append(e)
        return epgs
