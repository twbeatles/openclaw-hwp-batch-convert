# hwp-batch-convert

Windows에서 한컴 한글 **HWP/HWPX 문서를 폴더 단위로 일괄 변환**하는 OpenClaw 스킬입니다.

이 저장소는 HWP/HWPX 문서를 **PDF, HWPX, DOCX, ODT, HTML, RTF, TXT, PNG, JPG, BMP, GIF, HWP** 형식으로 변환하는 CLI와, OpenClaw 상위 레이어에서 그대로 재사용하기 쉬운 **계획 출력 / JSON 리포트 / 보안 팝업 자동 허용** 흐름을 제공합니다.

핵심은 단순한 “파일 하나 변환”이 아니라, 아래 같은 실사용 시나리오입니다.

- `이 폴더 hwp 전부 pdf로 바꿔줘`
- `하위 폴더까지 포함해서 docx로 내보내줘`
- `실제 변환 전에 대상 파일/출력 위치부터 보여줘`
- `보안 확인 팝업 때문에 멈추지 않게 해줘`
- `결과를 JSON 리포트로 남겨줘`

---

## What this skill is good at

이 스킬이 특히 좋은 지점:

- 한컴 한글 문서 위주의 한국어 실무 파일 변환
- 폴더 단위 / 여러 파일 일괄 처리
- 변환 전에 계획만 확인하는 안전한 흐름
- 자동화/브리핑 시스템과 연결하기 쉬운 JSON 출력
- 보안 확인 팝업을 화이트리스트 방식으로 제한 처리

즉, 문서 변환 자체보다 **배치 작업을 안전하고 반복 가능하게 만드는 것**에 강점이 있습니다.

---

## Current feature set

현재 버전 기준 핵심 기능:

- HWP / HWPX 입력 지원
- PDF / HWPX / DOCX / ODT / HTML / RTF / TXT / PNG / JPG / BMP / GIF / HWP 출력
- 폴더 단위 일괄 변환
- 여러 파일 동시 입력
- 하위 폴더 포함/제외 옵션
- 같은 형식 자동 건너뜀
- 파일명 충돌 시 자동 번호 부여
- `--plan-only` 작업 계획 확인
- `--json` 표준 출력
- `--report-json` 결과 파일 저장
- `--mode mock` 모의 변환 테스트
- `--auto-allow-dialogs` 보안 확인 팝업 자동 허용
- `--self-test-dialog-handler` UI 자동 클릭 로직 로컬 테스트

---

## Repository layout

```text
hwp-batch-convert/
├── README.md
├── SKILL.md
├── hwp-batch-convert.skill
├── references/
│   ├── auto-allow-dialogs.md
│   └── hwpmate-reuse-notes.md
└── scripts/
    └── hwp_batch_convert.py
```

---

## Environment / requirements

이 스킬은 사실상 **Windows 전용**입니다.

실변환(`--mode real`)에 필요한 조건:

- Windows 환경
- 한컴 한글(HWP) 설치
- COM 자동화 가능 환경
- Python 실행 환경
- 일반적으로 `pywin32` 사용 가능 상태

테스트/흐름 검증만 하려면 아래만으로도 가능합니다.

- Windows
- Python
- `--mode mock`

즉, 실제 변환과 테스트 모드를 분리해 둔 점이 운영상 꽤 편합니다.

---

## Recommended workflow

가장 추천하는 사용 흐름은 아래입니다.

1. 먼저 `--plan-only --json`으로 대상 파일/출력 경로를 확인한다.
2. 환경이 준비되지 않았거나 먼저 구조만 보고 싶으면 `--mode mock`으로 검증한다.
3. 한컴 한글 + COM 환경이 준비된 뒤 `--mode real`로 실제 변환한다.
4. 보안 확인 팝업 때문에 멈출 수 있으면 `--auto-allow-dialogs`를 켠다.
5. 배치 작업 결과는 `--report-json`으로 남긴다.

이 흐름을 추천하는 이유는, HWP COM 자동화는 환경 의존성이 있기 때문에 **경로/계획 검증과 실변환을 분리하는 편이 훨씬 안전**하기 때문입니다.

---

## Quick start

### 1) 같은 위치에 PDF로 변환

```powershell
python scripts/hwp_batch_convert.py "C:\docs\contracts" --format PDF --same-location
```

### 2) 별도 출력 폴더로 PDF 변환

```powershell
python scripts/hwp_batch_convert.py "C:\docs\hwp" --format PDF --output-dir "C:\docs\pdf"
```

### 3) 여러 파일을 DOCX로 변환

```powershell
python scripts/hwp_batch_convert.py "C:\docs\a.hwp" "C:\docs\b.hwpx" --format DOCX --output-dir "C:\docs\docx"
```

### 4) 하위 폴더 포함한 일괄 변환

```powershell
python scripts/hwp_batch_convert.py "C:\docs\incoming" --format PDF --output-dir "C:\docs\out" --include-sub
```

### 5) 하위 폴더 제외

```powershell
python scripts/hwp_batch_convert.py "C:\docs\incoming" --format PDF --output-dir "C:\docs\out" --no-include-sub
```

---

## Safe preview / planning examples

### 실제 변환 없이 계획만 보기

```powershell
python scripts/hwp_batch_convert.py "C:\docs\hwp" --format PDF --output-dir "C:\docs\pdf" --plan-only --json
```

이 모드는 다음을 확인할 때 유용합니다.

- 몇 개 파일이 대상인지
- 출력 폴더가 어디인지
- 건너뛸 파일이 있는지
- 이름 충돌이 날지

### mock 모드로 전체 흐름 검증

```powershell
python scripts/hwp_batch_convert.py "C:\docs\sample" --format PDF --output-dir "C:\docs\out" --mode mock --json
```

mock 모드는 실제 한글 COM 없이도 결과 구조와 자동화 파이프라인을 먼저 검증하기 좋습니다.

---

## Real conversion examples

### JSON 리포트까지 남기기

```powershell
python scripts/hwp_batch_convert.py "C:\docs\hwp" --format PDF --output-dir "C:\docs\pdf" --json --report-json "C:\docs\pdf\report.json"
```

### 보안 확인 팝업 자동 허용과 함께 실행

```powershell
python scripts/hwp_batch_convert.py "C:\docs\hwp" --format PDF --output-dir "C:\docs\pdf" --auto-allow-dialogs --json --report-json "C:\docs\pdf\report.json"
```

### 덮어쓰기 허용

```powershell
python scripts/hwp_batch_convert.py "C:\docs\hwp" --format PDF --output-dir "C:\docs\pdf" --overwrite
```

---

## CLI overview

`scripts/hwp_batch_convert.py --help` 기준 주요 옵션:

- `sources ...`: 입력 파일 또는 폴더 경로(여러 개 가능)
- `--format`: 출력 형식
- `--include-sub`: 하위 폴더 포함(기본값 켜짐)
- `--no-include-sub`: 하위 폴더 제외
- `--same-location`: 원본과 같은 위치에 출력
- `--output-dir`: 출력 루트 폴더
- `--overwrite`: 같은 이름 파일 덮어쓰기 허용
- `--plan-only`: 실제 변환 없이 계획만 출력
- `--mode real|mock`: 실변환 또는 모의 변환
- `--auto-allow-dialogs`: 한글 보안 확인 팝업 자동 허용
- `--json`: stdout JSON 출력
- `--report-json`: 결과 JSON 파일 저장
- `--self-test-dialog-handler`: UI 자동 클릭 로직 테스트

---

## Auto-allow dialog behavior

한글 COM 자동화 중 다음 조건을 **모두 만족하는 보안 팝업만** 자동으로 처리합니다.

- 창 제목: `한글`
- 본문 텍스트에 `접근하려는 시도` 포함
- 버튼 텍스트: `모두 허용` 우선, 없으면 `허용`

이 조건 외의 팝업은 건드리지 않도록 화이트리스트 방식으로 제한합니다.

즉, “자동 클릭”이라고 해도 무차별 클릭이 아니라 **매우 제한된 패턴만 허용**하는 구조입니다.

### 로컬 UI 테스트

```powershell
python scripts/hwp_batch_convert.py --self-test-dialog-handler
```

### mock 모드에서의 주의

```powershell
python scripts/hwp_batch_convert.py "C:\docs\sample" --format PDF --output-dir "C:\docs\out" --mode mock --auto-allow-dialogs --json
```

mock 모드에서는 실제 한글 창이 없으므로 자동 허용이 동작하지 않고, 경고 또는 테스트성 정보만 남는 것이 정상입니다.

---

## JSON / report fields

자동화 파이프라인에서 특히 확인할 만한 필드:

- `auto_dialog_enabled`
- `auto_dialog_detected_count`
- `auto_dialog_clicked_count`
- `auto_dialog_events[]`

일반적으로는 다음처럼 쓰면 됩니다.

- 채팅/브리핑에는 `summary`나 성공/실패 개수만 노출
- 상세 진단에는 `report.json` 전체를 저장
- 보안 팝업 자동 허용이 실제로 몇 번 개입했는지 별도 기록

---

## Output / path behavior

실사용에서 자주 신경 써야 하는 부분:

- `--same-location`을 쓰면 원본 근처에 결과가 생김
- `--output-dir`을 쓰면 출력 구조를 별도 폴더로 분리 가능
- 여러 입력 소스를 동시에 넣으면 결과 경로를 미리 확인하는 편이 안전함
- 파일명 충돌 시 자동 번호 부여가 되지만, 배치 작업 전 `--plan-only` 확인이 제일 안전함

---

## Recommended use cases

이 스킬이 특히 잘 맞는 경우:

- 법무/행정/학교/관공서 문서처럼 HWP 중심 폴더를 한 번에 PDF화
- DOCX/ODT/텍스트로 일괄 내보내기
- 사람이 하나씩 GUI에서 저장하기 귀찮은 대량 변환
- OpenClaw 상위 자동화에서 변환 결과를 JSON으로 수집해야 할 때

반대로 아래는 범위 밖이거나 비추천입니다.

- macOS/Linux에서 실변환
- 한컴 한글이 설치되지 않은 상태에서 real 변환
- HWP가 핵심이 아닌 일반 문서 변환 작업 전반

---

## Limitations

알아둘 점:

- 실변환은 Windows + 한컴 한글 설치 환경에 크게 의존합니다.
- COM 자동화 특성상 UI/보안 정책 변화에 영향받을 수 있습니다.
- 대량 배치 작업에서는 파일 잠금, 출력 경로 권한, 팝업 상태를 함께 봐야 합니다.
- `--auto-allow-dialogs`는 안전하게 제한되어 있지만, 예상과 다른 새 팝업은 자동 처리하지 않을 수 있습니다.

---

## Deployment / release notes

현재 GitHub 최신 릴리스: **v0.1.0**

현재 단계에서는 README를 통해 아래를 명확히 해두는 것이 중요합니다.

- Windows 전용이라는 점
- 실변환과 mock 모드를 분리했다는 점
- 보안 팝업 자동 허용이 화이트리스트 방식이라는 점
- 계획 확인(`--plan-only`) → 실변환 → JSON 보고 흐름을 권장한다는 점

배포 전 점검 추천 순서:

1. `--self-test-dialog-handler`
2. `--plan-only --json`
3. `--mode mock`
4. 실제 샘플 폴더 대상 real 변환
5. `--report-json` 결과 확인
6. 태그/릴리스 갱신 및 배포 채널 반영

---

## Related references

세부 참고 문서:

- `references/auto-allow-dialogs.md`
- `references/hwpmate-reuse-notes.md`

GitHub Releases: <https://github.com/twbeatles/hwp-batch-convert/releases>
