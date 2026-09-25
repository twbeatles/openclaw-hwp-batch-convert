from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Sequence

PDF_MAGIC = b"%PDF"
# 헤더 + 최소 본문. 변환 직후 생성된 빈/깨진 PDF를 걸러내는 하한이다.
MIN_PDF_BYTES = 8

EXPORT_METHOD_SAVEAS_2 = "saveas_2"
EXPORT_METHOD_SAVEAS_3 = "saveas_3"
EXPORT_METHOD_PRINT_TO_PDF_EX = "print_to_pdf_ex"
EXPORT_METHOD_RUN_TO_PDF = "run_to_pdf"

PDF_EXPORT_SAVEAS_FIRST = "saveas_first"
PDF_EXPORT_PRINT_TO_PDF_EX_FIRST = "print_to_pdf_ex_first"

PDF_PRINTER_NAME_CANDIDATES: tuple[str, ...] = (
    "Hancom PDF",
    "Microsoft Print to PDF",
)

PRINT_METHOD_NORMAL = 0
PRINT_RANGE_ALL = 0
PRINT_COPY_ONE = 1

CancelCheck = Callable[[], bool]


def normalize_pdf_export_mode(raw: str | None) -> str:
    if not raw:
        return PDF_EXPORT_SAVEAS_FIRST
    mode = str(raw).strip().lower()
    if mode in (PDF_EXPORT_PRINT_TO_PDF_EX_FIRST, "print", "print_first"):
        return PDF_EXPORT_PRINT_TO_PDF_EX_FIRST
    return PDF_EXPORT_SAVEAS_FIRST


def is_valid_pdf_file(path: Path, *, min_bytes: int = MIN_PDF_BYTES) -> bool:
    """PDF 매직 바이트와 최소 크기를 검사합니다."""
    try:
        if not path.is_file():
            return False
        size = path.stat().st_size
        if size < min_bytes:
            return False
        with path.open("rb") as f:
            header = f.read(len(PDF_MAGIC))
        return header == PDF_MAGIC
    except OSError:
        return False


def remove_incomplete_output(
    path: Path,
    *,
    before_mtime_ns: int | None,
    before_size: int | None,
) -> None:
    """내보내기 실패 후 깨진/부분 산출물을 정리합니다.

    변환 전에 이미 있던 파일이 손대지 않은 채(스냅샷과 동일)로 남아 있으면
    절대 지우지 않는다. before_*는 필수: 호출자가 스냅샷을 전달해야 한다.
    """
    try:
        if not path.exists():
            return
        st = path.stat()
        if before_mtime_ns is not None and before_size is not None:
            if st.st_mtime_ns == before_mtime_ns and st.st_size == before_size:
                return
        # 새로 생겼거나 내용이 바뀌었는데 유효 PDF가 아니면 제거
        if before_mtime_ns is None or not is_valid_pdf_file(path):
            path.unlink(missing_ok=True)
    except OSError:
        pass


def list_installed_printer_names() -> list[str]:
    """설치된 프린터 이름 목록 (win32print 없으면 빈 목록)."""
    try:
        import win32print  # type: ignore
    except ImportError:
        return []
    names: list[str] = []
    try:
        flags = getattr(win32print, "PRINTER_ENUM_LOCAL", 2) | getattr(
            win32print, "PRINTER_ENUM_CONNECTIONS", 4
        )
        for entry in win32print.EnumPrinters(flags):
            # (flags, description, name, comment) 형태가 일반적
            if len(entry) >= 3 and entry[2]:
                names.append(str(entry[2]))
    except Exception:
        pass
    return names


def resolve_pdf_printer_candidates(
    preferred: Sequence[str] | None = None,
) -> list[str]:
    """설치된 가상 PDF 프린터를 우선으로 후보 목록을 만든다."""
    preferred_list = list(preferred) if preferred else list(PDF_PRINTER_NAME_CANDIDATES)
    installed = list_installed_printer_names()
    installed_lower = {name.lower(): name for name in installed}

    ordered: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        key = name.lower()
        if key in seen:
            return
        seen.add(key)
        # 설치 목록에 있으면 실제 표기 사용
        ordered.append(installed_lower.get(key, name))

    for name in preferred_list:
        if name.lower() in installed_lower:
            _add(name)
    # 설치 목록에서 PDF/XPS 계열 프린터 추가 발굴
    for name in installed:
        lower = name.lower()
        if any(token in lower for token in ("pdf", "xps", "hancom")):
            _add(name)
    # 조회 실패 환경에서도 시도 목록 보장
    if not ordered:
        for name in preferred_list:
            _add(name)
    return ordered


def _set_param(obj: Any, attr: str, val: Any) -> bool:
    """HParameterSet/ActionSet 속성은 SetItem 우선, 없으면 setattr로 설정."""
    try:
        setter = getattr(obj, "SetItem", None)
        if callable(setter):
            setter(attr, val)
            return True
    except Exception:
        pass
    try:
        setattr(obj, attr, val)
        return True
    except Exception:
        return False


def _apply_safe_print_items(hprint: Any) -> int:
    """공통 안전 인쇄 값 적용. 성공한 항목 수를 반환한다."""
    pairs: list[tuple[str, Any]] = [
        ("PrintMethod", PRINT_METHOD_NORMAL),
        ("NumCopy", PRINT_COPY_ONE),
        ("ReverseOrder", 0),
        ("Pause", 0),
        ("Collate", 1),
        ("PrintImage", 1),
        ("PrintDrawObj", 1),
        ("PrintClickHere", 0),
        ("PrintToFile", 0),
        ("UserOrder", 0),
    ]
    applied = 0
    for key, value in pairs:
        if _set_param(hprint, key, value):
            applied += 1
    return applied


def apply_default_print_settings(hwp: Any) -> bool:
    """열린 문서의 인쇄 설정을 1쪽씩(PrintMethod=0) 기본값으로 best-effort 리셋.

    Execute(실제 인쇄/PDF 생성)는 절대 하지 않는다.
    SaveAs 경로 전에 호출하면 문서에 남은 모아찍기 등이 반영되는 것을 막는다.
    """
    if hwp is None:
        return False

    any_ok = False

    # 1) XHwpPrint 프로퍼티 (문서 단위)
    try:
        docs = getattr(hwp, "XHwpDocuments", None)
        if docs is not None:
            doc = docs.Item(0)
            prn = getattr(doc, "XHwpPrint", None)
            if prn is not None:
                for key, value in (
                    ("PrintMethod", PRINT_METHOD_NORMAL),
                    ("NumCopy", PRINT_COPY_ONE),
                    ("ReverseOrder", 0),
                ):
                    try:
                        setattr(prn, key, value)
                        any_ok = True
                    except Exception:
                        pass
    except Exception:
        pass

    # 2) HAction + HParameterSet.HPrint GetDefault (Execute 없음)
    try:
        hparam = getattr(hwp, "HParameterSet", None)
        haction = getattr(hwp, "HAction", None)
        if hparam is not None and haction is not None:
            pset = getattr(hparam, "HPrint", None)
            if pset is not None:
                hset = getattr(pset, "HSet", pset)
                for action_id in ("PrintToPDFEx", "Print"):
                    try:
                        haction.GetDefault(action_id, hset)
                        if _apply_safe_print_items(pset) > 0:
                            any_ok = True
                    except Exception:
                        pass
    except Exception:
        pass

    # 3) CreateAction("Print") GetDefault + SetItem 보정 (Execute 금지: 물리 인쇄 위험)
    try:
        create_action = getattr(hwp, "CreateAction", None)
        if callable(create_action):
            act: Any = create_action("Print")
            pset: Any = act.CreateSet()
            act.GetDefault(pset)
            if _apply_safe_print_items(pset) > 0:
                any_ok = True
    except Exception:
        pass

    return any_ok


def try_export_pdf_via_print_to_pdf_ex(
    hwp: Any,
    output_path: str | Path,
    *,
    cancel_check: CancelCheck | None = None,
    printer_names: Sequence[str] | None = None,
    max_printer_attempts: int = 2,
) -> tuple[bool, str | None]:
    """PrintToPDFEx 또는 RunToPDF로 PDF를 생성합니다 (물리 Print Execute 금지)."""
    if hwp is None:
        return False, None

    def _cancelled() -> bool:
        if cancel_check is None:
            return False
        try:
            return bool(cancel_check())
        except Exception:
            return False

    output = Path(output_path)
    output_str = str(output)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False, None

    before_mtime_ns: int | None = None
    before_size: int | None = None
    if output.exists():
        try:
            st = output.stat()
            before_mtime_ns = st.st_mtime_ns
            before_size = st.st_size
        except OSError:
            pass

    def _output_is_success() -> bool:
        if not output.exists():
            return False
        try:
            st = output.stat()
        except OSError:
            return False
        if st.st_size < MIN_PDF_BYTES:
            return False
        if before_mtime_ns is not None:
            if st.st_mtime_ns == before_mtime_ns and st.st_size == (before_size or 0):
                return False
        return is_valid_pdf_file(output)

    candidates = list(printer_names) if printer_names else resolve_pdf_printer_candidates()
    if max_printer_attempts > 0:
        candidates = candidates[:max_printer_attempts]
    if not candidates:
        candidates = list(PDF_PRINTER_NAME_CANDIDATES[:max_printer_attempts or 2])

    # 1. HAction PrintToPDFEx
    try:
        hparam = getattr(hwp, "HParameterSet", None)
        haction = getattr(hwp, "HAction", None)
        if hparam is not None and haction is not None:
            pset = getattr(hparam, "HPrint", None)
            if pset is not None:
                hset = getattr(pset, "HSet", pset)
                for printer_name in candidates:
                    if _cancelled():
                        remove_incomplete_output(output, before_mtime_ns=before_mtime_ns, before_size=before_size)
                        return False, None
                    try:
                        haction.GetDefault("PrintToPDFEx", hset)
                        _apply_safe_print_items(pset)
                        _set_param(pset, "FileName", output_str)
                        _set_param(pset, "filename", output_str)
                        _set_param(pset, "PrinterName", printer_name)
                        haction.Execute("PrintToPDFEx", hset)
                        if _output_is_success():
                            return True, EXPORT_METHOD_PRINT_TO_PDF_EX
                        remove_incomplete_output(output, before_mtime_ns=before_mtime_ns, before_size=before_size)
                    except Exception:
                        remove_incomplete_output(output, before_mtime_ns=before_mtime_ns, before_size=before_size)
    except Exception:
        pass

    if _cancelled():
        return False, None

    # 2. XHwpPrint.RunToPDF
    try:
        docs = getattr(hwp, "XHwpDocuments", None)
        if docs is not None:
            prn = docs.Item(0).XHwpPrint
            try:
                prn.PrintMethod = PRINT_METHOD_NORMAL
            except Exception:
                pass
            try:
                prn.filename = output_str
            except Exception:
                try:
                    prn.FileName = output_str
                except Exception:
                    pass
            for printer_name in candidates:
                if _cancelled():
                    remove_incomplete_output(output, before_mtime_ns=before_mtime_ns, before_size=before_size)
                    return False, None
                try:
                    prn.PrinterName = printer_name
                except Exception:
                    pass
                try:
                    prn.RunToPDF()
                    if _output_is_success():
                        return True, EXPORT_METHOD_RUN_TO_PDF
                    remove_incomplete_output(output, before_mtime_ns=before_mtime_ns, before_size=before_size)
                except Exception:
                    remove_incomplete_output(output, before_mtime_ns=before_mtime_ns, before_size=before_size)
    except Exception:
        pass

    remove_incomplete_output(output, before_mtime_ns=before_mtime_ns, before_size=before_size)
    return False, None
