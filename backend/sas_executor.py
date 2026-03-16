"""
SASPy 세션 관리 및 코드 실행
"""

import os
import re
import threading
import saspy
import pandas as pd
from io import StringIO
from dotenv import load_dotenv

load_dotenv()

# 그래픽 활성화 + SVG 인라인 출력 (ODA ODS HTML5 기반)
_CODE_PREFIX = "ods graphics on / outputfmt=svg reset;\n"

# 인라인 속성 패턴 (SAS HTML 정제용)
_ATTR_STRIP = re.compile(
    r'\s+(style|class|bgcolor|cellpadding|cellspacing|border|width|align|valign|nowrap)'
    r'="[^"]*"',
    re.IGNORECASE,
)


def _extract_sas_output(html: str) -> list:
    """SAS HTML LST에서 테이블 + SVG 그래프 추출·정제 → HTML 문자열 목록 반환
    반환 형식: [{"type": "table"|"svg", "html": str}]
    """
    result = []

    # 테이블 추출
    for tbl in re.findall(r'<table[^>]*>.*?</table>', html, re.IGNORECASE | re.DOTALL):
        n_rows = len(re.findall(r'<tr[\s>]', tbl, re.IGNORECASE))
        n_cells = len(re.findall(r'<t[dh][\s>]', tbl, re.IGNORECASE))
        if n_rows < 2 or n_cells < 2:
            continue
        result.append({"type": "table", "html": _ATTR_STRIP.sub('', tbl)})

    # SVG 그래프 추출 (ODS HTML5 inline SVG)
    for svg in re.findall(r'<svg[^>]*>.*?</svg>', html, re.IGNORECASE | re.DOTALL):
        # 너무 작은 SVG(아이콘 등)는 제외
        if len(svg) < 500:
            continue
        result.append({"type": "svg", "html": svg})

    return result

KEEPALIVE_INTERVAL = 4 * 60  # 4분마다 ping (ODA 유휴 타임아웃 방어)


class SASExecutor:
    def __init__(self):
        self._session = None
        self._lock = threading.Lock()
        self._keepalive_timer = None

    # 세션 초기화 시 실행할 SAS 코드
    # %sysget(HOME) → 현재 ODA 사용자 홈 경로 동적 획득 (하드코딩 없음)
    _INIT_CODE = """
        options dlcreatedir;
        libname mylib "%sysget(HOME)/sas_workspace";
        %put MYLIB_PATH=%sysfunc(pathname(mylib));
    """

    def _get_session(self):
        if self._session is None:
            kwargs = {"cfgname": "oda", "results": "HTML"}
            sas_user = os.getenv("SAS_USER_NAME")
            sas_pass = os.getenv("SAS_PASS_WORD")
            if sas_user and sas_pass:
                kwargs["user"] = sas_user
                kwargs["pw"] = sas_pass
            self._session = saspy.SASsession(**kwargs)
            self._session.submit(self._INIT_CODE)   # 라이브러리 자동 할당
            self._schedule_keepalive()
        return self._session

    def _schedule_keepalive(self):
        """주기적으로 SAS에 %put 을 보내 유휴 타임아웃을 방지"""
        self._cancel_keepalive()
        self._keepalive_timer = threading.Timer(
            KEEPALIVE_INTERVAL, self._ping
        )
        self._keepalive_timer.daemon = True
        self._keepalive_timer.start()

    def _ping(self):
        try:
            if self._session:
                self._session.submit("%put keepalive;")
                self._schedule_keepalive()  # 다음 ping 예약
        except Exception:
            pass  # 세션이 이미 죽었으면 무시

    def _cancel_keepalive(self):
        if self._keepalive_timer:
            self._keepalive_timer.cancel()
            self._keepalive_timer = None

    def is_alive(self) -> bool:
        """세션 생존 여부 확인"""
        if self._session is None:
            return False
        try:
            r = self._session.submit("%put alive;")
            return "alive" in r.get("LOG", "")
        except Exception:
            return False

    def execute(self, code: str) -> dict:
        """
        SAS 코드 실행
        Returns: { "success": bool, "log": str, "output": str, "tables": list[DataFrame], "session_lost": bool }
        """
        full_code = _CODE_PREFIX + code
        with self._lock:
            try:
                sas = self._get_session()
                result = sas.submit(full_code)
            except Exception as e:
                # 세션 죽은 경우 — 재연결 시도
                self._session = None
                self._cancel_keepalive()
                try:
                    sas = self._get_session()
                    result = sas.submit(full_code)
                    reconnected = True
                except Exception as e2:
                    return {
                        "success": False,
                        "log": f"SAS 세션 오류: {e2}",
                        "output": "",
                        "tables": [],
                        "session_lost": True,
                    }
            else:
                reconnected = False

        log = result.get("LOG", "")
        lst = result.get("LST", "")
        has_error = bool(re.search(r'\bERROR\b', log))

        tables = []
        if lst.strip() and not has_error:
            tables = _extract_sas_output(lst)

        # HTML 태그 제거 (텍스트 폴백용)
        lst_text = re.sub(r'<[^>]+>', ' ', lst)
        lst_text = re.sub(r'&nbsp;', ' ', lst_text)
        lst_text = re.sub(r'&lt;', '<', lst_text)
        lst_text = re.sub(r'&gt;', '>', lst_text)
        lst_text = re.sub(r'&amp;', '&', lst_text)
        lst_text = '\n'.join(l for l in lst_text.splitlines() if l.strip())

        r = {
            "success": not has_error,
            "log": log,
            "output": lst_text,
            "tables": tables,
            "session_lost": False,
        }
        if reconnected:
            r["session_lost"] = True  # WORK 데이터 유실 경고용
        return r

    def column_info(self, libref: str, memname: str) -> list:
        """컬럼 이름·라벨·타입 조회 (saspy columnInfo 사용)"""
        with self._lock:
            sas = self._get_session()
            df = sas.columnInfo(table=memname.upper(), libref=libref.upper())
        if df is None or df.empty:
            return []
        cols = []
        for _, row in df.iterrows():
            type_raw = str(row.get("Type", "")).strip().lower()
            cols.append({
                "name":  str(row.get("Column", "") or "").strip(),
                "label": str(row.get("Label",  "") or "").strip(),
                "type":  "N" if type_raw in ("num", "numeric") else "C",
            })
        return cols

    def close(self):
        self._cancel_keepalive()
        if self._session:
            try:
                self._session.endsas()
            except Exception:
                pass
            self._session = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
