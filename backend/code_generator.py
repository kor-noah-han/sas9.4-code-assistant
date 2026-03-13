"""
GPT 기반 SAS 코드 생성 및 에러 수정
"""

import os
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv
import yaml

load_dotenv()

_base_url = os.getenv("OPENAI_BASE_URL")
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    **( {"base_url": _base_url} if _base_url else {} ),
)
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# PROC SQL 차이점 로드
_root = Path(__file__).parent.parent
_diff_path = _root / "sashelp/source/proc_sql_diff.yml"
_proc_sql_diff = ""
if _diff_path.exists():
    _data = yaml.safe_load(_diff_path.read_text(encoding="utf-8"))
    _proc_sql_diff = "\n".join(
        f"- [{d['category']}] Standard SQL: {d['standard_sql']} → PROC SQL: {d['proc_sql']}"
        for d in _data.get("differences", [])
    )

# 분석 프로시저 참조 로드
_procs_path = _root / "sashelp/source/stat_procs.yml"
_stat_procs = ""
if _procs_path.exists():
    _procs_data = yaml.safe_load(_procs_path.read_text(encoding="utf-8"))
    lines = []
    for p in _procs_data.get("procedures", []):
        opts = "; ".join(p.get("key_options", []))
        ex = p.get("example", "").strip().replace("\n", " ")
        lines.append(f"- {p['name']}: {p['purpose']} | 옵션: {opts} | 예: {ex}")
    _stat_procs = "\n".join(lines)

# sashelp 데이터셋 목록 로드 (단일 소스: sashelp_datasets.yml)
# top: true → 컬럼명 포함, top: false → 이름+라벨만 (토큰 절약)
_ds_path = _root / "sashelp/source/sashelp_datasets.yml"
_sashelp_ds_ref = ""
if _ds_path.exists():
    _ds_data = yaml.safe_load(_ds_path.read_text(encoding="utf-8"))
    _ds_lines = []
    for d in _ds_data.get("datasets", []):
        if d.get("top", False):
            cols = ", ".join(c["name"] for c in d.get("columns", []))
            _ds_lines.append(f"- sashelp.{d['name']}: {d.get('label', '')} | columns: {cols}")
        else:
            _ds_lines.append(f"- sashelp.{d['name']}: {d.get('label', '')}")
    _sashelp_ds_ref = "\n".join(_ds_lines)

SYSTEM_PROMPT = """You are a SAS programming expert for SAS OnDemand for Academics (ODA).

Rules for code generation:
- Output ONLY raw SAS code, no markdown, no explanation
- Always end procedures with RUN;
- Use ODS OUTPUT or PROC PRINT to produce tabular output when showing results
- Keep code concise and correct

Library rules (CRITICAL):
- Default to WORK when no library is specified
- Use the exact library the user explicitly mentions (mylib, work, etc.) — never override it
- When user says "영구 저장" or mentions a permanent library, use mylib (pre-assigned to ~/sas_workspace)
""" + (f"\nAvailable sashelp datasets:\n{_sashelp_ds_ref}\n" if _sashelp_ds_ref else "") \
  + (f"\nPROC SQL vs Standard SQL key differences:\n{_proc_sql_diff}\n" if _proc_sql_diff else "") \
  + (f"\nAvailable SAS analysis procedures:\n{_stat_procs}\n" if _stat_procs else "")

FIX_PROMPT = """You are a SAS debugging expert.
The SAS code below produced an error. Fix it and return ONLY the corrected SAS code, no explanation.
"""

SUMMARY_PROMPT = """You are a data analyst.
Summarize the SAS analysis results in clear, concise Korean.
Focus on key numbers and insights. Be brief (3-5 sentences max).
"""

COMPRESS_PROMPT = """You are a conversation summarizer.
Summarize the following conversation history in Korean, preserving key analysis context:
- What datasets were analyzed
- What analyses were performed
- Key findings or results referenced

Be concise (5-7 sentences max). This summary will be used as context for future analysis requests.
"""


def _extract_code(raw: str) -> str:
    """마크다운 코드블록 제거"""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        end = -1 if lines[-1].strip() == "```" else len(lines)
        return "\n".join(lines[1:end]).strip()
    return raw


def generate(user_request: str, history: list[dict], summary: str = "") -> str:
    """자연어 요청 → SAS 코드 생성"""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if summary:
        messages.append({"role": "user", "content": f"[이전 대화 요약]\n{summary}"})
        messages.append({"role": "assistant", "content": "이전 대화 맥락을 파악했습니다."})
    messages += history
    messages.append({"role": "user", "content": user_request})

    response = client.chat.completions.create(model=MODEL, messages=messages)
    return _extract_code(response.choices[0].message.content)


def fix(code: str, error_log: str, history: list[dict], summary: str = "") -> str:
    """에러 발생 SAS 코드 수정"""
    messages = [{"role": "system", "content": FIX_PROMPT}]
    if summary:
        messages.append({"role": "user", "content": f"[이전 대화 요약]\n{summary}"})
        messages.append({"role": "assistant", "content": "이전 대화 맥락을 파악했습니다."})
    messages += history
    messages.append({
        "role": "user",
        "content": f"SAS 코드:\n{code}\n\n에러 로그:\n{error_log}",
    })

    response = client.chat.completions.create(model=MODEL, messages=messages)
    return _extract_code(response.choices[0].message.content)


def compress_history(old_summary: str, turns_to_compress: list[dict]) -> str:
    """오래된 대화 턴들을 요약 압축"""
    content = ""
    if old_summary:
        content += f"[이전 요약]\n{old_summary}\n\n"
    content += "[추가 대화]\n"
    for msg in turns_to_compress:
        role = "사용자" if msg["role"] == "user" else "어시스턴트"
        content += f"{role}: {msg['content']}\n\n"

    messages = [
        {"role": "system", "content": COMPRESS_PROMPT},
        {"role": "user", "content": content},
    ]
    response = client.chat.completions.create(model=MODEL, messages=messages, max_tokens=250)
    return response.choices[0].message.content.strip()


def summarize(user_request: str, code: str, output: str) -> str:
    """실행 결과 한국어 요약"""
    messages = [
        {"role": "system", "content": SUMMARY_PROMPT},
        {
            "role": "user",
            "content": f"요청: {user_request}\n\nSAS 코드:\n{code}\n\n실행 결과:\n{output[:3000]}",
        },
    ]

    response = client.chat.completions.create(model=MODEL, messages=messages, max_tokens=250)
    return response.choices[0].message.content.strip()
