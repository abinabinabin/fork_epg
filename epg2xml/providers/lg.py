import logging
from datetime import date, datetime, timedelta
from typing import List, Dict, Any

# epg2xml 프레임워크의 일부. 실제 환경에서는 epg2xml 패키지 내에 위치해야 함.
# utils에서 get_json_response를 가져오는 것으로 보아, self.get_json은 EPGProvider의 표준 메소드임.
from epg2xml.providers import EPGProgram, EPGProvider, no_endtime
# from epg2xml.utils import get_json_response # 필요시 직접 호출용 (EPGProvider.get_json 사용 권장)

log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper())

# 시청 등급 코드 매핑 (기존 유지 또는 API 응답에 맞춰 조정)
G_CODE = {"0": 0, "1": 7, "2": 12, "3": 15, "4": 19, "": 0} # 빈 값은 전체관람가로 처리

# 프로그램 카테고리 코드 매핑 (기존 유지 또는 API 응답에 맞춰 조정)
# API 응답 샘플의 "urcBrdCntrTvSchdGnreCd" 와 매칭됨
P_CATE: Dict[str, Any] = {
    "00": "영화", "01": "스포츠/취미", "02": "만화", "03": "드라마", "04": "교양/다큐",
    "05": "스포츠/취미", "06": "교육", "07": "어린이", "08": "연예/오락",
    "09": "공연/음악", "10": "게임", "11": "다큐", "12": "뉴스/정보",
    "13": "라이프", "15": "홈쇼핑", "16": "경제/부동산", "31": "기타",
    "": "기타" # 빈 카테고리 코드 처리
}
# 채널 자체의 장르 코드 매핑용 (API 응답의 "brdGnreDtoList" 사용)
# 이 맵은 get_svc_channels 내에서 동적으로 생성됨 (_initialize_channel_genre_map)

class LG(EPGProvider):
    """EPGProvider for LG U+ (LGUplus)
    데이터: jsonapi (https://www.lguplus.com/uhdc/fo/prdv/chnlgid/v1/tv-schedule-list)
    요청수: 1 (채널 목록용, 모든 지역/카테고리 채널 포함 가정) + (#channels * #days) (프로그램 정보)
    특이사항:
    - API는 특정 날짜와 채널 ID를 파라미터로 받아 해당 채널의 하루치 EPG를 반환하는 구조.
    - 프로그램 시작 시각만 제공 (@no_endtime 사용).
    - 채널 장르와 프로그램 장르 코드가 다를 수 있음.
    """

    def __init__(self, cfg: dict):
        super().__init__(cfg) # EPGProvider의 __init__ 호출 (self.cfg, self.req 등 설정)
        self.svc_url = "https://www.lguplus.com/uhdc/fo/prdv/chnlgid/v1/tv-schedule-list"
        self.channel_genre_map: Dict[str, str] = {} # 채널 장르 코드 -> 장르 이름 매핑
        self.genre_map_initialized = False
        # User-Agent 등은 self.req.headers 에 EPGProvider에서 이미 설정됨

    def _fetch_api_data(self, params: dict, err_msg_prefix: str) -> Any:
        """공통 API 호출 로직 with self.get_json (EPGProvider의 메소드)"""
        try:
            # self.get_json은 EPGProvider 클래스에 정의되어 있어야 함.
            # 이 메소드가 HTTP 요청 및 JSON 파싱을 담당.
            if not hasattr(self, 'get_json'):
                log.error("LG: EPGProvider(self)에 get_json 메소드가 없습니다. epg2xml 프레임워크 오류일 수 있습니다.")
                return None
            data = self.get_json(self.svc_url, params=params, err_msg=err_msg_prefix)
            return data
        except Exception as e:
            log.error(f"LG: API 호출 중 예외 발생 ({err_msg_prefix}): {e}", exc_info=True)
            return None

    def _initialize_channel_genre_map(self, api_data_for_genres: dict) -> None:
        """API 응답으로부터 채널 장르 매핑 테이블을 생성하고 초기화합니다."""
        if self.genre_map_initialized:
            return
        
        raw_genre_list = api_data_for_genres.get("brdGnreDtoList")
        if not isinstance(raw_genre_list, list):
            log.warning("LG: API 응답에 'brdGnreDtoList'가 없거나 리스트 형식이 아닙니다.")
            return # 장르맵 초기화 실패

        temp_genre_map = {}
        for genre_item in raw_genre_list:
            if isinstance(genre_item, dict):
                genre_code = genre_item.get("urcBrdCntrTvChnlGnreCd")
                genre_name = genre_item.get("urcBrdCntrTvChnlGnreNm")
                if genre_code is not None and genre_name is not None: # null 값은 제외
                    temp_genre_map[str(genre_code)] = str(genre_name)
                # else: # 필수 키 누락 시 로깅은 선택적 (너무 많을 수 있음)
                #     log.debug(f"LG: 채널 장르 항목에 필수 키 누락: {genre_item}")
            else:
                log.warning(f"LG: 잘못된 채널 장르 항목 형식 (딕셔너리 아님): {genre_item}")
        
        if temp_genre_map:
            self.channel_genre_map = temp_genre_map
            self.genre_map_initialized = True
            log.debug(f"LG: 채널 장르 맵 생성/업데이트 완료. 총 {len(self.channel_genre_map)}개 장르.")
        else:
            log.warning("LG: 'brdGnreDtoList'에서 유효한 채널 장르 정보를 찾지 못했습니다.")


    def get_svc_channels(self) -> List[dict]:
        """LG U+ 서비스 채널 목록을 가져옵니다."""
        log.info("LG U+ 서비스 채널 목록 가져오기를 시작합니다...")
        svc_channels = []
        
        # LG U+ API는 BAS_DT (오늘날짜) 와 CHNL_TYPE='1' (전체추정) 파라미터로
        # 전체 채널 목록과 함께 장르 목록("brdGnreDtoList")도 반환하는 것으로 보임 (JSON 샘플 기반).
        params_for_channels = {
            "BAS_DT": date.today().strftime("%Y%m%d"),
            "CHNL_TYPE": "1" 
        }

        data = self._fetch_api_data(params_for_channels, "LG U+ 전체 채널 및 장르 목록")

        if not data: # API 호출 실패 또는 빈 데이터
            log.error("LG: 채널 및 장르 목록 API로부터 데이터를 받지 못했습니다.")
            return svc_channels

        # 채널 장르 맵 초기화 (성공적인 API 호출 후 한 번만 시도)
        if not self.genre_map_initialized:
            self._initialize_channel_genre_map(data)

        # 채널 목록 파싱 (JSON 샘플의 "brdCntrTvChnlIDtoList" 사용)
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
            
            if not service_id or not name: # 필수 정보 누락 시 건너뜀
                log.warning(f"LG: 채널 정보에 ServiceId 또는 Name이 없어 건너뜁니다: {x_ch_info}")
                continue
                
            channel_obj = {
                "ServiceId": str(service_id),
                "Name": str(name),
                "No": str(x_ch_info.get("urcBrdCntrTvChnlNo", "")), # 채널 번호
                "Icon_url": x_ch_info.get("bgImgUrl", ""),       # 아이콘 URL
                "EPG": [] # 프로그램 정보는 나중에 채워짐
            }
            
            # 채널 카테고리 설정
            channel_genre_code = str(x_ch_info.get("urcBrdCntrTvChnlGnreCd", "")) # 채널의 장르 코드
            if channel_genre_code and self.channel_genre_map:
                channel_obj["Category"] = self.channel_genre_map.get(channel_genre_code, "")
            elif channel_genre_code: # 맵에 없는 새로운 장르 코드일 경우
                channel_obj["Category"] = f"장르코드:{channel_genre_code}" 
            else: # 장르 코드가 아예 없는 경우
                channel_obj["Category"] = "" 
            
            svc_channels.append(channel_obj)
        
        if not svc_channels:
            log.warning("LG U+ 에서 수집된 서비스 채널이 전혀 없습니다.")
        else:
            log.info(f"LG U+ 에서 총 {len(svc_channels)}개의 서비스 채널 정보를 수집했습니다.")
            
        return svc_channels

    @no_endtime 
    def get_programs(self, **kwargs) -> None:
        """특정 채널, 특정 날짜의 EPG 프로그램 정보를 가져옵니다."""
        # epg2xml 프레임워크는 이 메소드를 채널별, 날짜별로 호출하며,
        # kwargs에 'channel' (EPGChannel 객체)과 'day' (datetime.date 객체) 등을 전달합니다.
        
        # KeyError: 'channel' 방지를 위해 kwargs에 키가 있는지 확인
        if "channel" not in kwargs or "day" not in kwargs:
            log.error("LG: get_programs 호출 시 'channel' 또는 'day' 정보가 누락되었습니다. kwargs: %s", kwargs)
            return

        channel = kwargs["channel"] # EPGChannel 객체
        current_day = kwargs["day"] # datetime.date 객체
        
        ch_id = channel.id       # EPGChannel 객체의 id는 ServiceId와 동일
        ch_name = channel.name
        
        # FETCH_LIMIT에 따른 날짜 수 (ndays)는 epg2xml 프레임워크가 이미 외부에서 루프를 돌며
        # current_day를 변경하여 여러 번 호출해주므로, 여기서 ndays를 직접 사용할 필요는 적음.
        # 다만, LG U+ API가 특정 일수 이상을 한 번에 제공하지 않는다면, 그 한계를 인지하는 것은 좋음.
        # (원본 주석: 5일치만 제공) -> 현재는 하루치씩 가져오므로 이 제한은 큰 의미 없음.

        log.debug(f"LG: 채널 '{ch_name}({ch_id})'의 '{current_day.strftime('%Y-%m-%d')}' EPG 정보 수집 시작...")
        
        params_for_epg = {
            "BAS_DT": current_day.strftime("%Y%m%d"),
            "CHNL_TYPE": "1", 
            "CHNL_ID": ch_id  
        }

        data = self._fetch_api_data(params_for_epg, f"채널 '{ch_name}({ch_id})' EPG ({current_day.strftime('%Y-%m-%d')})")

        if not data or not data.get("brdCntTvSchIDtoList"):
            log.info(f"LG: 채널 '{ch_name}({ch_id})'의 '{current_day.strftime('%Y-%m-%d')}' EPG 정보가 없습니다 (API 응답 비어있음).")
            return

        program_raw_data_list = data.get("brdCntTvSchIDtoList")
        if not isinstance(program_raw_data_list, list):
            log.warning(f"LG: 프로그램 목록 데이터('brdCntTvSchIDtoList')가 리스트가 아닙니다 (채널: {ch_name}, 날짜: {current_day}).")
            return
            
        try:
            epgs_for_day = self.__epgs_of_day(ch_id, program_raw_data_list) 
            channel.programs.extend(epgs_for_day) # EPGChannel 객체에 프로그램 추가
            log.debug(f"LG: 채널 '{ch_name}({ch_id})'의 '{current_day.strftime('%Y-%m-%d')}' EPG {len(epgs_for_day)}개 추가 완료.")
        except Exception as e: # 파싱 중 예외 발생 시
            log.error(f"LG: 프로그램 파싱 중 예외 발생 (채널: {ch_name}, 날짜: {current_day}): {e}", exc_info=True)


    def __epgs_of_day(self, channel_service_id: str, program_raw_data_list: list) -> List[EPGProgram]:
        """로우 데이터로부터 EPGProgram 객체 리스트를 생성합니다."""
        _epgs: List[EPGProgram] = []
        if not isinstance(program_raw_data_list, list): # 방어 코드
            log.warning(f"LG: __epgs_of_day 입력 데이터(program_raw_data_list)가 리스트가 아님 (채널 ID: {channel_service_id}).")
            return _epgs

        for p_info in program_raw_data_list: 
            if not isinstance(p_info, dict): # 방어 코드
                log.warning(f"LG: 잘못된 프로그램 정보 형식 (딕셔너리 아님, 채널 ID: {channel_service_id}): {p_info}")
                continue

            # channel_xmltv_id는 epg2xml.json의 ID_FORMAT에 따라 생성된 ID여야 함.
            # 여기서는 ServiceId를 그대로 사용하거나, EPGProgram 생성자에 ServiceId를 전달.
            # EPGProgram 생성자는 channelid (즉, xmltv_id)를 받음.
            # get_programs에서 받은 channel.id는 xmltv_id가 아니라 ServiceId임.
            # 따라서 epg2xml.json의 ID_FORMAT을 여기서도 적용하거나,
            # channel 객체에서 xmltv_id를 가져와야 함.
            # EPGProvider.get_programs_by_channel에서 _ch.id는 xmltv_id임.
            # 따라서 get_programs에 전달되는 channel.id도 xmltv_id. channel_service_id는 xmltv_id로 간주.
            _epg = EPGProgram(channel_service_id) # channel_service_id는 실제로는 xmltv_id여야 함
                                                 # 하지만 epg2xml은 내부적으로 ServiceId를 사용하고 최종 XML 생성 시 ID_FORMAT 적용
                                                 # EPGProgram은 ServiceId 기준으로 만들어도 됨.
            
            _epg.title = p_info.get("brdPgmTitNm", "").strip()
            if not _epg.title: _epg.title = "제목 없음"

            _epg.desc = p_info.get("brdPgmDscr", "").strip() or None
            
            brd_dt_str = p_info.get("brdCntrTvChnlBrdDt")
            start_t_str = p_info.get("epgStrtTme")

            if brd_dt_str and start_t_str:
                try:
                    _epg.stime = datetime.strptime(brd_dt_str + start_t_str, "%Y%m%d%H:%M:%S")
                except ValueError as e_time:
                    log.error(f"LG: 잘못된 시간 형식 (채널 ID: {channel_service_id}, 프로그램: {_epg.title}): {brd_dt_str}{start_t_str} - {e_time}")
                    continue 
            else:
                log.warning(f"LG: 방송 날짜 또는 시작 시간이 없습니다 (채널 ID: {channel_service_id}, 프로그램: {_epg.title})")
                continue

            rating_code = str(p_info.get("brdWtchAgeGrdCd", ""))
            _epg.rating = G_CODE.get(rating_code, 0) # 등급 코드 매핑
            
            extras_list = []
            if p_info.get("brdPgmRsolNm"): extras_list.append(p_info["brdPgmRsolNm"]) # 해상도
            if p_info.get("subtBrdYn") == "Y": extras_list.append("자막")
            if p_info.get("explBrdYn") == "Y": extras_list.append("화면해설")
            if p_info.get("silaBrdYn") == "Y": extras_list.append("수화")
            if extras_list: _epg.extras = " ".join(extras_list)

            program_category_code = str(p_info.get("urcBrdCntrTvSchdGnreCd", "")) # 프로그램 장르 코드
            if program_category_code and program_category_code in P_CATE:
                category_name = P_CATE[program_category_code]
                if category_name: 
                    _epg.categories = [category_name]
            elif program_category_code: 
                log.debug(f"LG: 알 수 없는 프로그램 카테고리 코드 '{program_category_code}' (프로그램: {_epg.title})")
                _epg.categories = [f"코드:{program_category_code}"] # 코드 자체를 카테고리로 사용
            
            # 부제, 회차 정보 등은 API 응답 JSON 샘플에 명확히 없으므로, 있다면 추가 파싱
            # 예: _epg.title_sub = p_info.get("subTitle") 
            # 예: _epg.ep_num = p_info.get("episodeNumber")
            
            _epgs.append(_epg)
        return _epgs
