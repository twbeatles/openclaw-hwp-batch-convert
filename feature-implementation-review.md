# Feature Implementation Review

## Status

HwpMate(`twbeatles/HwpMate`)의 핵심 안정성 아키텍처 및 프로젝트 감사(Audit) 권장사항을 바탕으로 고도화가 완료되었다.

### 반영된 핵심 항목

1. **보안 모듈(FilePathCheckDLL) 사전 준비 및 2단계 안전망**
   - DLL SHA-256 무결성 검증, `%LOCALAPPDATA%` 영구 설치, HKCU 4종 레지스트리 자동 등록(`hwp_batch_security.py`).
   - 1단계 보안 DLL 등록을 통한 팝업 원천 차단 + 2단계 `AutoAllowDialogWatcher` 폴백.
2. **PDF 변환 품질 및 인쇄 설정 제어**
   - 인쇄 설정(모아찍기 등)을 1쪽씩 일반 인쇄(`PrintMethod=0`)로 안전하게 리셋(`hwp_batch_print.py`).
   - SaveAs 실패 시 가상 PDF 프린터(`PrintToPDFEx` / `RunToPDF`) 자동 폴백 (물리 프린터 출력 원천 차단).
   - `%PDF` 매직 헤더 검증 및 실패 시 불완전/깨진 파일 자동 정리.
   - `--pdf-export-mode` (`saveas_first` | `print_to_pdf_ex_first`) 지원.
3. **다중/보조 산출물(Auxiliary Artifacts) 추적 및 정리 (Audit A-02 해결)**
   - HTML(`.files` 폴더) 및 이미지(`_1.png` 다중 페이지 등) 보조 산출물 충돌 감지/회피 및 실패 시 찌꺼기 파일 정리.
4. **저장 직전 원자적 충돌 재검증 (Audit A-01 해결 - TOCTOU 방어)**
   - 워커가 파일 변환 직전에 `overwrite=False` 충돌을 재검사하여, 외부 프로세스가 파일을 생성했더라도 덮어쓰지 않고 새 번호 동적 할당.
5. **자동 백업 시스템 및 경로 안전성 강화**
   - 변환 전 원본 파일 자동 백업 (`--backup`, `--backup-max-per-stem`, 마이크로초 타임스탬프, prune).
   - 긴 경로(240자/260자) 및 UNC 대응 `com_path_candidates` 확장 경로(`\\?\`) 지원.
   - 폴더 순회 스캔 시 `backup/` 디렉터리 자동 제외.
6. **실패 자동 재시도 및 콘솔 인코딩 안전화**
   - 변환 실패 시 1초 딜레이 후 자동 재시도 (`--retry-count`, 기본 1, 최대 3).
   - Windows 콘솔 UTF-8 출력 스트림 고정.
7. **정밀 감사 메타데이터(Audit Report) 확장 및 패키징**
   - JSON 리포트에 `created_files`, `output_size`, `output_mtime`, `save_format`, `export_method`, `progid_used`, `backup_file`, `retry_count` 필드 제공.
   - 배포용 `hwp-batch-convert.skill` 단일 아카이브 최신화.

## 남아 있는 성격의 리스크

- Hancom HWP COM 동작 자체는 Windows 환경과 설치 상태에 의존한다.
- timeout/정리 로직이 있어도 COM 내부 hang을 항상 완벽하게 복구한다고 보장할 수는 없다.
- 새 보안 팝업 패턴이 등장하면 화이트리스트에 추가 정의가 필요할 수 있다.

## Related docs

- `README.md`
- `SKILL.md`
- `references/auto-allow-dialogs.md`
- `references/hwpmate-reuse-notes.md`
