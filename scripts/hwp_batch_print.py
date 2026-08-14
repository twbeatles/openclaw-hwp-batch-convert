from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Sequence

PDF_MAGIC = b"%PDF"
MIN_PDF_BYTES = 64

EXPORT_METHOD_SAVEAS_2 = "SaveAs-2param"
EXPORT_METHOD_SAVEAS_3 = "SaveAs-3param"
EXPORT_METHOD_PRINT_TO_PDF_EX = "PrintToPDFEx"
EXPORT_METHOD_RUN_TO_PDF = "RunToPDF"

PDF_EXPORT_SAVEAS_FIRST = "saveas_first"
PDF_EXPORT_PRINT_TO_PDF_EX_FIRST = "print_to_pdf_ex_first"

PDF_PRINTER_NAME_CANDIDATES: tuple[str, ...] = (
    "Hancom PDF",
    "Microsoft Print to PDF",
    "Adobe PDF",
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
    before_mtime_ns: int | None = None,
    before_size: int | None = None,
) -> None:
    """내보내기 실패 후 깨진/부분 산출물을 정리합니다."""
    try:
        if not path.exists():
            return
        st = path.stat()
        if before_mtime_ns is not None and before_size is not None:
            if st.st_mtime_ns == before_mtime_ns and st.st_size == before_size:
                return
        if before_mtime_ns is None or not is_valid_pdf_file(path):
            path.unlink(missing_ok=True)
    except OSError:
        pass


def resolve_pdf_printer_candidates() -> list[str]:
    """시스템에 등록된 가상 PDF 프린터 목록을 탐색합니다."""
    found: list[str] = []
    try:
        import win32print  # type: ignore

        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        for item in win32print.EnumPrinters(flags):
            name = item[2]
            if any(cand.lower() in name.lower() for cand in ("hancom pdf", "print to pdf", "adobe pdf")):
                found.append(name)
    except Exception:
        pass

    for candidate in PDF_PRINTER_NAME_CANDIDATES:
        if candidate not in found:
            found.append(candidate)
    return found


def _set_param(obj: Any, attr: str, val: Any) -> bool:
    try:
        setattr(obj, attr, val)
        return True
    except Exception:
        return False


def _apply_safe_print_items(hprint: Any) -> None:
    """HPrint 파라미터를 기본(1쪽씩 일반 인쇄)으로 안전하게 리셋합니다."""
    for attr, val in (
        ("PrintMethod", PRINT_METHOD_NORMAL),
        ("printmethod", PRINT_METHOD_NORMAL),
        ("Range", PRINT_RANGE_ALL),
        ("range", PRINT_RANGE_ALL),
        ("NumCopy", PRINT_COPY_ONE),
        ("numcopy", PRINT_COPY_ONE),
        ("Collate", 0),
        ("collate", 0),
        ("Device", 0),
        ("device", 0),
        ("ReverseOrder", 0),
        ("reverseorder", 0),
        ("Pause", 0),
        ("pause", 0),
        ("PrintToFile", 0),
        ("printtofile", 0),
    ):
        _set_param(hprint, attr, val)


def apply_default_print_settings(hwp: Any) -> bool:
    """HWP COM 문서의 인쇄 설정을 기본값으로 리셋합니다."""
    if hwp is None:
        return False
    any_applied = False
    try:
        hparam = getattr(hwp, "HParameterSet", None)
        haction = getattr(hwp, "HAction", None)
        if hparam is not None and haction is not None:
            pset = getattr(hparam, "HPrint", None)
            if pset is not None:
                hset = getattr(pset, "HSet", pset)
                haction.GetDefault("PrintToPDFEx", hset)
                _apply_safe_print_items(pset)
                any_applied = True
    except Exception:
        pass

    try:
        docs = getattr(hwp, "XHwpDocuments", None)
        if docs is not None:
            prn = docs.Item(0).XHwpPrint
            prn.PrintMethod = PRINT_METHOD_NORMAL
            any_applied = True
    except Exception:
        pass

    return any_applied


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
