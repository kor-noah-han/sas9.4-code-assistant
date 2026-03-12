# SAS 9.4 Code Assistant

자연어로 요청하면 SAS 코드를 자동 생성하고 SAS OnDemand for Academics(ODA)에서 실행·시각화해주는 웹 기반 AI 어시스턴트

## 스크린샷

> 브라우저에서 한국어로 분석 요청 → SAS 코드 생성 → ODA 실행 → 결과 표·그래프 렌더링 → 한국어 요약

## 주요 기능

| 기능 | 설명 |
| --- | --- |
| 자연어 → SAS 코드 | Claude가 요청을 SAS 코드로 변환 |
| 자동 실행 | SASPy를 통해 SAS ODA에서 즉시 실행 |
| 에러 자동 수정 | 에러 발생 시 코드 수정 후 최대 3회 재시도 |
| 결과 테이블 렌더링 | SAS HTML LST에서 테이블 추출·정제 후 브라우저 출력 |
| 그래프 렌더링 | ODS HTML5 인라인 SVG 그래프 브라우저 렌더링 |
| 한국어 요약 | 분석 결과를 한국어로 요약 |
| 멀티턴 대화 | 이전 분석 문맥 유지 (히스토리 자동 압축) |
| 라이브러리 트리 | SASHELP / WORK / MYLIB 데이터셋 브라우저 |
| 컬럼 툴팁 | 데이터셋 hover 시 컬럼 이름·라벨·타입 표시 |
| 데이터셋 관리 | 우클릭으로 이름 바꾸기 / 이동 / 삭제 |
| 영구 저장 | MYLIB (`~/sas_workspace`) 에 데이터 영구 저장 |
| 세션 관리 | 여러 대화 세션, 유휴 타임아웃 자동 정리 |
| ODA Keepalive | 4분마다 ping으로 세션 타임아웃 방지 |
| SSE 스트리밍 | 코드 생성 → 실행 → 요약 진행 상황 실시간 전달 |

## 프로젝트 구조

```text
code-assistant/
├── backend/
│   ├── main.py              # FastAPI 서버 (SSE 스트리밍, 세션 관리, 라이브러리 API)
│   ├── sas_executor.py      # SASPy 세션 래퍼 (실행, 결과 추출, keepalive)
│   ├── code_generator.py    # Claude API 호출 (코드 생성 / 에러 수정 / 요약)
│   └── sas_agent.py         # 구버전 CLI 에이전트 (레거시)
├── frontend/
│   └── index.html           # 단일 파일 SPA (라이브러리 트리, 채팅 UI, SSE 클라이언트)
├── sashelp/
│   └── source/
│       ├── sashelp_datasets.yml  # SASHELP 데이터셋 정의 (단일 소스)
│       ├── stat_procs.yml        # 주요 SAS 분석 프로시저 참조
│       └── proc_sql_diff.yml     # PROC SQL vs 표준 SQL 차이점
├── SAS-ODA-JarFiles/        # SAS IOM 연결용 JAR 파일
├── java.security.sas        # Java 암호화 정책 (SAS ODA 연결용)
└── .env                     # API 키 및 설정
```

## 환경 설정

### 1. 가상환경 및 패키지 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install saspy pandas fastapi uvicorn python-dotenv anthropic pyyaml
```

### 2. SAS JAR 파일 복사

SAS ODA IOM 연결에 필요한 JAR 3개(`sas.rutil.jar`, `sas.rutil.nls.jar`, `sastpj.rutil.jar`)를 SAS 지원 포털에서 다운로드 후 복사:

```bash
SASPY_JAVA=.venv/lib/python3.9/site-packages/saspy/java

cp SAS-ODA-JarFiles/*.jar $SASPY_JAVA/
cp SAS-ODA-JarFiles/*.jar $SASPY_JAVA/iomclient/
```

### 3. SASPy 설정 파일

`.venv/lib/python3.9/site-packages/saspy/sascfg_personal.py` 생성:

```python
SAS_config_names = ['oda']

oda = {
    'java'      : '/Library/Java/JavaVirtualMachines/temurin-8.jdk/Contents/Home/bin/java',
    'iomhost'   : ['odaws01-apse1-2.oda.sas.com', 'odaws02-apse1-2.oda.sas.com'],
    'iomport'   : 8591,
    'authkey'   : 'oda',
    'encoding'  : 'utf-8',
    'javaparms' : [
        '-Djava.security.properties=/절대경로/java.security.sas',
        '-Djava.net.preferIPv4Stack=true',
    ],
}
```

> **리전 확인**: SAS ODA 로그인 후 우측 상단에서 리전 확인
>
> - Asia Pacific Region 2: `odaws01-apse1-2.oda.sas.com`

### 4. 인증 정보 설정

**`~/.authinfo`** (권한 600 필수):

```text
oda user 이메일@example.com password 비밀번호
```

```bash
chmod 600 ~/.authinfo
```

**`.env`** 파일:

```text
ANTHROPIC_API_KEY = sk-ant-...
ANTHROPIC_MODEL   = claude-sonnet-4-6
```

## 실행

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload --port 8000
```

브라우저에서 `http://localhost:8000` 접속

## API 엔드포인트

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/chat/stream` | SSE 스트리밍 (코드 생성 → 실행 → 요약) |
| POST | `/session/reset` | 대화 세션 초기화 |
| GET | `/libraries` | 라이브러리 트리 초기 로드 (YAML 기반) |
| GET | `/libraries/refresh` | WORK / MYLIB / SASHELP 실시간 조회 |
| GET | `/libraries/columns` | 데이터셋 컬럼 정보 lazy 로드 |
| POST | `/libraries/dataset` | 데이터셋 이름 바꾸기 / 이동 / 삭제 |
| GET | `/health` | 서버 상태 확인 |

## 라이브러리 구조

| 라이브러리 | 설명 |
| --- | --- |
| `SASHELP` | 읽기 전용 샘플 데이터셋 (cars, iris, heart 등) |
| `WORK` | 세션 임시 라이브러리 (세션 종료 시 삭제) |
| `MYLIB` | 영구 라이브러리 (`~/sas_workspace`, 세션 시작 시 자동 할당) |

## 주의사항

- **Java 8 필수** — Eclipse Temurin 8 권장 (다른 버전은 SAS ODA IOM 연결 불가)
- **SAS JAR 3개**는 SAS 지원 포털에서 별도 다운로드 필요 (라이선스 보호)
- **MYLIB** 데이터는 `~/sas_workspace/`에 물리적으로 저장되며, 세션 재시작 시 자동 복원
