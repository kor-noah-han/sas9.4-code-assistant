# SAS 9.4 Code Assistant

자연어로 요청하면 SAS 코드를 자동 생성하고 SAS OnDemand for Academics에서 실행·시각화해주는 웹 기반 AI 어시스턴트

> 브라우저에서 한국어로 분석 요청 → SAS 코드 생성 → SAS OnDemand for Academics 실행 → 결과 표·그래프 렌더링 → 한국어 요약

---

## 주요 기능

| 기능 | 설명 |
| --- | --- |
| 자연어 → SAS 코드 | Claude가 요청을 SAS 코드로 변환 |
| 자동 실행 | SASPy를 통해 SAS OnDemand for Academics에서 즉시 실행 |
| 에러 자동 수정 | 에러 발생 시 코드 수정 후 최대 3회 재시도 |
| 결과 테이블 렌더링 | SAS HTML LST에서 테이블 추출·정제 후 브라우저 출력 |
| 그래프 렌더링 | ODS HTML5 인라인 SVG 그래프 브라우저 렌더링 |
| 한국어 요약 | 분석 결과를 한국어로 요약 |
| 멀티턴 대화 | 이전 분석 문맥 유지 (히스토리 자동 압축) |
| 라이브러리 트리 | SASHELP / WORK / MYLIB 데이터셋 브라우저 |
| 컬럼 툴팁 | 데이터셋 hover 시 컬럼 이름·라벨·타입 표시 |
| 데이터셋 관리 | 우클릭으로 이름 바꾸기 / 이동 / 삭제 |
| 영구 저장 | MYLIB (`~/sas_workspace`) 에 데이터 영구 저장 |
| MYLIB 자동 갱신 | 페이지 로드 시 MYLIB 백그라운드 조회 |
| 세션 관리 | 여러 대화 세션, 유휴 타임아웃 자동 정리 |
| SAS OnDemand for Academics Keepalive | 4분마다 ping으로 세션 타임아웃 방지 |
| SSE 스트리밍 | 코드 생성 → 실행 → 요약 진행 상황 실시간 전달 |

---

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
│       ├── sashelp_datasets.yml  # SASHELP 데이터셋 정의 50+ (단일 소스)
│       ├── stat_procs.yml        # 주요 SAS 분석 프로시저 참조
│       └── proc_sql_diff.yml     # PROC SQL vs 표준 SQL 차이점
├── SAS-ODA-JarFiles/        # SAS IOM 연결용 JAR 파일 (별도 다운로드 필요)
├── java.security.sas        # Java 암호화 정책 (SAS OnDemand for Academics 연결용)
└── .env                     # API 키 및 설정
```

---

## 환경 설정

### Step 1 — SAS OnDemand for Academics 계정 만들기

1. [https://welcome.oda.sas.com](https://welcome.oda.sas.com) 접속
2. **Register** 클릭 → 이름·소속기관·이메일 입력 후 가입
3. 가입 후 이메일 인증 (인증 메일이 스팸함에 들어가는 경우 있음)
4. 로그인 후 우측 상단 프로필 아이콘 → **Profile** 에서 **리전(Region)** 확인

   | 리전 | IOM 호스트 |
   | --- | --- |
   | US (East) | `odaws01-us-east-2.oda.sas.com` |
   | Asia Pacific (Region 1) | `odaws01-apse1.oda.sas.com` |
   | Asia Pacific (Region 2) | `odaws01-apse1-2.oda.sas.com` |
   | Europe (North) | `odaws01-eunorth-1.oda.sas.com` |

   > 로그인 URL이 `https://odamid-apse1-2.oda.sas.com/...` 형태라면 `apse1-2` 리전입니다.

---

### Step 2 — Java 8 설치

SAS OnDemand for Academics IOM 연결은 **Java 8 전용**입니다. Java 11/17/21은 동작하지 않습니다.

**Eclipse Temurin 8 (권장)**:

- [https://adoptium.net/temurin/releases/?version=8](https://adoptium.net/temurin/releases/?version=8) 접속
- OS에 맞는 JDK 8 `.pkg` (macOS) 또는 `.msi` (Windows) 다운로드 후 설치

설치 확인:

```bash
/Library/Java/JavaVirtualMachines/temurin-8.jdk/Contents/Home/bin/java -version
# 출력 예: openjdk version "1.8.0_xxx"
```

---

### Step 3 — SAS IOM JAR 파일 다운로드

SASPy가 SAS OnDemand for Academics에 IOM으로 연결하려면 SAS Integration Technologies 클라이언트 JAR 3개가 필요합니다.

**필요한 파일:**

```text
sas.rutil.jar
sas.rutil.nls.jar
sastpj.rutil.jar
```

**다운로드 방법:**

1. [https://support.sas.com/downloads/](https://support.sas.com/downloads/) 접속 (SAS 프로필 로그인 필요, SAS OnDemand for Academics 계정과 동일)
2. 검색창에 `SAS Integration Technologies` 또는 `IOM` 검색
3. **SAS Integration Technologies 9.4** 클라이언트 패키지 다운로드
4. 압축 해제 후 `sas.rutil.jar`, `sas.rutil.nls.jar`, `sastpj.rutil.jar` 3개를 `SAS-ODA-JarFiles/` 폴더에 복사

> SAS 지원 포털 접근이 어려운 경우: SASPy GitHub Issues나 커뮤니티에서 공유된 파일을 사용하기도 합니다. 단, 라이선스 확인 필요.

---

### Step 4 — 파이썬 가상환경 및 패키지 설치

```bash
# 프로젝트 루트에서 실행
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install saspy pandas fastapi uvicorn python-dotenv anthropic pyyaml
```

---

### Step 5 — JAR 파일을 SASPy 경로에 복사

```bash
# 파이썬 버전 확인 후 경로 조정 (3.9, 3.11 등)
SASPY_JAVA=.venv/lib/python3.9/site-packages/saspy/java

cp SAS-ODA-JarFiles/*.jar $SASPY_JAVA/
cp SAS-ODA-JarFiles/*.jar $SASPY_JAVA/iomclient/
```

> macOS에서 Python 버전이 다르면 `python3.9` 부분을 `python3.11` 등으로 변경

---

### Step 6 — SASPy 설정 파일 작성

아래 경로에 파일을 새로 생성합니다:

```text
.venv/lib/python3.9/site-packages/saspy/sascfg_personal.py
```

```python
SAS_config_names = ['oda']

oda = {
    # Java 8 실행 파일 절대 경로 (Step 2에서 설치한 경로)
    'java'      : '/Library/Java/JavaVirtualMachines/temurin-8.jdk/Contents/Home/bin/java',

    # Step 1에서 확인한 리전의 IOM 호스트 (2개 입력 권장)
    'iomhost'   : ['odaws01-apse1-2.oda.sas.com', 'odaws02-apse1-2.oda.sas.com'],
    'iomport'   : 8591,

    # ~/.authinfo 파일의 키 이름 (아래 Step 7과 동일하게 맞춤)
    'authkey'   : 'oda',

    'encoding'  : 'utf-8',

    'javaparms' : [
        # java.security.sas 파일의 절대 경로로 수정
        '-Djava.security.properties=/Users/yourname/dev/code-assistant/java.security.sas',
        '-Djava.net.preferIPv4Stack=true',
    ],
}
```

> **Windows 사용자**: `java` 경로를 `C:\\Program Files\\Eclipse Adoptium\\jdk-8...\\bin\\java.exe` 형태로 입력

---

### Step 7 — 인증 정보 설정

**`~/.authinfo`** — SAS OnDemand for Academics 로그인 정보 저장

```text
oda user 가입할때쓴이메일@example.com password 비밀번호
```

파일 권한을 반드시 600으로 설정 (소유자만 읽기/쓰기):

```bash
chmod 600 ~/.authinfo
```

> Windows에서는 `%USERPROFILE%\_authinfo` (언더스코어) 파일에 동일한 형식으로 작성

---

### Step 8 — 환경 변수 설정 (`.env` 파일)

프로젝트 루트에 `.env` 파일을 생성합니다:

```env
# Anthropic API 키 — https://console.anthropic.com 에서 발급
ANTHROPIC_API_KEY=sk-ant-api03-...

# 사용할 Claude 모델 (claude-sonnet-4-6 권장)
ANTHROPIC_MODEL=claude-sonnet-4-6
```

**환경 변수 목록:**

| 변수 | 필수 | 설명 | 예시 |
| --- | --- | --- | --- |
| `ANTHROPIC_API_KEY` | ✅ | Anthropic API 키 | `sk-ant-api03-...` |
| `ANTHROPIC_MODEL` | ✅ | 사용할 Claude 모델 ID | `claude-sonnet-4-6` |

> **API 키 발급**: [https://console.anthropic.com](https://console.anthropic.com) → API Keys → Create Key

---

## 실행

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload --port 8000
```

브라우저에서 `http://localhost:8000` 접속

서버 최초 실행 시 SAS OnDemand for Academics 세션 연결에 **30~60초** 소요됩니다. 라이브러리 패널에 스피너가 도는 동안 대기하세요.

---

## API 엔드포인트

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/chat/stream` | SSE 스트리밍 (코드 생성 → 실행 → 요약) |
| POST | `/session/reset` | 대화 세션 초기화 |
| GET | `/libraries` | 라이브러리 트리 초기 로드 (YAML 기반, 즉시 반환) |
| GET | `/libraries/refresh` | WORK / MYLIB 실시간 조회 (`?initial=true` 시 MYLIB만) |
| GET | `/libraries/columns` | 데이터셋 컬럼 정보 lazy 로드 |
| POST | `/libraries/dataset` | 데이터셋 이름 바꾸기 / 이동 / 삭제 |
| GET | `/health` | 서버 상태 확인 |

---

## 라이브러리 구조

| 라이브러리 | 설명 |
| --- | --- |
| `SASHELP` | 읽기 전용 샘플 데이터셋 50+ (YAML에서 즉시 로드, SAS 쿼리 없음) |
| `WORK` | 세션 임시 라이브러리 (세션 종료 시 삭제) |
| `MYLIB` | 영구 라이브러리 (`~/sas_workspace`, 페이지 로드 시 백그라운드 갱신) |

---

## 자주 발생하는 문제

### SAS 연결이 안 될 때

- `~/.authinfo` 권한이 600인지 확인: `ls -la ~/.authinfo`
- `sascfg_personal.py`의 `iomhost` 리전이 SAS OnDemand for Academics 계정 리전과 일치하는지 확인
- Java 버전 확인: `java -version` → 1.8.x 여야 함
- JAR 3개가 `saspy/java/` 와 `saspy/java/iomclient/` 양쪽에 복사됐는지 확인

### `java.security` 오류가 날 때

- `sascfg_personal.py`의 `-Djava.security.properties=` 경로가 실제 `java.security.sas` 파일의 **절대 경로**인지 확인
- 경로에 공백이 있으면 따옴표로 감싸기

### 라이브러리가 로딩 중에 멈출 때

- 첫 연결 시 SAS OnDemand for Academics 세션 초기화에 최대 60초 소요
- 이후 요청부터는 빠르게 응답

---

## 주의사항

- **Java 8 필수** — Eclipse Temurin 8 권장. Java 11+ 는 SAS OnDemand for Academics IOM 연결 불가
- **SAS JAR 3개** — SAS 지원 포털에서 별도 다운로드 필요 (라이선스 보호)
- **MYLIB** 데이터는 `~/sas_workspace/`에 물리적으로 저장되며, 세션 재시작 시 자동 복원
- **SASHELP** 메타데이터는 `sashelp_datasets.yml` 단일 소스 — 코드 생성 프롬프트에 자동 주입
- `.env` 파일과 `~/.authinfo`는 절대 Git에 커밋하지 마세요
