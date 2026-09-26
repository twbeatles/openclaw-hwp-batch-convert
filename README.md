# 📄 HWP Batch Convert (openclaw-hwp-batch-convert)

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows%20(Native)-lightgrey.svg)](https://www.microsoft.com/windows)
[![Tests](https://img.shields.io/badge/tests-22%20passed%20(100%25)-brightgreen.svg)](tests/)
[![Supported Formats](https://img.shields.io/badge/export-PDF%20%7C%20DOCX%20%7C%20HWPX%20%7C%20ODT%20%7C%20HTML%20%7C%20Images-orange.svg)](#-지원-형식-supported-formats)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![OpenClaw Skill](https://img.shields.io/badge/OpenClaw-AgentSkill-purple.svg)](SKILL.md)

> **한컴오피스 한글(HWP / HWPX) 문서를 PDF, DOCX, HWPX 등으로 빠르고 안전하게 일괄 변환하는 엔터프라이즈급 자동화 엔진 & OpenClaw 에이전트 스킬**

Windows 환경에서 수백~수천 개의 한글 문서를 처리할 때 발생하는 **보안 승인 팝업 멈춤, 호환 확인 모달 멈춤, 인쇄 설정 왜곡, COM 좀비 프로세스 누수, 원본 파일 덮어쓰기** 문제를 완벽하게 해결한 안정성 중심의 배치 변환 솔루션입니다.

---

## 📌 목차 (Table of Contents)

1. [30초 빠른 시작 (TL;DR)](#-30초-빠른-시작-tldr)
2. [왜 hwp-batch-convert인가? (차별점 비교)](#-왜-hwp-batch-convert인가-차별점-비교)
3. [변환 파이프라인 아키텍처](#-변환-파이프라인-아키텍처)
4. [지원 형식 (Supported Formats)](#-지원-형식-supported-formats)
5. [설치 및 요구사항](#-설치-및-요구사항)
6. [실전 사용 시나리오별 예제](#-실전-사용-시나리오별-예제)
7. [CLI 옵션 전체 레퍼런스](#-cli-옵션-전체-레퍼런스)
8. [4대 엔터프라이즈 안전 메커니즘 (Deep Dive)](#-4대-엔터프라이즈-안전-메커니즘-deep-dive)
9. [OpenClaw / AI 에이전트 연동 가이드](#-openclaw--ai-에이전트-연동-가이드)
10. [자주 묻는 질문 & 문제 해결 (FAQ & Troubleshooting)](#-자주-묻는-질문--문제-해결-faq--troubleshooting)
11. [테스트 및 자체 진단](#-테스트-및-자체-진단)
12. [기원 및 라이선스](#-기원-및-라이선스)

---

## ⚡ 30초 빠른 시작 (TL;DR)

### 1) 의존성 설치
```powershell
pip install pywin32
```

### 2) 대표 실행 명령어

* **폴더 내 모든 HWP를 PDF로 변환 (원본과 같은 위치)**
  ```powershell
  python scripts/hwp_batch_convert.py "C:\문서폴더" --format PDF --same-location
  ```

* **하위 폴더까지 포함하여 별도 폴더에 PDF로 일괄 변환**
  ```powershell
  python scripts/hwp_batch_convert.py "C:\계약서" --format PDF --output-dir "C:\변환완료" --include-sub
  ```

* **보안 팝업 자동 승인 + 자동 백업 + 안전 타임아웃 활성화 (권장 실무 옵션)**
  ```powershell
  python scripts/hwp_batch_convert.py "C:\공문서" --format PDF --output-dir "C:\결과" `
    --auto-allow-dialogs --backup --kill-owned-hwp-on-timeout `
    --json --report-json "C:\결과\report.json"
  ```

> ⚠️ **주의**: `--same-location` 또는 `--output-dir` 중 **하나를 반드시 지정**해야 합니다.

---

## 💡 왜 hwp-batch-convert인가? (차별점 비교)

일반적인 오픈소스 HWP 변환 스크립트는 단순 1회성 변환에는 동작하지만, **실무 대량 배치 작업에서는 팝업 대기나 프로세스 먹통으로 중단**되기 쉽습니다. `hwp-batch-convert`는 [twbeatles/HwpMate](https://github.com/twbeatles/HwpMate)의 엔터프라이즈 아키텍처를 계승하여 완벽한 무인 자동화를 보장합니다.

| 비교 항목 | 일반 변환 스크립트 | **hwp-batch-convert** |
| :--- | :--- | :--- |
| **보안 승인 팝업 대응** | 수동 클릭할 때까지 무한 대기 | **2단계 차단망**: 보안 DLL 자동 등록 + Win32 화이트리스트 자동 클릭 |
| **DOCX/RTF 호환 창** | "배치가 변경될 수 있습니다" 모달에서 멈춤 | **호환 창 전용 백그라운드 응답기**(`HwpCompatDialogResponder`)가 자동 승인 |
| **PDF 인쇄 품질** | 모아찍기/용지 설정 꼬임 그대로 반영 | **`PrintMethod=0` 강제 리셋**으로 정규 1쪽 인쇄 복원 |
| **PDF 저장 실패 폴백** | SaveAs 실패 시 즉시 에러 중단 | **가상 PDF 프린터(`PrintToPDFEx`) 자동 우회** & `%PDF` 매직 헤더 검증 |
| **원본 파일 보호** | 실수로 원본 `.hwp` 덮어씀 | **TOCTOU 원자적 방어**: `--overwrite`여도 원본 한글 파일은 덮어쓰지 않음 |
| **다중 산출물 관리** | HTML(`.files`), 이미지 실패 시 찌꺼기 잔류 | 실패 시 **보조 산출물(Auxiliary Artifacts) 자동 추적 및 롤백 삭제** |
| **프로세스 관리** | 변환 실패 시 숨겨진 한글 프로세스 누수 | **Toolhelp32 스냅샷 기반 PID 추적** & 타임아웃 시 소유 프로세스 강제 회수 |
| **AI 에이전트 연동** | 텍스트 로그 파싱 필요 | **사전 계획(`--plan-only`)** + **모의 실행(`--mode mock`)** + **정밀 JSON 리포트** |

---

## 🔄 변환 파이프라인 아키텍처

```mermaid
flowchart TD
    A[입력 소스 지정: 파일/폴더] --> B[1. 배치 계획 수립 BatchPlanner]
    B --> C{plan-only 또는 mock 모드?}
    C -- Yes --> D[계획 확인 및 Mock 가상 변환 완료]
    C -- No --> E[2. 사전 보안 준비: FilePathCheckDLL 등록]
    
    E --> F[3. 한글 COM 인스턴스 기동 & PID 스냅샷]
    F --> G[4. 백그라운드 안전 감시자 기동]
    subgraph Watchers [백그라운드 안전 감시자]
        G1[AutoAllowDialogWatcher: 접근 허용 팝업 자동 클릭]
        G2[HwpCompatDialogResponder: 서식 호환 경고창 자동 계속]
    end
    G --> Watchers
    
    Watchers --> H[5. 파일 단위 변환 루프]
    H --> I[원본 자동 백업 (--backup)]
    I --> J[TOCTOU 충돌 재검증 & 유니크 파일명 할당]
    J --> K[인쇄 설정 리셋 PrintMethod=0]
    K --> L{포맷별 변환 실행}
    
    L -- PDF --> M1[SaveAs 시도 -> 실패 시 PrintToPDFEx 폴백]
    M1 --> M2[%PDF 헤더 무결성 검증]
    L -- 기타 포맷 --> N1[SaveAs 변환 & 보조 산출물 추적]
    
    M2 --> O{성공 여부}
    N1 --> O
    O -- 실패 --> P[보조 산출물/깨진 파일 롤백 & 재시도]
    O -- 성공 --> Q[감사 메타데이터 기록]
    
    Q --> R{남은 파일 있음?}
    P --> R
    R -- Yes --> H
    R -- No --> S[6. 리소스 해제 & JSON 감사 리포트 출력]
```

---

## 🗂️ 지원 형식 (Supported Formats)

입력 파일(`.hwp`, `.hwpx`)을 아래의 모든 형식으로 상호 변환할 수 있습니다.

| 형식 플래그 (`--format`) | 확장자 | 저장 엔진 및 내부 포맷 | 비고 |
| :--- | :---: | :--- | :--- |
| **`PDF`** (기본값) | `.pdf` | `PDF` / `PrintToPDFEx` | SaveAs 실패 시 가상 프린터 자동 폴백, `%PDF` 검증 |
| **`DOCX`** | `.docx` | `OOXML` | '변환 문서' 호환 경고창 자동 승인 지원 |
| **`HWPX`** | `.hwpx` | `HWPX` | 한글 표준 개방형 문서 포맷 |
| **`HWP`** | `.hwp` | `HWP` | 한글 97~3.0/2002 호환 바이너리 포맷 |
| **`ODT`** | `.odt` | `ODF` (대체: `ODT`) | 한글 2022+ 실측 반영: `ODF` 우선 호출 |
| **`HTML`** | `.html` | `HTML` | 본문 이미지(`PIC*.png`) 및 `.files` 폴더 자동 추적 |
| **`RTF`** | `.rtf` | `RTF` | 서식 있는 텍스트 포맷 |
| **`TXT`** | `.txt` | `TEXT` | 순수 텍스트 추출 |
| **`PNG`** | `.png` | `PNG` | 다중 페이지 이미지 자동 추적 (`{stem}001.png` 등) |
| **`JPG`** | `.jpg` | `JPG` | 고압축 이미지 포맷 |
| **`BMP`** | `.bmp` | `BMP` | 비압축 비트맵 포맷 |
| **`GIF`** | `.gif` | `GIF` | 그래픽 인터체인지 포맷 |

---

## 🚀 설치 및 요구사항

### 시스템 요구사항
* **운영체제**: Windows 10, Windows 11, Windows Server 2016+
* **필수 소프트웨어**: **한컴오피스 한글 2010 이상** 정식 설치 (한글 2014, 2018, 2020, 2022, 2024 지원)
* **Python**: Python 3.10 이상

### 설치 가이드
```powershell
# 1. 저장소 클론
git clone https://github.com/twbeatles/openclaw-hwp-batch-convert.git
cd openclaw-hwp-batch-convert

# 2. 필수 의존성 설치
pip install pywin32

# (선택) 단위 테스트 및 개발 의존성 설치
pip install pytest
```

---

## 💻 실전 사용 시나리오별 예제

### 시나리오 1: 단일 파일 및 폴더 일괄 변환
```powershell
# 단일 파일 변환 (같은 폴더에 output.pdf 생성)
python scripts/hwp_batch_convert.py "C:\docs\보고서.hwp" --format PDF --same-location

# 폴더 내 모든 HWP/HWPX를 PDF로 변환하여 별도 폴더에 저장
python scripts/hwp_batch_convert.py "C:\docs\input" --format PDF --output-dir "C:\docs\output"

# 여러 개별 파일을 지정하여 DOCX로 변환
python scripts/hwp_batch_convert.py "C:\docs\1.hwp" "C:\docs\2.hwpx" --format DOCX --output-dir "C:\docs\word"
```

### 시나리오 2: 하위 폴더 트리 구조를 유지하며 재귀 변환
```powershell
# 하위 디렉터리 구조를 그대로 복제하며 PDF 변환 (기본값이 --include-sub)
python scripts/hwp_batch_convert.py "C:\부서별문서" --format PDF --output-dir "C:\부서별PDF" --include-sub

# 여러 부서 폴더를 동시에 넣고 최상위 폴더명을 보존하고 싶을 때 (--preserve-source-root)
python scripts/hwp_batch_convert.py "C:\영업부" "C:\개발부" --format PDF --output-dir "C:\통합출력" --preserve-source-root
```

### 시나리오 3: 엔터프라이즈 대량 무인 배치 (안전 종합 세트)
야간 배치나 대용량 변환 시 프로세스가 멈추지 않도록 백업, 팝업 자동 승인, 고아 프로세스 정리를 활성화합니다.
```powershell
python scripts/hwp_batch_convert.py "C:\대용량문서" `
  --format PDF `
  --output-dir "C:\대용량PDF" `
  --auto-allow-dialogs `
  --auto-continue-compat-dialog `
  --backup `
  --backup-max-per-stem 10 `
  --startup-timeout-seconds 30 `
  --file-timeout-seconds 120 `
  --kill-owned-hwp-on-timeout `
  --retry-count 2 `
  --report-json "C:\대용량PDF\audit_report.json"
```

### 시나리오 4: 사전 시뮬레이션 (Dry-Run & Mock)
실제 한글 프로그램을 실행하지 않고 작업 계획이나 자동화 파이프라인을 검증합니다.
```powershell
# 1) 작업 계획만 미리보기 (어떤 파일이 대상이고 어디로 저장되는지 확인)
python scripts/hwp_batch_convert.py "C:\문서" --format PDF --output-dir "C:\출력" --plan-only --json

# 2) 한글이 설치되지 않은 환경이나 CI 환경에서 모의 변환 검증 (--mode mock)
python scripts/hwp_batch_convert.py "C:\문서" --format PDF --output-dir "C:\출력" --mode mock --json
```

---

## 📋 CLI 옵션 전체 레퍼런스

`python scripts/hwp_batch_convert.py [sources ...] [옵션]`

### 1. 입출력 및 대상 지정
| 옵션 | 기본값 | 설명 |
| :--- | :---: | :--- |
| `sources` | 필수 | 변환할 파일 또는 폴더 경로 (공백으로 구분하여 여러 개 지정 가능) |
| `--same-location` | - | 원본 파일과 동일한 디렉터리에 출력 파일 생성 (`--output-dir`과 배타적) |
| `--output-dir <DIR>` | - | 변환 산출물이 저장될 루트 디렉터리 (`--same-location`과 배타적) |
| `--include-sub` | **True** | 하위 폴더를 재귀적으로 탐색하여 변환 |
| `--no-include-sub` | - | 지정한 최상위 폴더의 파일만 변환하고 하위 폴더는 제외 |
| `--preserve-source-root` | False | 여러 입력 소스 지정 시, 출력 폴더 아래에 입력 폴더명을 유지하여 격리 저장 |

### 2. 포맷 및 변환 제어
| 옵션 | 기본값 | 설명 |
| :--- | :---: | :--- |
| `--format <FORMAT>` | `PDF` | 출력 형식 (`PDF`, `DOCX`, `HWPX`, `HWP`, `ODT`, `HTML`, `RTF`, `TXT`, `PNG`, `JPG`, `BMP`, `GIF`) |
| `--pdf-export-mode` | `saveas_first` | PDF 변환 전략 (`saveas_first`: 고품질 우선, `print_to_pdf_ex_first`: 모아찍기 해제 우선) |
| `--retry-count <N>` | `1` | 변환 실패 시 자동 재시도 횟수 (0~3) |
| `--overwrite` | False | 동일 이름의 출력 파일이 이미 존재할 경우 덮어쓰기 허용 (단, 원본 `.hwp/.hwpx`는 절대 덮어쓰지 않음) |

### 3. 안전성 & 원본 백업
| 옵션 | 기본값 | 설명 |
| :--- | :---: | :--- |
| `--backup` | False | 변환 전 원본 파일을 각 소스 폴더 내 `backup/` 디렉터리에 타임스탬프와 함께 자동 백업 |
| `--backup-max-per-stem <N>` | `20` | 동일 파일명(stem)당 보관할 최대 백업 수 (초과 시 오래된 백업부터 자동 정리) |

### 4. 보안 팝업 & 프로세스 안정화
| 옵션 | 기본값 | 설명 |
| :--- | :---: | :--- |
| `--ensure-security-module` | **True** | 보안 모듈(`FilePathCheckDLL`) 자동 설치 및 레지스트리 4종 경로 등록 보장 |
| `--no-ensure-security-module` | - | 보안 모듈 등록 과정 생략 |
| `--auto-allow-dialogs` | False | 한글 보안 접근 허용 팝업을 백그라운드에서 감지하여 자동 클릭 (소유 PID 한정) |
| `--auto-continue-compat-dialog` | **True** | DOCX/RTF 변환 시 "배치가 변경될 수 있습니다" 확인 모달에 자동 '계속' 응답 |
| `--no-auto-continue-compat-dialog` | - | 호환 확인 모달 자동 응답 비활성화 |
| `--startup-timeout-seconds <SEC>` | `20.0` | 한글 COM 객체 초기화 대기 한계 시간(초) |
| `--file-timeout-seconds <SEC>` | `120.0` | 단일 파일 변환 제한 시간(초) |
| `--kill-owned-hwp-on-timeout` | False | 타임아웃 발생 시 현재 변환 작업이 생성한 한글 프로세스만 정확히 추적하여 강제 종료 |

### 5. 실행 모드 및 리포트
| 옵션 | 기본값 | 설명 |
| :--- | :---: | :--- |
| `--mode {real,mock}` | `real` | `real`: 실제 한글 COM 변환 실행, `mock`: 테스트용 가상 변환 |
| `--plan-only` | False | 실제 변환을 수행하지 않고 대상 파일 수 및 출력 경로 계획만 출력 |
| `--json` | False | 표준 출력(stdout)으로 기계 판독 가능한 JSON 형식 결과 출력 |
| `--report-json <PATH>` | - | 변환 요약 및 개별 파일 감사 메타데이터를 지정한 JSON 파일로 저장 |
| `--fail-fast` | False | 한 파일이라도 변환에 실패하면 남은 대기열 작업을 즉시 중단 |
| `--allow-partial-success` | False | 일부 파일 실패가 발생해도 프로세스 종료 코드를 성공(`0`)으로 반환 |
| `--allow-empty` | False | 변환 대상 파일이 없어도 에러 대신 빈 성공 결과 반환 |
| `--self-test-dialog-handler` | - | 팝업 자동 클릭 로직의 Win32 로컬 UI 자체 테스트 실행 |

---

## 🛡️ 4대 엔터프라이즈 안전 메커니즘 (Deep Dive)

### 1. 2단계 보안 승인 팝업 무력화 아키텍처
한컴오피스는 외부 프로그램이 COM API로 문서에 접근할 때 보안 승인 팝업을 띄워 프로세스를 차단합니다. 본 도구는 2중 안전망으로 이를 완벽히 무인화합니다.
* **1단계 (원천 차단)**: 한컴 공식 보안 모듈(`FilePathCheckDLL`)의 SHA-256 무결성을 검증한 뒤 `%LOCALAPPDATA%`에 배치하고, `HKCU\Software\HNC\HwpAutomation\Modules` 및 `HwpCtrl\Modules` 레지스트리 4종에 자동 등록하여 팝업 발생 자체를 예방합니다.
* **2단계 (런타임 폴백)**: 보안 모듈이 우회되는 특수 환경에서는 `AutoAllowDialogWatcher`가 백그라운드 스레드로 동작합니다. 현재 스크립트가 실행한 HWP 프로세스(PID) 소유의 창 중 **제목이 '한글'이고 본문에 '접근하려는 시도'가 포함된 창만** 화이트리스트 검증하여 `모두 허용` 버튼을 자동 클릭합니다.

### 2. DOCX/RTF 서식 호환 경고창 자동 계속 (`HwpCompatDialogResponder`)
한글 문서를 DOCX나 RTF로 저장할 때 간헐적으로 발생하는 *"변환 문서(배치가 변경될 수 있습니다. 계속?)"* 확인 모달은 COM `SetMessageBoxMode` 설정을 무시하고 버튼 컨트롤 핸들도 제공하지 않습니다.
* 본 도구는 Win32 창 메시지 루프를 통해 해당 다이얼로그를 감지하고, **소유 HWP 프로세스에 한정하여 `Y` 키 입력을 안전하게 전달**함으로써 변환 중단을 방지합니다.

### 3. 무손실 원본 보호 & TOCTOU 방어
* **원본 한글 문서 영구 보호**: `--overwrite` 옵션이 켜져 있더라도 원본이 `.hwp`/`.hwpx`인 경우, 변환 결과가 원본을 덮어쓰지 못하도록 보호합니다 (예: `doc.hwpx`를 HWP로 변환할 때 기존 `doc.hwp` 원본을 보존하고 `doc (1).hwp`로 분기).
* **저장 직전 원자적 재검증 (TOCTOU 방어)**: 계획 수립 시점과 실제 저장 시점 사이에 외부 프로세스가 동일한 이름의 파일을 생성하더라도 충돌을 재감지하여 동적으로 유니크한 번호를 할당합니다.
* **보조 산출물(Auxiliary Artifacts) 롤백**: HTML 변환 시 생기는 본문 이미지(`PIC*.png`, `.files` 폴더)나 다중 페이지 이미지 산출물을 스냅샷으로 추적하여, 변환 도중 실패할 경우 찌꺼기 파일을 원상 복구합니다.

### 4. PDF 인쇄 품질 복원 & 가상 프린터 자동 폴백
* **인쇄 설정 정규화**: 이전 사용자가 설정해 둔 모아찍기(2쪽 모아찍기 등)나 축소 인쇄 설정이 남아있을 경우 PDF 품질이 깨지는 문제를 막기 위해, 인쇄 파라미터를 일반 인쇄(`PrintMethod=0`)로 강제 초기화합니다.
* **가상 PDF 프린터 자동 폴백**: `SaveAs("PDF")`가 실패하거나 특정 한글 빌드에서 PDF 드라이버 에러가 발생하면, 즉시 `PrintToPDFEx` 및 `RunToPDF` 가상 프린터 루틴으로 자동 전환하여 PDF를 완성합니다.

---

## 🤖 OpenClaw / AI 에이전트 연동 가이드

`openclaw-hwp-batch-convert`는 [OpenClaw](https://github.com/openclaw/openclaw)의 **AgentSkill 규격**을 완벽하게 준수합니다.

### OpenClaw 자연어 프롬프트 예시
* *"다운로드 폴더에 있는 HWP 파일들 전부 PDF로 변환해줘."*
* *"계약서 폴더 하위 문서들 전부 DOCX로 변환하기 전에 대상 목록 먼저 보여줘 (Plan-only)."*
* *"보안 팝업 때문에 멈추지 않게 백그라운드로 안전하게 변환하고 결과 리포트 남겨줘."*

### 정밀 JSON 감사 리포트 스키마
`--json` 또는 `--report-json`을 지정하면 에이전트가 상태를 즉시 파악할 수 있는 고정 스키마가 제공됩니다.

```json
{
  "summary": {
    "format_type": "PDF",
    "mode": "real",
    "total_requested": 120,
    "success_count": 119,
    "failed_count": 1,
    "skipped_count": 0,
    "total_created_files": 119,
    "total_output_size_bytes": 104857600,
    "elapsed_seconds": 45.2,
    "progid_used": "HWPFrame.HwpObject",
    "pdf_export_mode": "saveas_first",
    "backup_enabled": true,
    "backup_count": 119,
    "warnings": [],
    "auto_dialog_enabled": true,
    "auto_dialog_detected_count": 2,
    "auto_dialog_clicked_count": 2
  },
  "tasks": [
    {
      "input_file": "C:\\docs\\보고서.hwp",
      "output_file": "C:\\output\\보고서.pdf",
      "source_root": "C:\\docs",
      "status": "success",
      "detail": "",
      "created_files": ["C:\\output\\보고서.pdf"],
      "output_size": 892301,
      "output_mtime": 1790407594.0,
      "save_format": "PDF",
      "export_method": "SaveAs",
      "backup_file": "C:\\docs\\backup\\보고서_20260926_162634_123456.hwp"
    }
  ],
  "auto_dialog_events": [
    {
      "window_title": "한글",
      "window_text": "한글 문서에 접근하려는 시도를 허용하시겠습니까?",
      "button_text": "모두 허용",
      "process_id": 14220,
      "clicked": true,
      "reason": "match"
    }
  ]
}
```

---

## ❓ 자주 묻는 질문 & 문제 해결 (FAQ & Troubleshooting)

### Q1. "한글 문서에 접근하려는 시도를 허용하시겠습니까?" 팝업에서 멈춥니다.
* **해결**: 본 도구는 기본적으로 1단계 레지스트리 자동 등록(`--ensure-security-module`)이 켜져 있습니다. 만약 사내 보안 정책 등으로 등록이 막혀 있다면 `--auto-allow-dialogs` 옵션을 추가하세요. 백그라운드 감시자가 팝업을 감지하여 0.2초 이내에 `모두 허용`을 자동 클릭합니다.
* 로컬에서 클릭 동작을 직접 확인하려면 `python scripts/hwp_batch_convert.py --self-test-dialog-handler`를 실행해 보세요.

### Q2. DOCX 변환 시 "배치가 변경될 수 있습니다" 확인 창이 뜹니다.
* **해결**: 본 도구는 기본적으로 `--auto-continue-compat-dialog`가 활성화되어 있습니다. 백그라운드 키보드 응답기가 해당 창에 즉시 'Y' 키를 전송하므로 사용자가 개입할 필요가 없습니다.

### Q3. 백그라운드에 `Hwp.exe` 프로세스가 남아 컴퓨터가 느려집니다.
* **해결**: 변환 작업 시 `--kill-owned-hwp-on-timeout` 옵션을 지정하면, 타임아웃 발생 시 이번 변환 세션이 직접 생성한 HWP 프로세스만 PID 기반으로 정밀 추적하여 안전하게 강제 종료합니다. (사용자가 이미 열어둔 다른 한글 창은 절대 종료하지 않습니다.)

### Q4. 변환된 PDF의 글자가 2쪽 모아찍기 형태로 작게 나옵니다.
* **해결**: 원본 한글 문서에 이전 인쇄 설정이 저장되어 있기 때문입니다. 본 도구의 `HwpPrintSettingsManager`가 자동으로 `PrintMethod=0` (일반 1쪽 인쇄)으로 초기화한 뒤 변환하므로 왜곡 없이 정상 출력됩니다.

### Q5. Linux나 macOS, Docker 환경에서도 실변환이 가능한가요?
* **안내**: 실제 한글 문서를 원본 서식 그대로 렌더링하여 PDF/DOCX로 변환하는 작업은 **한컴오피스 COM 라이브러리를 사용하므로 Windows 환경이 필수**입니다. 리눅스 환경이나 CI 파이프라인에서는 `--mode mock` 옵션을 통해 파이프라인 및 경로 로직을 모의 검증할 수 있습니다.

---

## 🧪 테스트 및 자체 진단

코드베이스의 신뢰성을 검증하기 위한 자동화 테스트 스위트가 포함되어 있습니다.

```powershell
# 1. pytest 전체 테스트 실행 (22개 단위/통합 테스트)
pytest tests/test_hwp_batch_convert.py -v

# 2. 보안 팝업 자동 클릭 UI 시뮬레이션 자체 테스트
python scripts/hwp_batch_convert.py --self-test-dialog-handler
```

---

## 📜 기원 및 라이선스

* **코어 아키텍처 기원**: 본 프로젝트는 [twbeatles/HwpMate](https://github.com/twbeatles/HwpMate)의 핵심 안정성 설계(보안 모듈 레지스트리 자동화, 인쇄 설정 리셋, 가상 프린터 자동 폴백, TOCTOU 충돌 방어, 다중 산출물 수명주기 관리)를 기반으로 고도화되었습니다.
* **라이선스**: 본 프로젝트는 MIT 라이선스에 따라 자유롭게 사용, 수정, 배포할 수 있습니다.
* **릴리스 및 이슈**: [GitHub Releases](https://github.com/twbeatles/openclaw-hwp-batch-convert/releases)
