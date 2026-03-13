"""
SAS Code Assistant - FastAPI Backend
SSE 스트리밍으로 코드 생성 → 실행 → 요약 전달
"""

import asyncio
import json
import re
import sys
import threading
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

_lib_lock = threading.Lock()


class _HTMLTextExtractor(HTMLParser):
    """HTML 테이블에서 셀 텍스트를 추출 (탭/줄바꿈 구분)"""
    def __init__(self):
        super().__init__()
        self._rows: list[list[str]] = []
        self._cur_row: list[str] = []
        self._cur_cell: list[str] = []
        self._in_cell = False

    def handle_starttag(self, tag, _):
        if tag in ('td', 'th'):
            self._in_cell = True
            self._cur_cell = []
        elif tag == 'tr':
            self._cur_row = []

    def handle_endtag(self, tag):
        if tag in ('td', 'th'):
            self._in_cell = False
            self._cur_row.append(''.join(self._cur_cell).strip())
        elif tag == 'tr':
            if self._cur_row:
                self._rows.append(self._cur_row)

    def handle_data(self, data):
        if self._in_cell:
            self._cur_cell.append(data)

    def get_text(self, max_rows=50) -> str:
        lines = ['\t'.join(r) for r in self._rows[:max_rows]]
        return '\n'.join(lines)


def _tables_to_text(items: list[dict], max_chars=2000) -> str:
    """result의 table HTML 목록 → 요약용 텍스트"""
    parts = []
    for item in items:
        if item.get('type') == 'svg':
            continue
        ext = _HTMLTextExtractor()
        ext.feed(item.get('html', ''))
        parts.append(ext.get_text())
    text = '\n\n'.join(p for p in parts if p)
    return text[:max_chars]


from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))  # backend/ 모듈 탐색

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

import yaml
import code_generator as llm
from sas_executor import SASExecutor

# sashelp_datasets.yml 로드 (단일 소스)
_root = Path(__file__).parent.parent
_sashelp_yml = _root / "sashelp/source/sashelp_datasets.yml"
_sashelp_datasets: list = []
_sashelp_top_names: set = set()

if _sashelp_yml.exists():
    _raw = yaml.safe_load(_sashelp_yml.read_text(encoding="utf-8"))
    for d in _raw.get("datasets", []):
        _sashelp_datasets.append({
            "name": d["name"],
            "label": d.get("label", ""),
            "columns": [
                {"name": c["name"], "label": c.get("label", ""), "type": c.get("type", "C")}
                for c in d.get("columns", [])
            ],
        })
        if d.get("top", False):
            _sashelp_top_names.add(d["name"].lower())

app = FastAPI(title="SAS Code Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# ── 세션 관리 ─────────────────────────────────────────────────────────────────

_sessions: dict[str, dict] = {}
MAX_RETRIES = 3
MAX_HISTORY_TURNS = 10
SESSION_IDLE_TIMEOUT = 20 * 60  # 20분 무응답 시 세션 종료


def _get_session(session_id: str) -> dict:
    if session_id not in _sessions:
        _sessions[session_id] = {
            "history": [],
            "history_summary": "",
            "executor": SASExecutor(),
            "idle_timer": None,
        }
    _reset_idle_timer(session_id)
    return _sessions[session_id]


def _reset_idle_timer(session_id: str):
    sess = _sessions.get(session_id)
    if not sess:
        return
    if sess.get("idle_timer"):
        sess["idle_timer"].cancel()
    timer = threading.Timer(SESSION_IDLE_TIMEOUT, _expire_session, args=[session_id])
    timer.daemon = True
    timer.start()
    sess["idle_timer"] = timer


def _expire_session(session_id: str):
    if session_id in _sessions:
        sess = _sessions.pop(session_id)
        try:
            sess["executor"].close()
        except Exception:
            pass


def _evt(type_: str, **kwargs) -> str:
    return f"data: {json.dumps({'type': type_, **kwargs})}\n\n"


# ── SSE 스트림 ────────────────────────────────────────────────────────────────

async def _stream(message: str, session_id: str):
    sess = _get_session(session_id)
    history = sess["history"]
    summary = sess["history_summary"]
    executor: SASExecutor = sess["executor"]

    try:
        # 1. 코드 생성
        yield _evt("status", text="SAS 코드 생성 중...")
        code = await asyncio.to_thread(llm.generate, message, history, summary)
        yield _evt("code", code=code)

        # 2. 실행 (최대 3회 재시도)
        result = None
        for attempt in range(1, MAX_RETRIES + 1):
            msg = "SAS 실행 중..." if attempt == 1 else f"코드 수정 후 재시도 {attempt}/{MAX_RETRIES}..."
            yield _evt("status", text=msg)

            result = await asyncio.to_thread(executor.execute, code)

            if result.get("session_lost"):
                yield _evt("warning", text="⚠️ SAS 세션이 재연결되었습니다. WORK 라이브러리 데이터가 초기화되었을 수 있습니다.")

            if result["success"]:
                break

            if attempt < MAX_RETRIES:
                yield _evt("status", text="오류 감지 — 코드 수정 중...")
                code = await asyncio.to_thread(llm.fix, code, result["log"], history, summary)
                yield _evt("code_retry", code=code, attempt=attempt)
            else:
                error_lines = [l for l in result["log"].splitlines() if "ERROR" in l]
                yield _evt("error", log="\n".join(error_lines[:10]) or result["log"][:500])
                sess["history"].append({"role": "user", "content": message})
                sess["history"].append({"role": "assistant", "content": f"[실패]\n{code}"})
                yield _evt("done", success=False)
                return

        # 3. 결과 — tables는 {"type":"table"|"svg", "html":str} 목록
        output_items = result["tables"]  # [{type, html}, ...]
        output_text = (
            result["output"][:2000]
            if not output_items and result["output"].strip()
            else ""
        )
        yield _evt("result", items=output_items, output=output_text)

        # 4. 요약 — 테이블 HTML 텍스트 + 텍스트 출력을 결합
        yield _evt("status", text="결과 요약 생성 중...")
        table_text = _tables_to_text(output_items)
        raw_output = (result["output"] or result["log"])[:1000]
        summary_input = (table_text + "\n" + raw_output).strip()[:2500]
        text_summary = await asyncio.to_thread(llm.summarize, message, code, summary_input)
        yield _evt("summary", text=text_summary)

        # 히스토리 업데이트
        sess["history"].append({"role": "user", "content": message})
        sess["history"].append({
            "role": "assistant",
            "content": f"[SAS 코드]\n{code}\n\n[결과 요약]\n{text_summary}",
        })
        if len(sess["history"]) > MAX_HISTORY_TURNS * 2:
            turns = sess["history"][:2]
            sess["history"] = sess["history"][2:]
            sess["history_summary"] = await asyncio.to_thread(
                llm.compress_history, sess["history_summary"], turns
            )

        yield _evt("done", success=True)

    except Exception as e:
        yield _evt("error", log=str(e))
        yield _evt("done", success=False)


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/sas-logo-blue.png")
async def logo():
    return FileResponse(Path(__file__).parent.parent / "sas-logo-blue.png", media_type="image/png")


@app.get("/chat/stream")
async def chat_stream(message: str, session_id: str = "default"):
    return StreamingResponse(
        _stream(message, session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/session/reset")
async def reset_session(session_id: str = "default"):
    if session_id in _sessions:
        sess = _sessions.pop(session_id)
        if sess.get("idle_timer"):
            sess["idle_timer"].cancel()
        try:
            sess["executor"].close()
        except Exception:
            pass
    return {"status": "reset"}


def _query_dynamic_libs(executor: SASExecutor, libregs: tuple = ("WORK", "MYLIB")) -> dict[str, list]:
    """지정된 라이브러리의 데이터셋 목록을 SAS에서 실시간 조회.
    SASHELP는 정적(sashelp_datasets.yml)이므로 포함하지 않음.
    """
    result = {}
    for libref in libregs:
        try:
            r = executor.execute(f"""
                options linesize=256;
                data _null_;
                    set sashelp.vtable(where=(libname='{libref}'));
                    put 'DS_ROW=' memname '|' nobs '|' nvar;
                run;
            """)
            log = r.get("log", "")
            datasets = []
            for line in log.splitlines():
                if not line.startswith("DS_ROW="):
                    continue
                parts = line[7:].split("|")
                if len(parts) >= 3:
                    name = parts[0].strip()
                    obs  = parts[1].strip()
                    nvar = parts[2].strip()
                    if name:
                        datasets.append({"name": name, "label": f"{obs} obs, {nvar} vars", "columns": []})
            result[libref] = datasets
        except Exception as e:
            import traceback
            print(f"[WARN] _query_dynamic_libs({libref}) failed: {e}\n{traceback.format_exc()}")
            result[libref] = []
    return result


_VALID_SAS_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,31}$')


class DatasetOpRequest(BaseModel):
    lib: str
    name: str
    action: str          # rename | delete | move
    new_name: str = ""
    target_lib: str = ""
    session_id: str = ""


@app.post("/libraries/dataset")
async def dataset_op(req: DatasetOpRequest):
    """데이터셋 이름 바꾸기 / 삭제 / 이동"""
    if not _VALID_SAS_NAME.match(req.lib) or not _VALID_SAS_NAME.match(req.name):
        return {"success": False, "message": "잘못된 라이브러리 또는 데이터셋 이름"}

    if req.session_id and req.session_id in _sessions:
        executor = _sessions[req.session_id]["executor"]
    else:
        executor = _get_lib_executor()

    if req.action == "rename":
        if not _VALID_SAS_NAME.match(req.new_name):
            return {"success": False, "message": "잘못된 새 이름"}
        code = f"proc datasets lib={req.lib} nolist; change {req.name}={req.new_name}; run; quit;"

    elif req.action == "delete":
        code = f"proc datasets lib={req.lib} nolist; delete {req.name}; run; quit;"

    elif req.action == "move":
        if not _VALID_SAS_NAME.match(req.target_lib):
            return {"success": False, "message": "잘못된 대상 라이브러리"}
        code = (
            f"proc copy in={req.lib} out={req.target_lib} memtype=data; select {req.name}; run; "
            f"proc datasets lib={req.lib} nolist; delete {req.name}; run; quit;"
        )

    else:
        return {"success": False, "message": "알 수 없는 작업"}

    result = await asyncio.to_thread(executor.execute, code)
    if not result["success"]:
        error_lines = [l for l in result["log"].splitlines() if "ERROR" in l]
        return {"success": False, "message": "\n".join(error_lines[:3]) or "SAS 오류 발생"}
    return {"success": True, "message": "완료"}


# 공유 SAS 세션 (라이브러리 조회용 — 채팅 세션과 별도)
_lib_executor = None  # type: Optional[SASExecutor]


def _get_lib_executor() -> SASExecutor:
    global _lib_executor
    with _lib_lock:
        if _lib_executor is None:
            _lib_executor = SASExecutor()
    return _lib_executor


@app.get("/libraries")
async def get_libraries():
    """초기 라이브러리 트리 반환. SASHELP는 sashelp_datasets.yml에서 로드."""
    return {
        "top_names": list(_sashelp_top_names),   # 프론트 SASHELP_TOP10 대체
        "libraries": [
            {
                "name": "SASHELP",
                "datasets": _sashelp_datasets,
            },
            {
                "name": "WORK",
                "datasets": [],
                "writable": True,
                "note": "임시 라이브러리 — 실행 중 생성된 테이블이 표시됩니다",
            },
            {
                "name": "MYLIB",
                "datasets": [],
                "writable": True,
                "note": "영구 라이브러리 — ~/sas_workspace에 저장됩니다",
            },
        ],
    }


@app.get("/libraries/refresh")
async def refresh_libraries(session_id: str = "", initial: bool = False):
    """WORK / MYLIB 데이터셋 목록을 SAS에서 실시간 조회.
    initial=True 이면 MYLIB만 조회 (초기 로드 — WORK는 항상 비어있음).
    session_id가 있으면 해당 채팅 세션의 SAS 연결을 재사용.
    """
    libregs = ("MYLIB",) if initial else ("WORK", "MYLIB")
    try:
        if session_id and session_id in _sessions:
            executor = _sessions[session_id]["executor"]
        else:
            executor = _get_lib_executor()
        dynamic = await asyncio.wait_for(
            asyncio.to_thread(_query_dynamic_libs, executor, libregs),
            timeout=25,
        )
        return {"libraries": dynamic}
    except asyncio.TimeoutError:
        return {"error": "timeout", "libraries": {k: [] for k in libregs}}
    except Exception as e:
        return {"error": str(e), "libraries": {k: [] for k in libregs}}


@app.get("/libraries/columns")
async def get_column_info(lib: str, ds: str, session_id: str = ""):
    """특정 데이터셋의 컬럼 이름·라벨·타입 조회.
    session_id가 있으면 채팅 세션 executor 우선 사용 (WORK/SASUSER 접근 보장).
    """
    try:
        if session_id and session_id in _sessions:
            executor = _sessions[session_id]["executor"]
        else:
            executor = _get_lib_executor()
        cols = await asyncio.to_thread(executor.column_info, lib, ds)
        return {"columns": cols}
    except Exception as e:
        return {"columns": [], "error": str(e)}


@app.get("/health")
async def health():
    return {"status": "ok", "sessions": len(_sessions)}
