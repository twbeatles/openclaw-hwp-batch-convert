---
name: hwp-batch-convert
description: Batch-convert 한컴 한글 문서(HWP/HWPX) on Windows into PDF and other export formats with a Korean-friendly automation workflow. Use when the user asks for 이 폴더 hwp 전부 pdf로 바꿔줘, hwp/hwpx/doc/pdf 일괄 변환, 한글문서 일괄 처리, 폴더 단위 변환, 여러 한글 파일을 다른 형식으로 내보내기, or wants plan-only / mock / real conversion runs with machine-readable reports. Supports HWP/HWPX to PDF, HWPX, DOCX, ODT, HTML, RTF, TXT, PNG, JPG, BMP, and GIF, plus security module auto-registration, automatic approval of known 한글 보안 확인 팝업, auto backup, and comprehensive audit metadata. Prefer this skill for Windows environments with Hancom HWP installed; do not use it for non-HWP document families unless the task is explicitly about HWP/HWPX conversion.
---

# Hwp Batch Convert

Use this skill for **Windows 기반 한글(HWP/HWPX) 문서 일괄 변환**.

Current scope:
- 폴더 단위 일괄 변환
- 파일 여러 개 일괄 변환
- HWP/HWPX → PDF 기본 변환 (SaveAs + PrintToPDFEx 가상 프린터 자동 폴백)
- HWP/HWPX → HWPX/DOCX/ODT/HTML/RTF/TXT/PNG/JPG/BMP/GIF 변환
- 동일 형식 자동 건너뜀
- 출력 파일명 충돌 시 자동 번호 부여 및 저장 직전 원자적 재검증(TOCTOU 방어)
- HTML(`.files`) 및 이미지 다중 산출물(Auxiliary Artifacts) 추적/충돌 회피 및 실패 시 자동 정리
- 한글 보안 모듈(FilePathCheckDLL) 사전 준비 + 레지스트리 자동 등록
- 보안 팝업 자동 허용용 `--auto-allow-dialogs` (2단계 폴백)
- 변환 전 원본 파일 자동 백업 (`--backup`, `--backup-max-per-stem`)
- `%PDF` 매직 헤더 검증 및 불완전 산출물 정리
- 지원하지 않는 단일 파일 조기 에러 처리
- 작업 계획만 확인하는 `--plan-only`
- 에이전트/자동화 연동용 `--json`, `--report-json` (정밀 감사 메타데이터 포함)
- `--startup-timeout-seconds`, `--file-timeout-seconds` timeout 제어
- `--kill-owned-hwp-on-timeout` 자동화로 띄운 HWP 정리 시도
- `--fail-fast`, `--allow-partial-success`, `--allow-empty`
- `--preserve-source-root` 로 여러 입력 source 결과 구분
- 로컬 UI 검증용 `--self-test-dialog-handler`
- 테스트용 `--mode mock`

## Source basis

This skill reuses and enhances the design of the source repo:
- `twbeatles/HwpMate`
- Main logic origin:
  - `hwpmate/services/hwp_security_module.py` (보안 모듈 무결성/레지스트리 등록)
  - `hwpmate/services/hwp_print_settings/` (인쇄 설정 리셋 및 PDF 폴백)
  - `hwpmate/services/artifact_policy.py` & `artifact_snapshot.py` (다중 산출물 추적/정리)
  - `hwpmate/services/hwp_converter/` (COM 제어 및 TOCTOU 충돌 방어)
  - `hwpmate/workers/conversion_worker/backup.py` (자동 백업)
  - `hwpmate/path_utils.py` (긴 경로 확장 경로 후보)

If you need the mapping details or reuse rationale, read:
- `references/hwpmate-reuse-notes.md`

If you need the popup whitelist / safety details, read:
- `references/auto-allow-dialogs.md`

## Quick start

같은 폴더에 PDF 출력:

```bash
python scripts/hwp_batch_convert.py "C:\docs\contracts" --format PDF --same-location
```

별도 출력 폴더로 변환 (백업 및 자동 팝업 허용 활성화):

```bash
python scripts/hwp_batch_convert.py "C:\docs\hwp" --format PDF --output-dir "C:\docs\pdf" --auto-allow-dialogs --backup
```

여러 파일 한 번에 변환:

```bash
python scripts/hwp_batch_convert.py "C:\docs\a.hwp" "C:\docs\b.hwpx" --format DOCX --output-dir "C:\docs\docx"
```

실제 변환 없이 계획만 확인:

```bash
python scripts/hwp_batch_convert.py "C:\docs\hwp" --format PDF --output-dir "C:\docs\pdf" --plan-only --json
```

테스트용 모의 변환:

```bash
python scripts/hwp_batch_convert.py "C:\docs\sample" --format PDF --output-dir "C:\docs\out" --mode mock --json
```

## Main script

### `scripts/hwp_batch_convert.py`

Parameters:
- `sources...`: 입력 파일/폴더 경로 하나 이상
- `--format`: 출력 형식 (`PDF`, `HWPX`, `DOCX`, `ODT`, `HTML`, `RTF`, `TXT`, `PNG`, `JPG`, `BMP`, `GIF`, `HWP`)
- `--same-location`: 원본과 같은 폴더에 출력
- `--output-dir`: 출력 루트 폴더
- `--include-sub`: 하위 폴더 포함(기본값)
- `--no-include-sub`: 하위 폴더 제외
- `--overwrite`: 같은 이름 출력 허용
- `--plan-only`: 실제 변환 없이 작업 계획만 생성
- `--mode real|mock`: 실변환 또는 모의 변환
- `--auto-allow-dialogs`: 제목 `한글`, 본문에 `접근하려는 시도`, 버튼 `모두 허용`/`허용` 조건을 모두 만족하고 현재 실행이 띄운 HWP 프로세스 범위에 속한 보안 팝업만 자동 클릭
- `--ensure-security-module`: 보안 모듈(FilePathCheckDLL) 설치 및 등록 보장 (기본 켜짐)
- `--pdf-export-mode`: PDF 내보내기 전략 (`saveas_first` 또는 `print_to_pdf_ex_first`)
- `--backup`: 변환 전 원본 파일을 `backup/` 폴더에 자동 백업
- `--backup-max-per-stem`: 파일명당 최대 백업 수 (기본 20)
- `--startup-timeout-seconds`: real 모드 초기화 timeout
- `--file-timeout-seconds`: real 모드 파일별 timeout
- `--kill-owned-hwp-on-timeout`: timeout 시 owned HWP 정리 시도
- `--fail-fast`: 한 파일 실패 시 남은 작업 중단
- `--allow-partial-success`: 일부 실패가 있어도 종료 코드 `0`
- `--allow-empty`: 변환 대상이 없어도 빈 결과 허용
- `--preserve-source-root`: 여러 source를 output-dir 아래에서 source 이름별로 분리
- `--json`: stdout JSON 출력 (상세 감사 메타데이터 포함)
- `--report-json`: 결과/에러 JSON 파일 저장
- `--self-test-dialog-handler`: 로컬 테스트용 샘플 `한글` 창을 띄워 자동 클릭 로직만 검증

## Verification

- 기본 자동 검증: `pytest tests/test_hwp_batch_convert.py -v`
- 로컬 UI 클릭 검증: `python scripts/hwp_batch_convert.py --self-test-dialog-handler`
