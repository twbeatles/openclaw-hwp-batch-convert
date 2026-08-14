from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Tuple

from hwp_batch_core import (
    FORMAT_TYPES,
    HWP_PROGIDS,
    AutoDialogEvent,
    RealWorkerResult,
    changed_artifacts,
    com_path_candidates,
    create_backup,
    dedupe_strings,
    existing_artifact_conflicts,
    kill_processes,
    parse_json_text,
    read_json_file,
    remove_new_attempt_artifacts,
    safe_unlink,
    snapshot_artifacts,
    uses_auxiliary_artifacts,
    write_json_file,
)
from hwp_batch_dialogs import AutoAllowDialogWatcher
from hwp_batch_print import (
    EXPORT_METHOD_PRINT_TO_PDF_EX,
    EXPORT_METHOD_RUN_TO_PDF,
    EXPORT_METHOD_SAVEAS_2,
    EXPORT_METHOD_SAVEAS_3,
    PDF_EXPORT_PRINT_TO_PDF_EX_FIRST,
    PDF_EXPORT_SAVEAS_FIRST,
    apply_default_print_settings,
    is_valid_pdf_file,
    normalize_pdf_export_mode,
    remove_incomplete_output,
    try_export_pdf_via_print_to_pdf_ex,
)
from hwp_batch_security import (
    SECURITY_MODULE_ALIAS,
    ensure_hwp_security_module,
)

DOCUMENT_LOAD_DELAY = 0.5
HWP_PROCESS_NAMES = {"hwp.exe", "hwpctrl.exe"}
WORKER_POLL_INTERVAL_SECONDS = 0.2

SECURITY_MODULE_ALIASES = (
    SECURITY_MODULE_ALIAS,
    "FilePathCheckerModule",
    "SecurityModule",
)


def snapshot_hwp_pids() -> set[int]:
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
        )
        if result.returncode != 0:
            return set()
        import csv
        import io

        reader = csv.reader(io.StringIO(result.stdout))
        pids: set[int] = set()
        for row in reader:
            if len(row) < 2:
                continue
            image_name = row[0].strip().lower()
            if image_name not in HWP_PROCESS_NAMES:
                continue
            try:
                pids.add(int(row[1]))
            except ValueError:
                pass
        return pids
    except Exception:
        return set()


def _with_document_access_hint(message: str) -> str:
    hint = "(암호·보호된 문서이거나 접근이 제한된 파일일 수 있습니다. 암호를 해제한 뒤 다시 시도하세요.)"
    if hint in message:
        return message
    lowered = message.lower()
    tokens_ko = ("암호", "비밀번호", "패스워드", "보호", "권한")
    tokens_en = ("password", "passwd", "encrypted", "protected", "access denied", "permission")
    if any(t in message for t in tokens_ko) or any(t in lowered for t in tokens_en):
        return f"{message} {hint}"
    return message


class RealHwpConverter:
    def __init__(self) -> None:
        self.hwp = None
        self.progid_used: str | None = None
        self.is_initialized = False
        self.owned_pids: set[int] = set()
        self.pythoncom = None
        self.security_module_registered = False
        self.security_module_warning: str | None = None
        self.last_created_files: list[Path] = []
        self.last_output_size: int | None = None
        self.last_output_mtime: float | None = None
        self.last_save_format: str | None = None
        self.last_export_method: str | None = None
        self.pdf_export_mode: str = PDF_EXPORT_SAVEAS_FIRST

    def initialize(self, *, ensure_security: bool = True) -> bool:
        if self.is_initialized:
            return True

        try:
            import pythoncom
            from win32com import client as win32_client
        except ImportError as exc:
            raise RuntimeError("pywin32가 필요합니다. `pip install pywin32` 후 다시 실행해주세요.") from exc

        self.pythoncom = pythoncom
        try:
            pythoncom.CoInitialize()
        except Exception:
            pass

        # 1. 보안 모듈 DLL 설치 + 레지스트리 사전 준비
        prep_ok = False
        prep_alias = None
        if ensure_security:
            try:
                prep_ok, prep_msg, prep_alias = ensure_hwp_security_module()
            except Exception as e:
                self.security_module_warning = f"보안 모듈 사전 준비 예외: {e}"

        dispatch_factory = getattr(win32_client, "DispatchEx", win32_client.Dispatch)
        errors: list[str] = []

        for progid in HWP_PROGIDS:
            before_pids = snapshot_hwp_pids()
            try:
                self.hwp = dispatch_factory(progid)
                self.progid_used = progid

                # 2. RegisterModule 시도
                aliases = list(SECURITY_MODULE_ALIASES)
                if prep_alias and prep_alias not in aliases:
                    aliases.insert(0, prep_alias)

                for alias in aliases:
                    try:
                        res = self.hwp.RegisterModule("FilePathCheckDLL", alias)
                        if res is not False and res != 0 and prep_ok:
                            self.security_module_registered = True
                            break
                    except Exception:
                        pass

                self.hwp.SetMessageBoxMode(0x00000001)
                time.sleep(0.2)
                self.owned_pids = snapshot_hwp_pids() - before_pids
                self.is_initialized = True
                self._suppress_hwp_ui_flash()
                return True
            except Exception as exc:
                errors.append(f"{progid}: {exc}")

        raise RuntimeError("한글 COM 객체 생성에 실패했습니다.\n" + "\n".join(errors))

    def _suppress_hwp_ui_flash(self) -> None:
        """한글 메인 창 표시를 숨겨 백그라운드 변환을 유지합니다."""
        if self.hwp is None:
            return
        try:
            xwindows = getattr(self.hwp, "XHwpWindows", None)
            if xwindows is not None:
                count_raw = getattr(xwindows, "Count", None)
                count = int(count_raw) if count_raw is not None else 1
                for idx in range(max(1, count)):
                    try:
                        win = xwindows.Item(idx)
                        win.Visible = False
                    except Exception:
                        break
        except Exception:
            pass

    def convert_file(
        self,
        input_path: Path,
        output_path: Path,
        format_type: str = "PDF",
        *,
        overwrite: bool = True,
        pdf_export_mode: str = PDF_EXPORT_SAVEAS_FIRST,
    ) -> Tuple[bool, str | None, Path]:
        """단일 파일 변환을 실행합니다.

        Returns:
            (성공 여부, 에러 메시지, 실제 저장된 출력 경로)
        """
        if not self.is_initialized or self.hwp is None:
            return False, "한글 COM 객체가 초기화되지 않았습니다.", output_path

        format_key = str(format_type).upper()
        format_info = FORMAT_TYPES.get(format_key, FORMAT_TYPES["PDF"])
        save_format = format_info["save_format"]
        pdf_mode = normalize_pdf_export_mode(pdf_export_mode)

        # 1. 저장 직전 원자적 충돌 재검사 (TOCTOU 방어 - Audit A-01)
        actual_output_file = output_path
        if not overwrite:
            parent = output_path.parent
            stem = output_path.stem
            ext = output_path.suffix
            counter = 1
            while True:
                conflicts = existing_artifact_conflicts(actual_output_file, format_key)
                if not conflicts:
                    break
                actual_output_file = parent / f"{stem} ({counter}){ext}"
                counter += 1
                if counter > 1000:
                    actual_output_file = parent / f"{stem}_{int(time.time())}{ext}"
                    break

        actual_output_file.parent.mkdir(parents=True, exist_ok=True)
        self.last_created_files = []
        self.last_output_size = None
        self.last_output_mtime = None
        self.last_save_format = save_format
        self.last_export_method = None

        input_candidates = com_path_candidates(input_path)
        output_candidates = com_path_candidates(actual_output_file)
        before_artifacts = snapshot_artifacts(actual_output_file, format_key)

        def _cleanup_failed_artifacts() -> None:
            remove_new_attempt_artifacts(before_artifacts, actual_output_file, format_key)

        # 2. 문서 열기
        opened = False
        open_error = None
        for in_candidate in input_candidates:
            try:
                open_result = self.hwp.Open(in_candidate, "", "forceopen:true")
                if open_result is not False and open_result != 0:
                    opened = True
                    break
            except Exception as e:
                open_error = str(e)

        if not opened:
            try:
                self.hwp.Clear(option=1)
            except Exception:
                pass
            msg = _with_document_access_hint(open_error or f"문서 열기 실패: {input_path.name}")
            return False, msg, actual_output_file

        time.sleep(DOCUMENT_LOAD_DELAY)
        self._suppress_hwp_ui_flash()

        # 3. 인쇄 설정 리셋 (PDF/이미지 등 1쪽씩 일반 인쇄)
        if format_key in ("PDF", "PNG", "JPG", "BMP", "GIF"):
            try:
                apply_default_print_settings(self.hwp)
            except Exception:
                pass

        # 4. 내보내기 전략 실행
        exported = False
        export_error = None

        def _try_saveas() -> bool:
            nonlocal export_error
            for out_candidate in output_candidates:
                try:
                    res = self.hwp.SaveAs(out_candidate, save_format)
                    if res is not False and res != 0:
                        self.last_export_method = EXPORT_METHOD_SAVEAS_2
                        return True
                except Exception:
                    pass
                try:
                    res = self.hwp.SaveAs(out_candidate, save_format, "")
                    if res is not False and res != 0:
                        self.last_export_method = EXPORT_METHOD_SAVEAS_3
                        return True
                except Exception as e:
                    export_error = str(e)
            return False

        def _try_print_to_pdf() -> bool:
            for out_candidate in output_candidates:
                try:
                    ok, method = try_export_pdf_via_print_to_pdf_ex(self.hwp, out_candidate)
                    if ok:
                        self.last_export_method = method or EXPORT_METHOD_PRINT_TO_PDF_EX
                        return True
                except Exception:
                    pass
            return False

        used_saveas = False
        used_print = False

        if format_key == "PDF":
            if pdf_mode == PDF_EXPORT_PRINT_TO_PDF_EX_FIRST:
                if _try_print_to_pdf():
                    exported = True
                    used_print = True
                elif _try_saveas():
                    exported = True
                    used_saveas = True
            else:
                if _try_saveas():
                    exported = True
                    used_saveas = True
                elif _try_print_to_pdf():
                    exported = True
                    used_print = True
        else:
            if _try_saveas():
                exported = True
                used_saveas = True

        if not exported:
            try:
                self.hwp.Clear(option=1)
            except Exception:
                pass
            _cleanup_failed_artifacts()
            return False, export_error or "내보내기 실패", actual_output_file

        # 5. 산출물 검증
        after_artifacts = snapshot_artifacts(actual_output_file, format_key)
        changed = changed_artifacts(before_artifacts, after_artifacts)

        if not after_artifacts or not changed:
            # SaveAs 성공 반환했으나 산출물이 없는 경우 PDF Print 폴백 1회
            if format_key == "PDF" and used_saveas and not used_print:
                if _try_print_to_pdf():
                    after_artifacts = snapshot_artifacts(actual_output_file, format_key)
                    changed = changed_artifacts(before_artifacts, after_artifacts)

        if not after_artifacts or not changed:
            try:
                self.hwp.Clear(option=1)
            except Exception:
                pass
            _cleanup_failed_artifacts()
            return False, f"출력 파일이 생성되지 않았습니다: {actual_output_file.name}", actual_output_file

        # 6. PDF 매직 헤더 검증
        if format_key == "PDF":
            pdf_target = actual_output_file if actual_output_file in changed else changed[0]
            if not is_valid_pdf_file(pdf_target):
                remove_incomplete_output(pdf_target)
                if used_saveas and not used_print:
                    if _try_print_to_pdf():
                        after_artifacts = snapshot_artifacts(actual_output_file, format_key)
                        changed = changed_artifacts(before_artifacts, after_artifacts)

                if not is_valid_pdf_file(pdf_target):
                    try:
                        self.hwp.Clear(option=1)
                    except Exception:
                        pass
                    _cleanup_failed_artifacts()
                    return False, f"유효한 PDF가 아닙니다 (매직/크기 검사 실패): {pdf_target.name}", actual_output_file

        # 7. 메타데이터 수집
        rep_file = actual_output_file if actual_output_file in changed else changed[0]
        rep_meta = after_artifacts.get(rep_file)
        self.last_created_files = changed
        self.last_output_size = rep_meta.size if rep_meta else (rep_file.stat().st_size if rep_file.exists() else None)
        try:
            self.last_output_mtime = rep_file.stat().st_mtime
        except OSError:
            self.last_output_mtime = (rep_meta.mtime_ns / 1_000_000_000) if rep_meta else None

        try:
            self.hwp.Clear(option=1)
        except Exception:
            pass

        return True, None, actual_output_file

    def cleanup(self) -> None:
        if self.hwp is not None and self.is_initialized:
            try:
                self.hwp.Clear(3)
            except Exception:
                pass
            try:
                self.hwp.Quit()
            except Exception:
                pass
            self.hwp = None
            self.is_initialized = False

        if self.pythoncom is not None:
            try:
                self.pythoncom.CoUninitialize()
            except Exception:
                pass


def _make_worker_state_path() -> Path:
    handle = tempfile.NamedTemporaryFile(prefix="hwp-convert-state-", suffix=".json", delete=False)
    handle.close()
    return Path(handle.name)


def _worker_command(
    *,
    script_path: Path,
    input_path: Path,
    output_path: Path,
    format_type: str,
    auto_allow_dialogs: bool,
    state_path: Path,
    overwrite: bool,
    backup: bool,
    backup_max_per_stem: int,
    pdf_export_mode: str,
    ensure_security_module: bool,
) -> list[str]:
    command = [
        sys.executable,
        str(script_path),
        "--internal-worker-real-convert",
        "--worker-input",
        str(input_path),
        "--worker-output",
        str(output_path),
        "--worker-format",
        format_type,
        "--worker-state-json",
        str(state_path),
        "--worker-backup-max-per-stem",
        str(backup_max_per_stem),
        "--worker-pdf-export-mode",
        pdf_export_mode,
    ]
    if auto_allow_dialogs:
        command.append("--worker-auto-allow-dialogs")
    if overwrite:
        command.append("--worker-overwrite")
    if backup:
        command.append("--worker-backup")
    if ensure_security_module:
        command.append("--worker-ensure-security-module")
    return command


def run_real_worker_task(task, args, script_path: Path) -> RealWorkerResult:
    state_path = _make_worker_state_path()
    before_pids = snapshot_hwp_pids()
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        _worker_command(
            script_path=script_path,
            input_path=task.input_file,
            output_path=task.output_file,
            format_type=args.format,
            auto_allow_dialogs=args.auto_allow_dialogs,
            state_path=state_path,
            overwrite=getattr(args, "overwrite", False),
            backup=getattr(args, "backup", False),
            backup_max_per_stem=getattr(args, "backup_max_per_stem", 20),
            pdf_export_mode=getattr(args, "pdf_export_mode", "saveas_first"),
            ensure_security_module=getattr(args, "ensure_security_module", True),
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    started_at = time.monotonic()
    initialized_at: float | None = None
    state_payload: dict[str, object] = {}
    timeout_stage: str | None = None

    try:
        while proc.poll() is None:
            latest_state = read_json_file(state_path)
            if latest_state:
                state_payload = latest_state
                if state_payload.get("initialized") and initialized_at is None:
                    initialized_at = time.monotonic()

            now = time.monotonic()
            if initialized_at is None:
                if now - started_at > args.startup_timeout_seconds:
                    timeout_stage = "startup"
                    break
            elif now - initialized_at > args.file_timeout_seconds:
                timeout_stage = "file"
                break
            time.sleep(WORKER_POLL_INTERVAL_SECONDS)

        if timeout_stage is not None:
            proc.kill()
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            latest_state = read_json_file(state_path)
            if latest_state:
                state_payload = latest_state

            warnings = list(state_payload.get("warnings", []))
            owned_pids = {int(pid) for pid in state_payload.get("owned_pids", [])}
            if args.kill_owned_hwp_on_timeout and not owned_pids:
                owned_pids = snapshot_hwp_pids() - before_pids

            if args.kill_owned_hwp_on_timeout and owned_pids:
                killed_pids = kill_processes(owned_pids)
                if killed_pids:
                    warnings.append(f"timeout 후 정리한 HWP PID: {', '.join(str(pid) for pid in killed_pids)}")
            elif args.kill_owned_hwp_on_timeout:
                warnings.append("timeout 후 정리할 HWP PID를 찾지 못했습니다.")

            if timeout_stage == "startup":
                error = f"초기화 시간 제한 {args.startup_timeout_seconds:.1f}초를 초과했습니다."
            else:
                error = f"파일 변환 시간 제한 {args.file_timeout_seconds:.1f}초를 초과했습니다."
            return RealWorkerResult(
                ok=False,
                error=error,
                warnings=dedupe_strings(warnings),
                progid_used=state_payload.get("progid_used"),
            )

        stdout, stderr = proc.communicate()
        latest_state = read_json_file(state_path)
        if latest_state:
            state_payload = latest_state

        payload = parse_json_text(stdout)
        warnings = list(state_payload.get("warnings", []))
        if payload and isinstance(payload.get("warnings"), list):
            warnings.extend(str(item) for item in payload["warnings"])

        if payload is None:
            error = stderr.strip() or stdout.strip() or "real worker 결과를 파싱하지 못했습니다."
            return RealWorkerResult(
                ok=False,
                error=error,
                warnings=dedupe_strings(warnings),
                progid_used=state_payload.get("progid_used"),
            )

        events = [
            AutoDialogEvent.from_record(record)
            for record in payload.get("auto_dialog_events", [])
            if isinstance(record, dict)
        ]
        created_files = [Path(p) for p in payload.get("created_files", [])]
        backup_file = Path(payload["backup_file"]) if payload.get("backup_file") else None
        final_output_file = Path(payload["final_output_file"]) if payload.get("final_output_file") else None

        ok = bool(payload.get("ok", False)) and proc.returncode == 0
        error = None if ok else str(payload.get("error") or stderr.strip() or "real worker 실행에 실패했습니다.")
        return RealWorkerResult(
            ok=ok,
            error=error,
            warnings=dedupe_strings(warnings),
            progid_used=str(payload.get("progid_used") or state_payload.get("progid_used") or "") or None,
            auto_dialog_events=events,
            created_files=created_files,
            output_size=payload.get("output_size"),
            output_mtime=payload.get("output_mtime"),
            save_format=payload.get("save_format"),
            export_method=payload.get("export_method"),
            backup_file=backup_file,
            final_output_file=final_output_file,
        )
    finally:
        safe_unlink(state_path)


def run_internal_real_worker(args) -> int:
    state_path = Path(args.worker_state_json)
    write_json_file(state_path, {"initialized": False, "owned_pids": [], "warnings": []})

    converter = RealHwpConverter()
    converter.pdf_export_mode = getattr(args, "worker_pdf_export_mode", PDF_EXPORT_SAVEAS_FIRST)
    warnings: list[str] = []
    watcher: AutoAllowDialogWatcher | None = None
    backup_path: Path | None = None
    payload: dict[str, object]

    try:
        # 백업 생성 (옵션 활성화 시)
        if getattr(args, "worker_backup", False):
            try:
                backup_path = create_backup(
                    Path(args.worker_input),
                    max_files=getattr(args, "worker_backup_max_per_stem", 20),
                )
            except Exception as e:
                warnings.append(f"백업 실패(무시하고 변환 계속): {e}")

        converter.initialize(ensure_security=getattr(args, "worker_ensure_security_module", True))
        if converter.security_module_warning:
            warnings.append(converter.security_module_warning)

        allowed_pids = set(converter.owned_pids)
        if args.worker_auto_allow_dialogs and not allowed_pids:
            warnings.append("자동 허용을 요청했지만 소유 HWP PID를 확인하지 못해 watcher를 비활성화했습니다.")

        watcher = AutoAllowDialogWatcher(
            enabled=args.worker_auto_allow_dialogs and bool(allowed_pids),
            allowed_pids=allowed_pids if allowed_pids else set(),
        )
        write_json_file(
            state_path,
            {
                "initialized": True,
                "owned_pids": sorted(converter.owned_pids),
                "progid_used": converter.progid_used,
                "warnings": warnings,
            },
        )
        watcher.start()

        ok, error, final_output = converter.convert_file(
            Path(args.worker_input),
            Path(args.worker_output),
            args.worker_format,
            overwrite=getattr(args, "worker_overwrite", False),
            pdf_export_mode=converter.pdf_export_mode,
        )
        events = watcher.snapshot_events()
        payload = {
            "ok": ok,
            "error": error,
            "progid_used": converter.progid_used,
            "owned_pids": sorted(converter.owned_pids),
            "warnings": warnings,
            "auto_dialog_events": [event.to_record() for event in events],
            "created_files": [str(p) for p in converter.last_created_files],
            "output_size": converter.last_output_size,
            "output_mtime": converter.last_output_mtime,
            "save_format": converter.last_save_format,
            "export_method": converter.last_export_method,
            "backup_file": str(backup_path) if backup_path else None,
            "final_output_file": str(final_output),
        }
    except Exception as exc:
        events = watcher.snapshot_events() if watcher is not None else []
        payload = {
            "ok": False,
            "error": str(exc),
            "progid_used": converter.progid_used,
            "owned_pids": sorted(converter.owned_pids),
            "warnings": warnings,
            "auto_dialog_events": [event.to_record() for event in events],
            "created_files": [],
            "backup_file": str(backup_path) if backup_path else None,
        }
        write_json_file(
            state_path,
            {
                "initialized": converter.is_initialized,
                "owned_pids": sorted(converter.owned_pids),
                "progid_used": converter.progid_used,
                "warnings": warnings,
                "error": str(exc),
            },
        )
    finally:
        if watcher is not None:
            watcher.stop()
        converter.cleanup()

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1
