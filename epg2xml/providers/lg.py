import logging
from datetime import date, datetime, timedelta
from typing import List, Dict, Any

# epg2xml 프레임워크의 일부이므로, 실제 환경에서는 epg2xml 패키지 내에 위치해야 함
from epg2xml.providers import EPGProgram, EPGProvider, no_endtime # type: ignore

log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper())

# 시청 등급 코드 매핑 (기존 유지)
G_CODE = {"0": 0, "1": 7, "2": 12, "3": 15, "4": 19}

# 프로그램 카테고리 코드 매핑 (기존 유지, 실제 API 응답의 'urcBrdCntrTvSchdGnreCd'와 매칭)
P_CATE: Dict[str, Any] = {
    "00": "영화", "02": "만화", "03": "드라마", "05": "스포츠", "06": "교육",
    "07": "어린이", # 원본은 None이었으나, 대표 이름으로 변경 (또는 "어린이/교육")
    "08": "연예/오락", "09": "공연/음악",
    "10": "게임", # 원본은 None
    "11": "다큐", "12": "뉴스/정보", "13": "라이프",
    "15": "홈쇼핑", # 원본은 None
    "16": "경제/부동산", # 원본은 None
    "31": "기타",
    # API 응답 샘플에 없지만, epg2xml 기본값에 있을 수 있는 추가 코드들
}


class LG(EPGProvider):
    """EPGProvider for LG U+
    데이터: jsonapi
    요청수: #channels * #days (프로그램 정보) + #area_codes (채널 목록)
    특이사항:
    - API 응답에 따르면 5일치 이상 제공 가능해 보임 (기존 주석과 다름)
    - 프로그램 시작 시각만 제공
    """
    # svc_url = "https://www.lguplus.com/uhdc/fo/prdv/chnlgid/v1/tv-schedule-list" # 원본에 있었으나, 클래스 변수로 관리
    # 실제 요청 URL은 get_svc_channels, get_programs 내부에서 params와 함께 조합됨

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.svc_url = "https://www.lguplus.com/uhdc/fo/prdv/chnlgid/v1/tv-schedule-list"
        self.channel_genre_map: Dict[str, str] = {} # 채널 장르 코드 -> 이름 매핑
        self.genre_map_initialized = False

    def get_area_codes(self) -> dict:
        # 이 부분은 원본 lg.py에 없었으나, 실제로는 지역별로 채널을 가져와야 할 수 있음.
        # 원본 lg.py는 params로 area_code를 받지 않고 단일 URL로 모든 지역 채널을 가져오는 것처럼 보임.
        # 만약 지역 코드가 필요하다면, 여기서 설정. 지금은 단일 접근으로 가정.
        # JSON 응답 샘플에는 지역 관련 정보가 명시적으로 분리되어 있지 않음.
        # epg2xml 프레임워크가 지역 코드를 어떻게 처리하는지 또는 이 Provider가 어떻게 가정하는지에 따라 달라짐.
        # 여기서는 이전 lg.py처럼 단일 지역(또는 전체)을 가져온다고 가정하고 빈 딕셔너리 또는 기본값 반환.
        # 또는, 원본처럼 params에 CTGR_CD를 설정하는 방식이면, 그 값을 여기서 정의.
        # 원본 lg.py의 get_svc_channels에는 area_code를 반복하는 로직이 없었음.
        # 하지만 JSON 샘플에는 "brdCntrTvChnlIDtoList"가 최상위에 있으므로, 한 번의 호출로 모든 채널을 가져오는 것으로 보임.
        # 따라서 area_codes 개념이 여기서는 불필요할 수 있음.
        # 만약 카테고리별로 채널을 가져오는 방식이라면 아래와 같이 구성.
        # 원본 lg.py는 CHNL_TYPE만 사용했음.
        # return {"ALL": {"CHNL_TYPE": "1"}} # 예시: 전체 채널을 의미하는 하나의 "지역"
        # 이전 lg.py에는 area_codes를 반복하는 로직이 없었으므로, 여기서는 단일 실행을 가정.
        # get_svc_channels에서 직접 params를 설정.
        return {"DEFAULT_PARAMS": {"CHNL_TYPE": "1"}} # 단일 파라미터 셋으로 실행

    def _initialize_channel_genre_map(self, api_data: dict) -> None:
        """API 응답으로부터 채널 장르 매핑(_cate 역할)을 생성하고 초기화합니다."""
        if self.genre_map_initialized:
            return
        if "brdGnreDtoList" in api_data and isinstance(api_data["brdGnreDtoList"], list):
            temp_genre_map = {}
            for genre_item in api_data["brdGnreDtoList"]:
                if isinstance(genre_item, dict) and \
                   genre_item.get("urcBrdCntrTvChnlGnreCd") is not None and \
                   genre_item.get("urcBrdCntrTvChnlGnreNm") is not None:
                    # urcBrdCntrTvChnlGnreCd가 null일 수도 있으므로 체크 (JSON 샘플에선 null이 있었음)
                    genre_code = genre_item["urcBrdCntrTvChnlGnreCd"]
                    genre_name = genre_item["urcBrdCntrTvChnlGnreNm"]
                    if genre_code is not None: # null이 아닌 코드만 사용
                         temp_genre_map[str(genre_code)] = str(genre_name) # 코드를 문자열로 통일
                else:
                    log.warning(f"LG: 잘못된 채널 장르 항목 형식 또는 필수 키 누락: {genre_item}")
            
            if temp_genre_map:
                self.channel_genre_map = temp_genre_map
                self.genre_map_initialized = True
                log.debug(f"LG: 채널 장르 맵 생성 완료: {self.channel_genre_map}")
            else:
                log.warning("LG: 'brdGnreDtoList'에서 유효한 채널 장르 정보를 찾지 못했습니다.")
        else:
            log.warning("LG: API 응답에 'brdGnreDtoList'가 없거나 리스트 형식이 아닙니다.")

    def get_svc_channels(self) -> List[dict]:
        svc_channels = []
        # params_set = {"CHNL_TYPE": "1"} # get_area_codes() 대신 직접 사용
        # 원본 lg.py는 CHNL_TYPE과 BAS_DT만 사용했음. BAS_DT는 당일 날짜.

        # get_area_codes()를 통해 파라미터 세트를 가져온다고 가정 (단일 세트)
        # 또는 고정된 파라미터 사용
        params = {
            "BAS_DT": date.today().strftime("%Y%m%d"),
            "CHNL_TYPE": "1" # 전체 채널 의미로 추정
        }

        try:
            # get_json은 EPGProvider의 메소드
            data = self.get_json( 
                self.svc_url,
                params=params,
                err_msg="LG U+ 채널 목록을 가져오지 못했습니다"
            )
        except Exception as e:
            log.error(f"LG: 채널 목록 API 호출 중 예외 발생: {e}")
            return svc_channels # 빈 목록 반환

        if not data:
            log.error("LG: 채널 목록 API로부터 데이터를 받지 못했습니다.")
            return svc_channels

        # 채널 장르 맵 초기화 (아직 안됐다면)
        if not self.genre_map_initialized:
            self._initialize_channel_genre_map(data)

        # 채널 목록 파싱 (JSON 샘플의 "brdCntrTvChnlIDtoList" 사용)
        raw_channel_list = data.get("brdCntrTvChnlIDtoList")
        if not isinstance(raw_channel_list, list):
            log.error(f"LG: 채널 목록('brdCntrTvChnlIDtoList')이 리스트가 아닙니다. 데이터: {str(data)[:200]}")
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
                "ServiceId": str(service_id),
                "Name": str(name),
                "No": str(x_ch_info.get("urcBrdCntrTvChnlNo", "")),
                "Icon_url": x_ch_info.get("bgImgUrl", ""),
                "EPG": [] 
            }
            
            channel_genre_code = str(x_ch_info.get("urcBrdCntrTvChnlGnreCd", ""))
            if channel_genre_code and self.channel_genre_map:
                channel_obj["Category"] = self.channel_genre_map.get(channel_genre_code, "")
            elif channel_genre_code: # 맵은 없지만 코드는 있을 경우
                channel_obj["Category"] = f"장르코드:{channel_genre_code}" 
            else:
                channel_obj["Category"] = ""
            
            svc_channels.append(channel_obj)
        
        if not svc_channels:
            log.warning("LG U+ 에서 수집된 서비스 채널이 전혀 없습니다.")
        else:
            log.info(f"LG U+ 에서 {len(svc_channels)}개의 서비스 채널 정보를 수집했습니다.")
            
        return svc_channels

    @no_endtime # 이 데코레이터는 프로그램 종료 시각을 제공하지 않음을 의미
    def get_programs(self, **kwargs) -> None:
        # 원본 lg.py는 FETCH_LIMIT을 5일로 제한하는 로직이 있었음.
        # self.cfg["FETCH_LIMIT"]은 epg2xml.json에서 설정한 값.
        # 여기서는 epg2xml 프레임워크가 날짜 반복을 처리한다고 가정.
        # EPGProvider.get_programs_by_provider -> self.get_programs_by_channel 호출
        # -> 각 채널(_ch)에 대해 날짜별로 루프 돌며 self.get_program_by_day(day, _ch) 호출
        # -> self.get_program_by_day가 params 만들고 self.get_json 호출하고 __epgs_of_day 호출.

        # 아래는 원본 lg.py의 get_programs 로직과 유사하게,
        # 채널별, 날짜별로 API를 직접 호출하는 방식.
        # EPGProvider의 기본 get_programs는 채널 목록을 순회하며 각 채널에 대해
        # 지정된 날짜 범위만큼 EPG를 가져오도록 되어 있음.
        # 이 Provider가 #channels * #days 만큼 요청을 보낸다고 명시되어 있으므로,
        # 이 메소드가 날짜별, 채널별로 호출될 것임. (kwargs에 day, channel 객체가 넘어옴)

        channel = kwargs["channel"] # EPGChannel 객체 (epg2xml.providers.__init__.EPGChannel)
        ch_id = channel.id # ServiceId (urcBrdCntrTvChnlId 값)
        ch_name = channel.name
        ndays = kwargs["ndays"] # 가져올 EPG 일 수 (FETCH_LIMIT 값)

        # LG는 최대 5일치 제공한다고 했었음. ndays가 이를 넘으면 경고 또는 조정.
        if ndays > 5:
            log.warning("LG U+는 최대 5일치 EPG만 제공합니다. 요청 일수: %d일 -> 5일로 조정됨.", ndays)
            # ndays = 5 # 실제 가져오는 날짜 수를 조정할 필요는 없음. Provider가 알아서 처리.

        # 날짜 루프는 EPGProvider.get_programs_by_channel에서 이미 처리해 줌.
        # 이 메소드는 특정 채널, 특정 날짜에 대한 EPG를 가져오는 역할을 함.
        # (kwargs에 'day_idx' 와 'day' 가 넘어옴)
        current_day = kwargs["day"] # datetime.date 객체
        
        params = {
            "BAS_DT": current_day.strftime("%Y%m%d"),
            "CHNL_TYPE": "1", # 채널 목록 가져올 때와 동일한 타입 사용
            "CHNL_ID": ch_id  # 특정 채널 ID
        }

        try:
            data = self.get_json(
                self.svc_url,
                params=params,
                err_msg=f"채널 '{ch_name}({ch_id})'의 '{current_day.strftime('%Y-%m-%d')}' EPG 정보를 가져오지 못했습니다."
            )
        except Exception as e:
            log.error(f"LG: EPG API 호출 중 예외 발생 (채널: {ch_name}, 날짜: {current_day}): {e}")
            return # 해당 날짜, 해당 채널 EPG 가져오기 실패

        if not data or not data.get("brdCntTvSchIDtoList"):
            # 이 경우, 해당 날짜/채널에 방송이 없거나 API 오류일 수 있음.
            # 원본 lg.py는 "오늘은 EPG 없는 채널입니다" 로그 후 break 했으나,
            # 여기서는 단일 날짜 처리이므로 그냥 반환. (EPGProvider가 날짜 루프를 돌 것임)
            log.info(f"LG: 채널 '{ch_name}({ch_id})'의 '{current_day.strftime('%Y-%m-%d')}' EPG 정보가 없습니다.")
            return

        # 프로그램 목록은 "brdCntTvSchIDtoList" 안에 있음 (JSON 샘플에 따름)
        program_data_list = data.get("brdCntTvSchIDtoList")
        if not isinstance(program_data_list, list):
            log.warning(f"LG: 프로그램 목록 데이터가 리스트가 아닙니다 (채널: {ch_name}, 날짜: {current_day}).")
            return
            
        try:
            # __epgs_of_day는 EPGProgram 객체 리스트를 반환
            epgs_for_day = self.__epgs_of_day(ch_id, program_data_list) 
            channel.programs.extend(epgs_for_day) # EPGChannel 객체에 프로그램 추가
        except Exception as e:
            log.exception(f"LG: 프로그램 파싱 중 예외 발생 (채널: {ch_name}, 날짜: {current_day}): {e}")


    def __epgs_of_day(self, channel_xmltv_id: str, program_raw_data: list) -> List[EPGProgram]:
        """특정 채널, 특정 날짜의 로우 데이터로부터 EPGProgram 객체 리스트를 생성합니다."""
        _epgs: List[EPGProgram] = []
        if not isinstance(program_raw_data, list):
            log.warning(f"LG: __epgs_of_day 입력 데이터가 리스트가 아님 (채널 ID: {channel_xmltv_id}).")
            return _epgs

        for p_info in program_raw_data: # p_info는 각 프로그램의 딕셔너리
            if not isinstance(p_info, dict):
                log.warning(f"LG: 잘못된 프로그램 정보 형식 (딕셔너리 아님, 채널 ID: {channel_xmltv_id}): {p_info}")
                continue

            _epg = EPGProgram(channel_xmltv_id) 
            
            _epg.title = p_info.get("brdPgmTitNm", "").strip()
            if not _epg.title: # 제목이 없는 프로그램은 의미가 없으므로 건너뛸 수 있음
                log.debug(f"LG: 제목 없는 프로그램 데이터 (채널 ID: {channel_xmltv_id}): {p_info}")
                _epg.title = "제목 없음" # 또는 continue

            _epg.desc = p_info.get("brdPgmDscr", "").strip() or None # 설명이 null이나 빈 문자열이면 None
            
            brd_dt_str = p_info.get("brdCntrTvChnlBrdDt")
            start_t_str = p_info.get("epgStrtTme")

            if brd_dt_str and start_t_str:
                try:
                    _epg.stime = datetime.strptime(brd_dt_str + start_t_str, "%Y%m%d%H:%M:%S")
                except ValueError as e_time:
                    log.error(f"LG: 잘못된 시간 형식 (채널 ID: {channel_xmltv_id}, 프로그램: {_epg.title}): {brd_dt_str}{start_t_str} - {e_time}")
                    continue 
            else:
                log.warning(f"LG: 방송 날짜 또는 시작 시간이 없습니다 (채널 ID: {channel_xmltv_id}, 프로그램: {_epg.title})")
                continue

            # G_CODE (시청 등급) 및 P_CATE (프로그램 카테고리)는 파일 상단에 정의되어 있음
            _epg.rating = G_CODE.get(str(p_info.get("brdWtchAgeGrdCd")), 0) 
            
            extras_list = []
            if p_info.get("brdPgmRsolNm"): extras_list.append(p_info["brdPgmRsolNm"]) 
            if p_info.get("subtBrdYn") == "Y": extras_list.append("자막")
            if p_info.get("explBrdYn") == "Y": extras_list.append("화면해설")
            if p_info.get("silaBrdYn") == "Y": extras_list.append("수화")
            if extras_list: _epg.extras = " ".join(extras_list) # 공백으로 구분된 문자열로 저장 (epg2xml 표준 extras)

            program_category_code = str(p_info.get("urcBrdCntrTvSchdGnreCd", "")) # 프로그램 장르 코드
            if program_category_code and program_category_code in P_CATE:
                category_name = P_CATE[program_category_code]
                if category_name: # P_CATE 값이 None이 아닐 경우에만 추가
                    _epg.categories = [category_name]
            elif program_category_code: # 코드는 있으나 P_CATE에 매핑이 없을 때
                log.debug(f"LG: 알 수 없는 프로그램 카테고리 코드 '{program_category_code}' (프로그램: {_epg.title})")
                # _epg.categories = [f"카테고리코드:{program_category_code}"] # 또는 기타로 처리

            # 부제, 회차 정보 등은 JSON 샘플에 명확히 없으므로 추가 파싱 로직은 생략
            # 예: _epg.title_sub = p_info.get("subTitle") 
            # 예: _epg.ep_num = p_info.get("episodeNumber")

            _epgs.append(_epg)
        return _epgs
