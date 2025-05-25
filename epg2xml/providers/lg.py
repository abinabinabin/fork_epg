import logging
from datetime import date, datetime, timedelta #
from typing import List, Dict, Any #

# epg2xml 프레임워크의 일부.
try:
    from epg2xml.providers import EPGProgram, EPGProvider, no_endtime #
except ImportError:
    _temp_log_lg = logging.getLogger("LG_PROVIDER_IMPORT_ERROR_FALLBACK") #
    _temp_log_lg.error("epg2xml.providers 모듈을 찾을 수 없습니다. epg2xml 패키지가 올바르게 설치되었는지 확인하세요.") #
    class EPGProvider: #
        def __init__(self, cfg): #
            self.cfg = cfg #
            import requests #
            self.req = requests.Session() #
            self.req.headers["User-Agent"] = "epg2xml_lg_temp_ua" #
            _temp_log_lg.info("임시 EPGProvider 사용 중 - self.req가 requests.Session()으로 임시 초기화됨.") #
        def get_json(self, url, **kwargs): #
            # 이 임시 get_json은 실제 요청을 보내지 않으므로, 실제 테스트에서는 의미 없음
            _temp_log_lg.error(f"임시 EPGProvider.get_json 호출됨 (url: {url}). 실제 데이터 못 가져옴.") #
            return None # 실제로는 API 호출 후 JSON 반환해야 함 #
    class EPGProgram: #
        def __init__(self, channelid): self.channelid = channelid; self.title=None; self.stime=None #
    def no_endtime(func): return func #
    log = _temp_log_lg #
else:
    log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper()) #

G_CODE = {"0": 0, "1": 7, "2": 12, "3": 15, "4": 19, "": 0} #
P_CATE: Dict[str, Any] = { #
    "00": "영화", "01": "스포츠/취미", "02": "만화", "03": "드라마", "04": "교양/다큐", #
    "05": "스포츠/취미", "06": "교육", "07": "어린이", "08": "연예/오락", #
    "09": "공연/음악", "10": "게임", "11": "다큐", "12": "뉴스/정보", #
    "13": "라이프", "15": "홈쇼핑", "16": "경제/부동산", "31": "기타", "": "기타" #
}

class LG(EPGProvider):
    """EPGProvider for LG U+ (LGUplus)"""

    def __init__(self, cfg: dict):
        log.info(f"LG Provider __init__ 시작 (cfg type: {type(cfg)})") #
        try:
            super().__init__(cfg) # EPGProvider의 __init__ 호출, self.sess 와 self.request 초기화
            log.info("LG Provider: super().__init__(cfg) 호출 성공.") #
        except Exception as e_super_init:
            log.critical(f"LG Provider: super().__init__(cfg) 호출 중 심각한 예외 발생: {e_super_init}", exc_info=True) #
            raise #

        # 부모 클래스에서 self.sess를 초기화했으므로, 이를 self.req에 할당
        if hasattr(self, 'sess'):
            self.req = self.sess
        else:
            # super().__init__이 self.sess를 제대로 초기화하지 못한 경우 (이론적으로 발생하면 안됨)
            log.critical("LG Provider __init__ 내부 오류: self.sess가 super().__init__ 후에도 없습니다. EPGProvider.__init__ 확인 필요.")
            raise AttributeError("'LG' object's parent EPGProvider did not initialize 'sess'.")

        # self.req (원래 self.sess) 존재 유무 확인
        if not hasattr(self, 'req') or self.req is None:
            log.critical("LG Provider __init__ 내부 오류: self.req가 super().__init__ 후 (및 self.req=self.sess 할당 시도 후)에도 없습니다.") #
            raise AttributeError("'LG' object (after super init and assignment) still has no attribute 'req' or req is None.") #

        # self.get_json 메소드는 EPGProvider에 없으므로, 이 확인은 의미가 없거나 오류를 유발함.
        # _fetch_api_data 메소드가 self.request를 사용하도록 수정되므로, 이 검사는 경고로 대체하거나 제거.
        if not hasattr(self, 'request') or not callable(self.request): # EPGProvider는 self.request 메소드를 제공
             log.critical("LG Provider __init__ 내부 오류: self.request 메소드가 없습니다. EPGProvider.__init__ 확인 필요.") #
             raise AttributeError("'LG' object has no attribute 'request' or it's not callable") #
        
        log.debug(f"LG Provider __init__: self.req (from self.sess) 확인됨, User-Agent: {self.req.headers.get('User-Agent', 'N/A')}") #
        log.debug(f"LG Provider __init__: self.request 메소드 확인됨.") #

        self.svc_url = "https://www.lguplus.com/uhdc/fo/prdv/chnlgid/v1/tv-schedule-list" #
        self.channel_genre_map: Dict[str, str] = {} #
        self.genre_map_initialized = False #
        log.info(f"LG Provider 인스턴스 생성 및 초기화 완료. 서비스 URL: {self.svc_url}") #

    def _fetch_api_data(self, params: dict, err_msg_prefix: str, method: str = "GET") -> Any:
        log.debug(f"LG: _fetch_api_data 호출됨. 목적: {err_msg_prefix}. URL: {self.svc_url}, Params: {params}") #
        
        try:
            # EPGProvider로부터 상속받은 self.request 메소드 사용
            # self.request는 내부적으로 JSON 파싱 시도
            if method.upper() == "GET":
                data = self.request(self.svc_url, method=method, params=params) # GET 요청 시 params 사용
            else: # POST, PUT 등 다른 메소드의 경우
                data = self.request(self.svc_url, method=method, data=params) # API가 form data를 기대하는 경우 data 사용, JSON body를 기대하면 json=params 사용

            log.debug(f"LG: API 응답 수신 (self.request 사용) (첫 200자): {str(data)[:200] if data else 'None'}") #

            # self.request가 반환한 데이터가 예상하는 dict 또는 list 형태인지 확인
            if not isinstance(data, (dict, list)): # API 응답에 따라 list도 가능할 수 있음
                log.error(f"LG: API 응답이 예상된 JSON 형식이 아님 ({err_msg_prefix}). 수신된 타입: {type(data)}. 응답: {str(data)[:500]}")
                return None
            return data
        except Exception as e:
            log.error(f"LG: self.request API 호출 중 예외 발생 ({err_msg_prefix}): {e}", exc_info=True) #
            return None

    # ... (이하 _initialize_channel_genre_map, get_svc_channels, get_programs, __epgs_of_day 메소드는 기존 코드 유지) ...
    # (제공된 파일의 나머지 메소드 코드를 여기에 그대로 유지합니다.)
    def _initialize_channel_genre_map(self, api_data_for_genres: dict) -> None: #
        if self.genre_map_initialized: return #
        if not isinstance(api_data_for_genres, dict): #
            log.warning("LG: _initialize_channel_genre_map에 전달된 api_data_for_genres가 dict 타입이 아닙니다.") #
            return #

        raw_genre_list = api_data_for_genres.get("brdGnreDtoList") #
        if not isinstance(raw_genre_list, list): #
            log.warning("LG: API 응답에 'brdGnreDtoList'가 없거나 리스트 형식이 아닙니다. 채널 카테고리 정보가 누락될 수 있습니다.") #
            return #

        temp_genre_map = {} #
        for genre_item in raw_genre_list: #
            if isinstance(genre_item, dict): #
                genre_code = genre_item.get("urcBrdCntrTvChnlGnreCd") #
                genre_name = genre_item.get("urcBrdCntrTvChnlGnreNm") #
                if genre_code is not None and genre_name is not None: #
                    temp_genre_map[str(genre_code)] = str(genre_name) #
            else: #
                log.warning(f"LG: 잘못된 채널 장르 항목 형식 (딕셔너리 아님): {str(genre_item)[:100]}") #
        
        if temp_genre_map: #
            self.channel_genre_map = temp_genre_map #
            self.genre_map_initialized = True #
            log.debug(f"LG: 채널 장르 맵 생성/업데이트 완료. 총 {len(self.channel_genre_map)}개 장르.") #
        else: #
            log.warning("LG: 'brdGnreDtoList'에서 유효한 채널 장르 정보를 찾지 못했습니다.") #


    def get_svc_channels(self) -> List[dict]: #
        log.info("LG U+ 서비스 채널 목록 가져오기를 시작합니다...") #
        svc_channels = [] #
        params_for_channels = {"BAS_DT": date.today().strftime("%Y%m%d"), "CHNL_TYPE": "1"} #
        data = self._fetch_api_data(params_for_channels, "LG U+ 전체 채널 및 장르 목록") #

        if not data: #
            log.error("LG: 채널 및 장르 목록 API로부터 데이터를 받지 못했습니다 (호출 실패 또는 빈 응답).") #
            return svc_channels #

        if not self.genre_map_initialized: self._initialize_channel_genre_map(data) #

        raw_channel_list = data.get("brdCntrTvChnlIDtoList") #
        if not isinstance(raw_channel_list, list): #
            log.error("LG: API 응답의 채널 목록('brdCntrTvChnlIDtoList')이 리스트가 아닙니다.") #
            return svc_channels #
            
        for x_ch_info in raw_channel_list: #
            if not isinstance(x_ch_info, dict): #
                log.warning(f"LG: 잘못된 채널 정보 형식: {x_ch_info}"); continue #
            service_id = x_ch_info.get("urcBrdCntrTvChnlId") #
            name = x_ch_info.get("urcBrdCntrTvChnlNm") #
            if not service_id or not name: #
                log.warning(f"LG: 채널 정보에 ServiceId/Name 누락: {x_ch_info}"); continue #
                
            channel_obj = { #
                "ServiceId": str(service_id), "Name": str(name), #
                "No": str(x_ch_info.get("urcBrdCntrTvChnlNo", "")), #
                "Icon_url": x_ch_info.get("bgImgUrl", ""), "EPG": [] #
            } #
            channel_genre_code = str(x_ch_info.get("urcBrdCntrTvChnlGnreCd", "")) #
            if channel_genre_code and self.channel_genre_map: #
                channel_obj["Category"] = self.channel_genre_map.get(channel_genre_code, "") #
            elif channel_genre_code: channel_obj["Category"] = f"장르코드:{channel_genre_code}" #
            else: channel_obj["Category"] = "" #
            svc_channels.append(channel_obj) #
        
        if not svc_channels: log.warning("LG U+ 에서 수집된 서비스 채널이 전혀 없습니다.") #
        else: log.info(f"LG U+ 에서 총 {len(svc_channels)}개의 서비스 채널 정보를 수집했습니다.") #
        return svc_channels #

    @no_endtime #
    def get_programs(self, **kwargs) -> None: #
        log.debug(f"LG: get_programs 호출됨. kwargs 키: {list(kwargs.keys())}") #
        if "channel" not in kwargs or "day" not in kwargs: #
            log.error("LG: get_programs 호출 시 'channel' 또는 'day' 정보 누락. kwargs: %s", kwargs) #
            return #

        channel = kwargs["channel"] #
        current_day = kwargs["day"] #
        
        if not (hasattr(channel, 'id') and hasattr(channel, 'name') and #
                hasattr(channel, 'svcid') and hasattr(channel, 'programs') and #
                isinstance(channel.programs, list)): #
            log.error(f"LG: get_programs 전달 channel 객체 오류: id={getattr(channel, 'id', 'N/A')}") #
            return #

        api_channel_id = channel.svcid #
        if not api_channel_id: #
            log.warning(f"LG: 채널 객체에 svcid 없음. xmltv_id '{channel.id}'에서 추출 시도.") #
            api_channel_id = channel.id.split('.')[0] #
            if not api_channel_id: #
                log.error(f"LG: 채널 API ID 결정 불가 (채널: {channel.name}, xmltv_id: {channel.id}).") #
                return #

        ch_name_for_log = channel.name; xmltv_id_for_log = channel.id #
        log.debug(f"LG: 채널 '{ch_name_for_log}'(API ID: {api_channel_id}, XMLTV ID: {xmltv_id_for_log})의 '{current_day.strftime('%Y-%m-%d')}' EPG 수집...") #
        
        params_for_epg = {"BAS_DT": current_day.strftime("%Y%m%d"), "CHNL_TYPE": "1", "CHNL_ID": api_channel_id} #
        data = self._fetch_api_data(params_for_epg, f"채널 '{ch_name_for_log}({api_channel_id})' EPG ({current_day.strftime('%Y-%m-%d')})") #

        if not data or not data.get("brdCntTvSchIDtoList"): #
            log.info(f"LG: 채널 '{ch_name_for_log}({api_channel_id})'의 '{current_day.strftime('%Y-%m-%d')}' EPG 정보 없음.") #
            return #

        program_raw_data_list = data.get("brdCntTvSchIDtoList") #
        if not isinstance(program_raw_data_list, list): #
            log.warning(f"LG: 프로그램 목록 데이터가 리스트 아님 (채널: {ch_name_for_log}, 날짜: {current_day}).") #
            return #
            
        try: #
            epgs_for_day = self.__epgs_of_day(xmltv_id_for_log, program_raw_data_list) #
            channel.programs.extend(epgs_for_day) #
            log.debug(f"LG: 채널 '{ch_name_for_log}({xmltv_id_for_log})'의 '{current_day.strftime('%Y-%m-%d')}' EPG {len(epgs_for_day)}개 추가 완료.") #
        except Exception as e: #
            log.error(f"LG: 프로그램 파싱 중 예외 (채널: {ch_name_for_log}, 날짜: {current_day}): {e}", exc_info=True) #

    def __epgs_of_day(self, channel_xmltv_id: str, program_raw_data_list: list) -> List[EPGProgram]: #
        _epgs: List[EPGProgram] = [] #
        if not isinstance(program_raw_data_list, list): #
            log.warning(f"LG: __epgs_of_day 입력 데이터가 리스트 아님 (XMLTV ID: {channel_xmltv_id}).") #
            return _epgs #

        for p_info in program_raw_data_list: #
            if not isinstance(p_info, dict): #
                log.warning(f"LG: 잘못된 프로그램 정보 형식 (XMLTV ID: {channel_xmltv_id}): {str(p_info)[:100]}") #
                continue #
            _epg = EPGProgram(channel_xmltv_id) #
            _epg.title = p_info.get("brdPgmTitNm", "").strip() or "제목 없음" #
            _epg.desc = p_info.get("brdPgmDscr", "").strip() or None #
            
            brd_dt_str = p_info.get("brdCntrTvChnlBrdDt"); start_t_str = p_info.get("epgStrtTme") #
            if brd_dt_str and start_t_str: #
                try: _epg.stime = datetime.strptime(brd_dt_str + start_t_str, "%Y%m%d%H:%M:%S") #
                except ValueError as e_time: #
                    log.error(f"LG: 시간 형식 오류 (XMLTV ID: {channel_xmltv_id}, PGM: {_epg.title}): {brd_dt_str}{start_t_str} - {e_time}"); continue #
            else: #
                log.warning(f"LG: 날짜/시간 누락 (XMLTV ID: {channel_xmltv_id}, PGM: {_epg.title})"); continue #

            _epg.rating = G_CODE.get(str(p_info.get("brdWtchAgeGrdCd", "")), 0) #
            extras_list = [] #
            if p_info.get("brdPgmRsolNm"): extras_list.append(p_info["brdPgmRsolNm"]) #
            if p_info.get("subtBrdYn") == "Y": extras_list.append("자막") #
            if p_info.get("explBrdYn") == "Y": extras_list.append("화면해설") #
            if p_info.get("silaBrdYn") == "Y": extras_list.append("수화") #
            if extras_list: _epg.extras = " ".join(extras_list) #

            program_category_code = str(p_info.get("urcBrdCntrTvSchdGnreCd", "")) #
            if program_category_code and program_category_code in P_CATE: #
                category_name = P_CATE[program_category_code] #
                if category_name: _epg.categories = [category_name] #
            elif program_category_code: #
                log.debug(f"LG: 알 수 없는 프로그램 카테고리 코드 '{program_category_code}' (프로그램: {_epg.title})") #
                _epg.categories = [f"코드:{program_category_code}"] #
            _epgs.append(_epg) #
        return _epgs #
