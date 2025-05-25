import logging
from datetime import date, datetime, timedelta
from typing import List, Dict, Any

# epg2xml 프레임워크의 일부.
try:
    from epg2xml.providers import EPGProgram, EPGProvider, no_endtime
except ImportError:
    # 이 부분은 로컬 테스트나 비정상적인 환경을 위한 대비이며,
    # GitHub Actions에서는 epg2xml 패키지가 site-packages에 올바르게 설치되어 있어야 합니다.
    # 기본 로거 사용 (epg2xml 프레임워크 로거를 찾을 수 없을 경우)
    _temp_log = logging.getLogger("LG_PROVIDER_IMPORT_ERROR")
    _temp_log.error("epg2xml.providers 모듈을 찾을 수 없습니다. epg2xml 패키지가 올바르게 설치되었는지 확인하세요.")
    # 임시 클래스 정의 (스크립트가 최소한 로드될 수 있도록 하지만, 기능은 제한됨)
    class EPGProvider:
        def __init__(self, cfg): 
            self.cfg = cfg
            # self.req 를 여기서라도 초기화 시도 (실제 requests.Session() 필요)
            # 하지만 EPGProvider의 실제 __init__을 따라야 함.
            # 이것은 임시 방편이며, 실제로는 상위 클래스 __init__이 호출되어야 함.
            self.req = None 
            log.error("임시 EPGProvider 사용 중 - self.req가 올바르게 초기화되지 않을 수 있음")
        def get_json(self, url, **kwargs): 
            log.error("EPGProvider.get_json 호출 실패 - 임시 구현")
            return None
    class EPGProgram:
        def __init__(self, channelid): self.channelid = channelid; self.title=None; self.stime=None # 기타 필드
    def no_endtime(func): return func
    log = _temp_log # 로거를 임시 로거로 사용
else:
    log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper())


# 시청 등급 코드 매핑
G_CODE = {"0": 0, "1": 7, "2": 12, "3": 15, "4": 19, "": 0} 

# 프로그램 카테고리 코드 매핑 (LG U+ API 응답의 'urcBrdCntrTvSchdGnreCd'와 매칭)
P_CATE: Dict[str, Any] = {
    "00": "영화", "01": "스포츠/취미", "02": "만화", "03": "드라마", "04": "교양/다큐",
    "05": "스포츠/취미", "06": "교육", "07": "어린이", "08": "연예/오락",
    "09": "공연/음악", "10": "게임", "11": "다큐", "12": "뉴스/정보",
    "13": "라이프", "15": "홈쇼핑", "16": "경제/부동산", "31": "기타",
    "": "기타" 
}

class LG(EPGProvider):
    """EPGProvider for LG U+ (LGUplus)
    데이터: jsonapi
    요청수: 1 (채널 목록용) + (#channels * #days) (프로그램 정보)
    특이사항: 프로그램 시작 시각만 제공.
    """

    def __init__(self, cfg: dict):
        log.info(f"LG Provider 초기화 시도 (cfg 키: {list(cfg.keys()) if isinstance(cfg, dict) else 'cfg 타입 오류'})...")
        try:
            # EPGProvider의 __init__을 명시적으로 호출하여 self.req 등이 설정되도록 함
            super().__init__(cfg) 
            log.info("LG Provider: super().__init__(cfg) 호출 완료.")

            # self.req (requests.Session 객체) 존재 및 초기화 확인
            if not hasattr(self, 'req') or self.req is None:
                log.critical("LG Provider 초기화 오류: self.req (requests.Session)가 super().__init__ 후에도 초기화되지 않았습니다! EPGProvider의 __init__ 구현을 확인해야 합니다.")
                # 이 경우 Provider는 정상 작동 불가. 하지만 오류를 발생시켜 epg2xml이 알도록 함.
                # raise AttributeError("'LG' object (after super init) still has no attribute 'req' or req is None")
            else:
                log.debug(f"LG Provider User-Agent: {self.req.headers.get('User-Agent')}")
            
            # self.get_json 메소드 존재 여부 확인
            if not hasattr(self, 'get_json') or not callable(self.get_json):
                 log.critical("LG Provider 초기화 오류: self 객체에 get_json 메소드가 없습니다! EPGProvider 상속 또는 프레임워크 문제를 확인하세요.")
                 # raise AttributeError("'LG' object has no attribute 'get_json' or it's not callable")
            else:
                log.debug("LG Provider: self.get_json 메소드가 정상적으로 존재합니다.")

        except Exception as e_init:
            log.critical(f"LG Provider 초기화 중 예외 발생: {e_init}", exc_info=True)
            # Provider 초기화 실패는 심각한 문제이므로 오류를 다시 발생시킴
            raise 

        self.svc_url = "https://www.lguplus.com/uhdc/fo/prdv/chnlgid/v1/tv-schedule-list"
        self.channel_genre_map: Dict[str, str] = {}
        self.genre_map_initialized = False
        log.info(f"LG Provider 인스턴스 생성 완료. 서비스 URL: {self.svc_url}")

    def _fetch_api_data(self, params: dict, err_msg_prefix: str, method: str = "GET") -> Any:
        """공통 API 호출 로직 with self.get_json (EPGProvider의 메소드)"""
        if not hasattr(self, 'get_json') or not callable(self.get_json):
            log.error(f"LG: API 호출 불가 - self.get_json 메소드 없음 ({err_msg_prefix})")
            return None
        
        log.debug(f"LG: API 호출 시도. URL: {self.svc_url}, Method: {method}, Params: {params}")
        try:
            data = self.get_json(self.svc_url, method=method, params=params, err_msg=f"{err_msg_prefix} (API호출)")
            log.debug(f"LG: API 응답 수신 (첫 200자): {str(data)[:200] if data else 'None'}")
            return data
        except Exception as e:
            log.error(f"LG: API 호출 중 예외 발생 ({err_msg_prefix}): {e}", exc_info=True)
            return None

    def _initialize_channel_genre_map(self, api_data_for_genres: dict) -> None:
        if self.genre_map_initialized:
            return
        if not isinstance(api_data_for_genres, dict): # api_data_for_genres 자체가 dict인지 확인
            log.warning("LG: _initialize_channel_genre_map에 전달된 api_data_for_genres가 dict 타입이 아닙니다.")
            return

        raw_genre_list = api_data_for_genres.get("brdGnreDtoList")
        if not isinstance(raw_genre_list, list):
            log.warning("LG: API 응답에 'brdGnreDtoList'가 없거나 리스트 형식이 아닙니다. 채널 카테고리 정보가 누락될 수 있습니다.")
            return 

        temp_genre_map = {}
        for genre_item in raw_genre_list:
            if isinstance(genre_item, dict):
                genre_code = genre_item.get("urcBrdCntrTvChnlGnreCd")
                genre_name = genre_item.get("urcBrdCntrTvChnlGnreNm")
                if genre_code is not None and genre_name is not None: 
                    temp_genre_map[str(genre_code)] = str(genre_name)
            else:
                log.warning(f"LG: 잘못된 채널 장르 항목 형식 (딕셔너리 아님): {str(genre_item)[:100]}")
        
        if temp_genre_map:
            self.channel_genre_map = temp_genre_map
            self.genre_map_initialized = True
            log.debug(f"LG: 채널 장르 맵 생성/업데이트 완료. 총 {len(self.channel_genre_map)}개 장르.")
        else:
            log.warning("LG: 'brdGnreDtoList'에서 유효한 채널 장르 정보를 찾지 못했습니다.")


    def get_svc_channels(self) -> List[dict]:
        log.info("LG U+ 서비스 채널 목록 가져오기를 시작합니다...")
        svc_channels = []
        
        params_for_channels = {
            "BAS_DT": date.today().strftime("%Y%m%d"),
            "CHNL_TYPE": "1" 
        }

        data = self._fetch_api_data(params_for_channels, "LG U+ 전체 채널 및 장르 목록")

        if not data:
            log.error("LG: 채널 및 장르 목록 API로부터 데이터를 받지 못했습니다 (호출 실패 또는 빈 응답).")
            return svc_channels

        if not self.genre_map_initialized:
            self._initialize_channel_genre_map(data)

        raw_channel_list = data.get("brdCntrTvChnlIDtoList")
        if not isinstance(raw_channel_list, list):
            log.error(f"LG: API 응답의 채널 목록('brdCntrTvChnlIDtoList')이 리스트가 아닙니다.")
            return svc_channels
            
        for x_ch_info in raw_channel_list:
            if not isinstance(x_ch_info, dict):
                log.warning(f"LG: 잘못된 채널 정보 형식 (딕셔너리 아님): {x_ch_info}")
                continue

            service_id = x_ch_info.get("urcBrdCntrTvChnlId")
            name = x_ch_info.get("urcBrdCntrTvChnlNm")
            
            if not service_id or not name: 
                log.warning(f"LG: 채널 정보에 ServiceId 또는 Name이 없어 건너뜁니다: {x_ch_info}")
                continue
                
            channel_obj = {
                "ServiceId": str(service_id), "Name": str(name),
                "No": str(x_ch_info.get("urcBrdCntrTvChnlNo", "")),
                "Icon_url": x_ch_info.get("bgImgUrl", ""), "EPG": [] 
            }
            
            channel_genre_code = str(x_ch_info.get("urcBrdCntrTvChnlGnreCd", ""))
            if channel_genre_code and self.channel_genre_map:
                channel_obj["Category"] = self.channel_genre_map.get(channel_genre_code, "")
            elif channel_genre_code: 
                channel_obj["Category"] = f"장르코드:{channel_genre_code}" 
            else: channel_obj["Category"] = "" 
            
            svc_channels.append(channel_obj)
        
        if not svc_channels: log.warning("LG U+ 에서 수집된 서비스 채널이 전혀 없습니다.")
        else: log.info(f"LG U+ 에서 총 {len(svc_channels)}개의 서비스 채널 정보를 수집했습니다.")
            
        return svc_channels

    @no_endtime 
    def get_programs(self, **kwargs) -> None:
        log.debug(f"LG: get_programs 호출됨. kwargs 키: {list(kwargs.keys())}")

        if "channel" not in kwargs:
            log.error("LG: get_programs 호출 시 'channel' 객체가 kwargs에 없습니다.")
            return
        if "day" not in kwargs:
            log.error("LG: get_programs 호출 시 'day' 객체가 kwargs에 없습니다.")
            return

        channel = kwargs["channel"] 
        current_day = kwargs["day"] 
        
        if not (hasattr(channel, 'id') and hasattr(channel, 'name') and 
                hasattr(channel, 'svcid') and hasattr(channel, 'programs') and 
                isinstance(channel.programs, list)):
            log.error(f"LG: get_programs에 전달된 channel 객체가 EPGChannel의 필수 속성/타입을 가지고 있지 않습니다: id={getattr(channel, 'id', 'N/A')}, name={getattr(channel, 'name', 'N/A')}, svcid_type={type(getattr(channel, 'svcid', None))}, programs_type={type(getattr(channel, 'programs', None))}")
            return

        api_channel_id = channel.svcid 
        if not api_channel_id: 
            log.warning(f"LG: 채널 객체에 svcid가 없습니다. xmltv_id '{channel.id}'에서 추출 시도.")
            api_channel_id = channel.id.split('.')[0] 
            if not api_channel_id: # 추출도 실패하면 진행 불가
                log.error(f"LG: 채널 API ID를 결정할 수 없습니다 (채널: {channel.name}, xmltv_id: {channel.id}).")
                return


        ch_name_for_log = channel.name
        xmltv_id_for_log = channel.id

        log.debug(f"LG: 채널 '{ch_name_for_log}'(API ID: {api_channel_id}, XMLTV ID: {xmltv_id_for_log})의 '{current_day.strftime('%Y-%m-%d')}' EPG 정보 수집 시작...")
        
        params_for_epg = {
            "BAS_DT": current_day.strftime("%Y%m%d"),
            "CHNL_TYPE": "1", 
            "CHNL_ID": api_channel_id  
        }

        data = self._fetch_api_data(params_for_epg, f"채널 '{ch_name_for_log}({api_channel_id})' EPG ({current_day.strftime('%Y-%m-%d')})")

        if not data or not data.get("brdCntTvSchIDtoList"):
            log.info(f"LG: 채널 '{ch_name_for_log}({api_channel_id})'의 '{current_day.strftime('%Y-%m-%d')}' EPG 정보가 없습니다 (API 응답 비어있음).")
            return

        program_raw_data_list = data.get("brdCntTvSchIDtoList")
        if not isinstance(program_raw_data_list, list):
            log.warning(f"LG: 프로그램 목록 데이터('brdCntTvSchIDtoList')가 리스트가 아닙니다 (채널: {ch_name_for_log}, 날짜: {current_day}).")
            return
            
        try:
            epgs_for_day = self.__epgs_of_day(xmltv_id_for_log, program_raw_data_list) 
            channel.programs.extend(epgs_for_day) 
            log.debug(f"LG: 채널 '{ch_name_for_log}({xmltv_id_for_log})'의 '{current_day.strftime('%Y-%m-%d')}' EPG {len(epgs_for_day)}개 추가 완료.")
        except Exception as e: 
            log.error(f"LG: 프로그램 파싱 중 예외 발생 (채널: {ch_name_for_log}, 날짜: {current_day}): {e}", exc_info=True)


    def __epgs_of_day(self, channel_xmltv_id: str, program_raw_data_list: list) -> List[EPGProgram]:
        _epgs: List[EPGProgram] = []
        if not isinstance(program_raw_data_list, list):
            log.warning(f"LG: __epgs_of_day 입력 데이터(program_raw_data_list)가 리스트가 아님 (채널 XMLTV ID: {channel_xmltv_id}).")
            return _epgs

        for p_info in program_raw_data_list: 
            if not isinstance(p_info, dict):
                log.warning(f"LG: 잘못된 프로그램 정보 형식 (딕셔너리 아님, 채널 XMLTV ID: {channel_xmltv_id}): {str(p_info)[:100]}")
                continue

            _epg = EPGProgram(channel_xmltv_id) 
            
            _epg.title = p_info.get("brdPgmTitNm", "").strip()
            if not _epg.title: _epg.title = "제목 없음"

            _epg.desc = p_info.get("brdPgmDscr", "").strip() or None
            
            brd_dt_str = p_info.get("brdCntrTvChnlBrdDt")
            start_t_str = p_info.get("epgStrtTme")

            if brd_dt_str and start_t_str:
                try:
                    _epg.stime = datetime.strptime(brd_dt_str + start_t_str, "%Y%m%d%H:%M:%S")
                except ValueError as e_time:
                    log.error(f"LG: 잘못된 시간 형식 (채널 XMLTV ID: {channel_xmltv_id}, 프로그램: {_epg.title}): {brd_dt_str}{start_t_str} - {e_time}")
                    continue 
            else:
                log.warning(f"LG: 방송 날짜 또는 시작 시간이 없습니다 (채널 XMLTV ID: {channel_xmltv_id}, 프로그램: {_epg.title})")
                continue

            rating_code = str(p_info.get("brdWtchAgeGrdCd", ""))
            _epg.rating = G_CODE.get(rating_code, 0) 
            
            extras_list = []
            if p_info.get("brdPgmRsolNm"): extras_list.append(p_info["brdPgmRsolNm"]) 
            if p_info.get("subtBrdYn") == "Y": extras_list.append("자막")
            if p_info.get("explBrdYn") == "Y": extras_list.append("화면해설")
            if p_info.get("silaBrdYn") == "Y": extras_list.append("수화")
            if extras_list: _epg.extras = " ".join(extras_list)

            program_category_code = str(p_info.get("urcBrdCntrTvSchdGnreCd", "")) 
            if program_category_code and program_category_code in P_CATE:
                category_name = P_CATE[program_category_code]
                if category_name: 
                    _epg.categories = [category_name]
            elif program_category_code: 
                log.debug(f"LG: 알 수 없는 프로그램 카테고리 코드 '{program_category_code}' (프로그램: {_epg.title})")
                _epg.categories = [f"코드:{program_category_code}"]
            
            _epgs.append(_epg)
        return _epgs
