# HwpMate 재사용 메모

이 스킬의 CLI 엔진은 `twbeatles/HwpMate`의 핵심 안정성·변환 품질 로직과 프로젝트 감사(Audit) 권장사항을 직접 참고해 고도화했다.

- `hwpmate/services/hwp_security_module.py`
  - FilePathCheckerModuleExample.dll 번들 SHA-256 무결성 검증
  - `%LOCALAPPDATA%` 영구 설치 및 HKCU `HwpAutomation/Modules`, `HwpCtrl/Modules` 레지스트리 4종 경로 등록 (`hwp_batch_security.py`)
  - 1단계 보안 모듈 사전 등록 + 2단계 `AutoAllowDialogWatcher` 폴백의 2중 안전망
- `hwpmate/services/hwp_print_settings/`
  - 인쇄 설정 1쪽씩 일반 인쇄(`PrintMethod=0`) 리셋
  - SaveAs 실패 시 `PrintToPDFEx` / `RunToPDF` 가상 프린터 자동 폴백
  - `%PDF` 매직 헤더 검증 및 실패 시 불완전 산출물 자동 정리 (`hwp_batch_print.py`)
- `hwpmate/services/artifact_policy.py` & `artifact_snapshot.py`
  - HTML(`.files` 폴더), 이미지(`_1.png` 다중 페이지 등) 보조 산출물 충돌 감지 및 회피
  - 변환 실패 시 비-PDF 보조 산출물까지 자동 정리 (Audit A-02 해결)
- `hwpmate/services/hwp_converter.py` & `hwpmate/workers/conversion_worker/`
  - 저장 직전 원자적 충돌 재검증 및 새 번호 동적 할당 (Audit A-01 TOCTOU 방어)
  - `_suppress_hwp_ui_flash` 백그라운드 창 숨김 및 암호 문서 안내 힌트
  - 감사 메타데이터 수집 (`created_files`, `output_size`, `output_mtime`, `save_format`, `export_method`, `progid_used`, `backup_file`)
- `hwpmate/workers/conversion_worker/backup.py`
  - 원본 자동 백업 (`--backup`), stem별 최대 백업 수 제한 및 오래된 백업 정리 (`--backup-max-per-stem`)
- `hwpmate/path_utils.py`
  - 긴 경로 및 UNC 대응 `com_path_candidates` 확장 경로(`\\?\`) 폴백
  - 240자/260자 경로 길이 경고 및 쓰기 권한 검사
- `hwpmate/models.py` & `constants.py`
  - 상세 데이터 모델 및 상수 매핑
