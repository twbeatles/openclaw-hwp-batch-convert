from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import nullcontext
from ctypes import wintypes
from pathlib import Path
from typing import Any, Tuple

from hwp_batch_core import (
    HWP_PROGIDS,
    AutoDialogEvent,
    RealWorkerResult,
    changed_artifacts,
    com_path_candidates,
    create_backup,
    dedupe_strings,
    existing_artifact_conflicts,
    is_com_failure_result,
    kill_processes,
    parse_json_text,
    read_json_file,
    remove_new_attempt_artifacts,
    safe_unlink,
    save_format_candidates,
    snapshot_artifacts,
    write_json_file,
)
from hwp_batch_dialogs import AutoAllowDialogWatcher, HwpCompatDialogResponder
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


TH32CS_SNAPPROCESS = 0x00000002

_snapshot_failure_count = 0
_snapshot_last_error: str | None = None


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def get_snapshot_health() -> tuple[int, str | None]:
    """(연속 실패 횟수, 마지막 오류 메시지). UI/워커 경고용."""
    return _snapshot_failure_count, _snapshot_last_error


def snapshot_hwp_pids() -> set[int]:
    """실행 중인 한글 관련 프로세스 PID 집합 반환.

    tasklist 서브프로세스 대신 Toolhelp32를 직접 써서 콘솔 깜빡임을 막는다.
    """
    global _snapshot_failure_count, _snapshot_last_error
    try:
        kernel32 = ctypes.windll.kernel32
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot in (-1, 0xFFFFFFFF):
            _snapshot_failure_count += 1
            _snapshot_last_error = "CreateToolhelp32Snapshot invalid handle"
            return set()

        pids: set[int] = set()
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                _snapshot_failure_count += 1
                _snapshot_last_error = "Process32FirstW failed"
                return set()
            while True:
                image_name = entry.szExeFile.strip().lower()
                if image_name in HWP_PROCESS_NAMES:
                    pids.add(int(entry.th32ProcessID))
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
            _snapshot_failure_count = 0
            _snapshot_last_error = None
            return pids
        finally:
            kernel32.CloseHandle(snapshot)
    except Exception as e:
        _snapshot_failure_count += 1
        _snapshot_last_error = str(e)
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
    hwp: Any
    def __init__(self) -> None:
        self.hwp = None
        self.progid_used: str | None = None
        self.is_initialized = False
        self.owned_pids: set[int] = set()
        self.pythoncom = None
        self.security_module_registered: bool | None = None
        self.security_module_error: str | None = None
        self.security_module_warning: str | None = None
        self.snapshot_unreliable = False
        self.process_tracking_warning: str | None = None
        # 호환 확인 문서("배치가 변경될 수 있습니다") 창 자동 계속 (소유 PID 한정)
        self.auto_continue_compat_dialogs = True
        self.compat_dialog_responses = 0
        # True only when this instance called CoInitialize itself.
        self._com_apartment_owned = False
        self.last_created_files: list[Path] = []
        self.last_output_size: int | None = None
        self.last_output_mtime: float | None = None
        self.last_save_format: str | None = None
        self.last_export_method: str | None = None
        self.pdf_export_mode: str = PDF_EXPORT_SAVEAS_FIRST

    def initialize(self, *, ensure_security: bool = True, manage_com_apartment: bool = True) -> bool:
        if self.is_initialized:
            return True

        try:
            import pythoncom
            from win32com import client as win32_client
        except ImportError as exc:
            raise RuntimeError("pywin32가 필요합니다. `pip install pywin32` 후 다시 실행해주세요.") from exc

        self.pythoncom = pythoncom
        if manage_com_apartment:
            try:
                pythoncom.CoInitialize()
                self._com_apartment_owned = True
            except Exception:
                # 이미 초기화된 스레드에서는 소유권 없이 계속
                self._com_apartment_owned = False
        else:
            self._com_apartment_owned = False

        # 1. 보안 모듈 DLL 설치 + 레지스트리 사전 준비
        prep_ok = False
        prep_msg = ""
        prep_alias = None
        if ensure_security:
            try:
                prep_ok, prep_msg, prep_alias = ensure_hwp_security_module()
            except Exception as e:
                prep_msg = str(e)
                self.security_module_warning = f"보안 모듈 사전 준비 예외: {e}"

        dispatch_factory = getattr(win32_client, "DispatchEx", win32_client.Dispatch)
        errors: list[str] = []

        for progid in HWP_PROGIDS:
            before_pids = snapshot_hwp_pids()
            try:
                self.hwp = dispatch_factory(progid)
                self.progid_used = progid

                # 2. RegisterModule 시도
                aliases: list[str] = []
                if prep_alias and prep_alias not in aliases:
                    aliases.append(prep_alias)
                for name in SECURITY_MODULE_ALIASES:
                    if name not in aliases:
                        aliases.append(name)

                module_errors: list[str] = []
                self.security_module_registered = False
                self.security_module_error = None
                for alias in aliases:
                    try:
                        res = self.hwp.RegisterModule("FilePathCheckDLL", alias)
                        if is_com_failure_result(res):
                            module_errors.append(f"{alias}: RegisterModule returned {res!r}")
                            continue
                        if prep_ok:
                            self.security_module_registered = True
                            self.security_module_error = None
                            break
                        self.security_module_registered = False
                        self.security_module_error = (
                            f"RegisterModule({alias}) 호출은 result={res!r}였으나 "
                            f"레지스트리 DLL 사전 준비 실패: {prep_msg}"
                        )
                        break
                    except Exception as module_error:
                        module_errors.append(f"{alias}: {module_error}")

                if not self.security_module_registered and self.security_module_error is None:
                    self.security_module_error = (
                        f"prep={prep_msg}; " + ("; ".join(module_errors) or "알 수 없는 오류")
                    )
                    self.security_module_warning = (
                        "한글 보안 모듈 등록 실패 (파일 접근 시 '모두 허용' 창이 뜰 수 있음): "
                        f"{self.security_module_error}"
                    )

                self.hwp.SetMessageBoxMode(0x00000001)
                time.sleep(0.2)
                after_pids = snapshot_hwp_pids()
                fail_count, fail_msg = get_snapshot_health()
                self.snapshot_unreliable = fail_count > 0 and not after_pids and not before_pids
                self.owned_pids = after_pids - before_pids
                self.is_initialized = True
                if self.snapshot_unreliable:
                    detail = f" ({fail_msg})" if fail_msg else ""
                    self.process_tracking_warning = (
                        "한글 프로세스 스냅샷(Toolhelp) 수집에 실패했습니다"
                        f"{detail}. 강제 종료·감시 범위가 제한될 수 있습니다."
                    )
                elif not self.owned_pids:
                    self.process_tracking_warning = (
                        "새로 생성된 한글 프로세스를 추적하지 못했습니다. "
                        "강제 종료는 비활성화되며 변환 외 다른 한글 창을 추적 대상으로 삼지 않습니다."
                    )
                self._suppress_hwp_ui_flash()
                return True
            except Exception as exc:
                errors.append(f"{progid}: {exc}")
                self.hwp = None
                self.progid_used = None
                # 연결 실패 후 고아 HWP 프로세스가 남았으면 이번 시도에서 생긴 PID만 정리
                orphan_pids = snapshot_hwp_pids() - before_pids
                if orphan_pids:
                    kill_processes(orphan_pids)
                continue

        if self._com_apartment_owned and self.pythoncom is not None:
            try:
                self.pythoncom.CoUninitialize()
            except Exception:
                pass
            self._com_apartment_owned = False
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
        responder = self._compat_dialog_responder()
        with responder:
            try:
                return self._convert_file_impl(
                    input_path,
                    output_path,
                    format_type,
                    overwrite=overwrite,
                    pdf_export_mode=pdf_export_mode,
                )
            finally:
                count = int(getattr(responder, "response_count", 0) or 0)
                if count:
                    self.compat_dialog_responses += count

    def _compat_dialog_responder(self):
        if not self.auto_continue_compat_dialogs or not self.owned_pids:
            # 소유 PID 미추적 시 다른 한글 세션 창을 건드리지 않는다.
            return nullcontext()
        return HwpCompatDialogResponder(lambda: set(self.owned_pids))

    def _convert_file_impl(
        self,
        input_path: Path,
        output_path: Path,
        format_type: str = "PDF",
        *,
        overwrite: bool = True,
        pdf_export_mode: str = PDF_EXPORT_SAVEAS_FIRST,
    ) -> Tuple[bool, str | None, Path]:
        """단일 파일 변환 본체 (호환 확인 창 응답은 convert_file 래퍼가 담당)."""
        if not self.is_initialized or self.hwp is None:
            return False, "한글 COM 객체가 초기화되지 않았습니다.", output_path

        format_key = str(format_type).upper()
        format_candidates = save_format_candidates(format_key)
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
        self.last_save_format = format_candidates[0]
        self.last_export_method = None

        input_candidates = com_path_candidates(input_path)
        output_candidates = com_path_candidates(actual_output_file)
        before_artifacts = snapshot_artifacts(actual_output_file, format_key)

        # 환경에 따라 Open 직전 재등록이 필요한 경우가 있어 호출만 보장 (실패 무시)
        try:
            self.hwp.RegisterModule("FilePathCheckDLL", SECURITY_MODULE_ALIAS)
        except Exception:
            pass

        def _cleanup_failed_artifacts() -> None:
            remove_new_attempt_artifacts(before_artifacts, actual_output_file, format_key)

        # 2. 문서 열기
        opened = False
        open_error = None
        for in_candidate in input_candidates:
            try:
                open_result = self.hwp.Open(in_candidate, "", "forceopen:true")
                if not is_com_failure_result(open_result):
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
            errors: list[str] = []
            # 형식 문자열 후보 × 경로 후보 × (2-param → 3-param 폴백)
            for format_name in format_candidates:
                for out_candidate in output_candidates:
                    try:
                        save_result = self.hwp.SaveAs(out_candidate, format_name)
                        if is_com_failure_result(save_result):
                            raise RuntimeError(f"SaveAs 2-param returned failure: {save_result!r}")
                        self.last_save_format = format_name
                        self.last_export_method = EXPORT_METHOD_SAVEAS_2
                        return True
                    except Exception as e1:
                        try:
                            save_result = self.hwp.SaveAs(out_candidate, format_name, "")
                            if is_com_failure_result(save_result):
                                raise RuntimeError(
                                    f"SaveAs 3-param returned failure: {save_result!r}"
                                )
                            self.last_save_format = format_name
                            self.last_export_method = EXPORT_METHOD_SAVEAS_3
                            return True
                        except Exception as e2:
                            errors.append(
                                f"{format_name} {out_candidate}: 2-param: {e1}, 3-param: {e2}"
                            )
            export_error = "; ".join(errors) if errors else "SaveAs 실패"
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
                prev = before_artifacts.get(pdf_target)
                remove_incomplete_output(
                    pdf_target,
                    before_mtime_ns=prev.mtime_ns if prev else None,
                    before_size=prev.size if prev else None,
                )
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
                # 1=hwpDiscard. 2/3은 한글 2022 실측에서 열린 원본 문서가
                # 템프에 남으므로 사용하지 않는다.
                self.hwp.Clear(1)
            except Exception:
                pass
            try:
                self.hwp.Quit()
            except Exception:
                pass
            self.hwp = None
            self.is_initialized = False
            self.owned_pids.clear()
            self.process_tracking_warning = None

        if self._com_apartment_owned and self.pythoncom is not None:
            try:
                self.pythoncom.CoUninitialize()
            except Exception:
                pass
            self._com_apartment_owned = False


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
    compat_dialog: bool = True,
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
    if not compat_dialog:
        command.append("--worker-disable-compat-dialog")
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
            compat_dialog=getattr(args, "auto_continue_compat_dialog", True),
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
    state_payload: dict[str, Any] = {}
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
            if owned_pids:
                # 이미 종료된 PID 재사용 오사를 막기 위해 살아있는 HWP로 한정
                owned_pids &= snapshot_hwp_pids()

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
    converter.auto_continue_compat_dialogs = not getattr(args, "worker_disable_compat_dialog", False)
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
        if converter.compat_dialog_responses > 0:
            warnings.append(
                "한글 호환 확인(배치가 변경될 수 있습니다) 창에 "
                f"{converter.compat_dialog_responses}회 자동 '계속' 응답했습니다. "
                "DOCX/RTF 등 변환 식이 원본과 배치가 다를 수 있어 결과를 확인하세요."
            )
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
