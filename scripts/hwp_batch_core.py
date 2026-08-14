from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence, Set

SUPPORTED_EXTENSIONS = (".hwp", ".hwpx")
MAX_FILENAME_COUNTER = 1000
DEFAULT_STARTUP_TIMEOUT_SECONDS = 20.0
DEFAULT_FILE_TIMEOUT_SECONDS = 120.0

BACKUP_DIR_NAME = "backup"
BACKUP_MAX_FILES_PER_STEM = 20
BACKUP_MAX_FILES_PER_STEM_MIN = 1
BACKUP_MAX_FILES_PER_STEM_MAX = 100

MAX_RETRY_COUNT = 3
RETRY_DELAY_SECONDS = 1.0

WINDOWS_PATH_WARN_LENGTH = 240
WINDOWS_PATH_BLOCK_LENGTH = 260

AUXILIARY_ARTIFACT_FORMATS = frozenset({"HTML", "PNG", "JPG", "BMP", "GIF"})
AUXILIARY_NAME_DELIMITERS = frozenset({"_", "-", " ", ".", "("})
MAX_AUXILIARY_SCAN_FILES = 500

HWP_PROGIDS = [
    "HWPControl.HwpCtrl.1",
    "HwpObject.HwpObject",
    "HWPFrame.HwpObject",
]

FORMAT_TYPES: dict[str, dict[str, str]] = {
    "HWP": {"ext": ".hwp", "save_format": "HWP"},
    "HWPX": {"ext": ".hwpx", "save_format": "HWPX"},
    "PDF": {"ext": ".pdf", "save_format": "PDF"},
    "DOCX": {"ext": ".docx", "save_format": "OOXML"},
    "ODT": {"ext": ".odt", "save_format": "ODT"},
    "HTML": {"ext": ".html", "save_format": "HTML"},
    "RTF": {"ext": ".rtf", "save_format": "RTF"},
    "TXT": {"ext": ".txt", "save_format": "TEXT"},
    "PNG": {"ext": ".png", "save_format": "PNG"},
    "JPG": {"ext": ".jpg", "save_format": "JPG"},
    "BMP": {"ext": ".bmp", "save_format": "BMP"},
    "GIF": {"ext": ".gif", "save_format": "GIF"},
}

STATUS_PENDING = "대기"
STATUS_PLANNED = "계획됨"
STATUS_SUCCESS = "성공"
STATUS_FAILED = "실패"
STATUS_SKIPPED = "건너뜀"
FAIL_FAST_DETAIL = "이전 작업 실패 후 --fail-fast로 중단했습니다."


# ============================================================================
# 경로 유틸리티
# ============================================================================

def canonicalize_path(path: str | Path) -> str:
    return os.path.abspath(os.path.normpath(str(path)))


def path_char_length(path: str | Path) -> int:
    return len(str(path))


def is_path_length_risky(path: str | Path, *, warn_length: int = WINDOWS_PATH_WARN_LENGTH) -> bool:
    return path_char_length(path) >= max(1, int(warn_length))


def is_path_length_blocking(path: str | Path, *, block_length: int = WINDOWS_PATH_BLOCK_LENGTH) -> bool:
    return path_char_length(path) >= max(1, int(block_length))


def to_extended_win_path(path: str | Path) -> str:
    raw = str(path).strip()
    if not raw:
        return raw
    normalized = os.path.abspath(os.path.normpath(raw.replace("/", "\\")))
    if normalized.startswith("\\\\?\\"):
        return normalized
    if normalized.startswith("\\\\"):
        return "\\\\?\\UNC\\" + normalized[2:]
    return "\\\\?\\" + normalized


def com_path_candidates(path: str | Path) -> list[str]:
    primary = os.path.abspath(os.path.normpath(str(path)))
    extended = to_extended_win_path(primary)
    if primary == extended or path_char_length(primary) < 200:
        if primary == extended:
            return [primary]
        return [primary, extended]
    return [extended, primary]


def is_valid_path_name(path: str | Path) -> bool:
    raw = str(path).strip()
    if not raw:
        return False
    if any(ord(char) < 32 for char in raw):
        return False

    normalized = raw.replace("/", "\\")
    extended_unc_prefix = "\\\\?\\UNC\\"
    extended_prefix = "\\\\?\\"
    if normalized.startswith(extended_unc_prefix):
        normalized = "\\\\" + normalized[len(extended_unc_prefix) :]
    elif normalized.startswith(extended_prefix):
        normalized = normalized[len(extended_prefix) :]

    invalid_chars = '<>"|?*'
    if any(char in normalized for char in invalid_chars):
        return False

    path_without_drive = normalized
    if len(normalized) >= 2 and normalized[1] == ":":
        if not normalized[0].isalpha():
            return False
        path_without_drive = normalized[2:]
    if ":" in path_without_drive:
        return False

    reserved_names = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    }
    for part in re.split(r"[\\]+", path_without_drive):
        if not part or part in {".", ".."}:
            continue
        if part.endswith((" ", ".")):
            return False
        base = part.split(".")[0].upper()
        if base in reserved_names:
            return False
    return True


def check_write_permission(folder_path: Path) -> bool:
    try:
        with tempfile.NamedTemporaryFile(
            dir=folder_path,
            prefix=".hwp_batch_write_test_",
            delete=True,
        ):
            pass
        return True
    except (PermissionError, OSError):
        return False


def iter_supported_files(
    root_path: Path,
    include_sub: bool = True,
    allowed_exts: Optional[Iterable[str]] = None,
    excluded_dir_names: Optional[Iterable[str]] = None,
) -> Iterable[Path]:
    allowed = {ext.lower() for ext in (allowed_exts or SUPPORTED_EXTENSIONS)}
    excluded_dirs = {name.lower() for name in (excluded_dir_names or (BACKUP_DIR_NAME,))}

    if root_path.is_file():
        if root_path.suffix.lower() in allowed:
            yield root_path
        return
    if not root_path.is_dir():
        return

    if include_sub:
        for dirpath, dirnames, filenames in os.walk(root_path):
            dirnames[:] = [d for d in dirnames if d.lower() not in excluded_dirs]
            for filename in filenames:
                _, ext = os.path.splitext(filename)
                if ext.lower() in allowed:
                    yield Path(dirpath) / filename
        return

    with os.scandir(root_path) as entries:
        for entry in entries:
            if not entry.is_file():
                continue
            _, ext = os.path.splitext(entry.name)
            if ext.lower() in allowed:
                yield Path(entry.path)


def dedupe_strings(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def parse_json_text(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def kill_processes(pids: Iterable[int]) -> list[int]:
    killed: list[int] = []
    for pid in sorted({int(pid) for pid in pids if int(pid) > 0}):
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=False,
            )
        except Exception:
            continue
        if result.returncode == 0:
            killed.append(pid)
    return killed


# ============================================================================
# 보조 산출물 (Auxiliary Artifacts) 추적 및 정책
# ============================================================================

def uses_auxiliary_artifacts(format_type: str) -> bool:
    return format_type.upper() in AUXILIARY_ARTIFACT_FORMATS


def matches_artifact_stem(name: str, stem: str) -> bool:
    name_key = name.lower()
    stem_key = stem.lower()
    if not stem_key:
        return False
    if name_key == stem_key:
        return True
    if not name_key.startswith(stem_key):
        return False
    if len(name_key) == len(stem_key):
        return True
    return name_key[len(stem_key)] in AUXILIARY_NAME_DELIMITERS


def artifact_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve() if path.exists() else path.absolute()))


def iter_candidate_artifact_paths(
    output_file: Path,
    format_type: str,
    *,
    include_nested: bool = True,
    nested_limit: int = MAX_AUXILIARY_SCAN_FILES,
) -> list[Path]:
    candidates: dict[str, Path] = {artifact_key(output_file): output_file}
    if not uses_auxiliary_artifacts(format_type):
        return list(candidates.values())

    parent = output_file.parent
    if not parent.exists():
        return list(candidates.values())

    stem = output_file.stem
    nested_count = 0
    try:
        for child in parent.iterdir():
            if not matches_artifact_stem(child.name, stem):
                continue
            if child.is_file():
                candidates[artifact_key(child)] = child
                continue
            if child.is_dir() and include_nested:
                if nested_count >= nested_limit:
                    continue
                try:
                    for nested in child.rglob("*"):
                        if not nested.is_file():
                            continue
                        if nested_count >= nested_limit:
                            break
                        candidates[artifact_key(nested)] = nested
                        nested_count += 1
                except OSError:
                    continue
    except OSError:
        return list(candidates.values())

    return list(candidates.values())


def existing_artifact_conflicts(output_file: Path, format_type: str) -> list[Path]:
    conflicts: dict[str, Path] = {}
    if output_file.exists():
        conflicts[artifact_key(output_file)] = output_file

    if not uses_auxiliary_artifacts(format_type):
        return list(conflicts.values())

    parent = output_file.parent
    if not parent.exists():
        return list(conflicts.values())

    try:
        for child in parent.iterdir():
            if child == output_file:
                continue
            if matches_artifact_stem(child.name, output_file.stem):
                conflicts[artifact_key(child)] = child
    except OSError:
        return list(conflicts.values())

    return sorted(conflicts.values(), key=lambda path: str(path).lower())


@dataclass(frozen=True)
class FileArtifactSnapshot:
    size: int
    mtime_ns: int


def snapshot_artifacts(output_file: Path, format_type: str) -> dict[Path, FileArtifactSnapshot]:
    snapshot: dict[Path, FileArtifactSnapshot] = {}
    candidates = iter_candidate_artifact_paths(output_file, format_type, include_nested=True)
    for path in candidates:
        try:
            if not path.is_file():
                continue
            st = path.stat()
            snapshot[path] = FileArtifactSnapshot(size=st.st_size, mtime_ns=st.st_mtime_ns)
        except OSError:
            continue
    return snapshot


def changed_artifacts(
    before: dict[Path, FileArtifactSnapshot],
    after: dict[Path, FileArtifactSnapshot],
) -> list[Path]:
    changed: list[Path] = []
    for path, meta in after.items():
        prev = before.get(path)
        if prev is None:
            changed.append(path)
            continue
        if meta.size != prev.size or meta.mtime_ns != prev.mtime_ns:
            changed.append(path)
    return sorted(changed, key=lambda p: str(p).lower())


def remove_new_attempt_artifacts(
    before: dict[Path, FileArtifactSnapshot],
    output_file: Path,
    format_type: str,
) -> tuple[list[Path], list[str]]:
    removed: list[Path] = []
    warnings: list[str] = []
    current = iter_candidate_artifact_paths(output_file, format_type, include_nested=True)
    for path in current:
        if path in before:
            continue
        try:
            if path.is_file():
                path.unlink(missing_ok=True)
                removed.append(path)
        except OSError as e:
            warnings.append(f"임시 파일 정리 실패: {path.name} ({e})")

    # 빈 디렉터리 정리
    if uses_auxiliary_artifacts(format_type):
        parent = output_file.parent
        stem = output_file.stem
        if parent.exists():
            try:
                for child in parent.iterdir():
                    if child.is_dir() and matches_artifact_stem(child.name, stem):
                        try:
                            if not any(child.iterdir()):
                                child.rmdir()
                        except OSError:
                            pass
            except OSError:
                pass

    return sorted(removed, key=lambda p: str(p).lower()), warnings


# ============================================================================
# 백업(Backup) 시스템
# ============================================================================

def clamp_backup_max(max_files: int | None) -> int:
    base = BACKUP_MAX_FILES_PER_STEM if max_files is None else int(max_files)
    return max(BACKUP_MAX_FILES_PER_STEM_MIN, min(BACKUP_MAX_FILES_PER_STEM_MAX, base))


def prune_old_backups(
    backup_dir: Path,
    stem: str,
    suffix: str,
    *,
    keep_path: Path | None = None,
    max_files: int | None = None,
) -> None:
    try:
        max_keep = clamp_backup_max(max_files)
        prefix = f"{stem}_"
        keep_resolved = keep_path.resolve() if keep_path is not None else None
        candidates: list[Path] = []
        for entry in backup_dir.iterdir():
            if not entry.is_file():
                continue
            if entry.suffix.lower() != suffix.lower():
                continue
            if not entry.name.startswith(prefix):
                continue
            if keep_resolved is not None and entry.resolve() == keep_resolved:
                continue
            candidates.append(entry)

        slots_for_old = max_keep - (1 if keep_resolved is not None else 0)
        if slots_for_old < 0:
            slots_for_old = 0
        if len(candidates) <= slots_for_old:
            return
        candidates.sort(key=lambda p: (p.stat().st_mtime, p.name))
        for old in candidates[: len(candidates) - slots_for_old]:
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        pass


def create_backup(file_path: Path, *, max_files: int | None = None) -> Path:
    try:
        backup_dir = file_path.parent / BACKUP_DIR_NAME
        backup_dir.mkdir(exist_ok=True)

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_name = f"{file_path.stem}_{timestamp}{file_path.suffix}"
        backup_path = backup_dir / backup_name
        counter = 1

        while backup_path.exists():
            backup_name = f"{file_path.stem}_{timestamp}_{counter}{file_path.suffix}"
            backup_path = backup_dir / backup_name
            counter += 1

        shutil.copy2(file_path, backup_path)
        prune_old_backups(
            backup_dir,
            file_path.stem,
            file_path.suffix,
            keep_path=backup_path,
            max_files=max_files,
        )
        return backup_path
    except Exception as e:
        raise RuntimeError(f"백업 생성 실패 ({file_path.name}): {e}") from e


# ============================================================================
# 데이터 모델
# ============================================================================

@dataclass
class AutoDialogEvent:
    window_title: str
    window_text: str
    button_text: str
    clicked: bool
    reason: str
    process_id: int | None = None
    timestamp: float = field(default_factory=time.time)

    def to_record(self) -> dict[str, Any]:
        return {
            "timestamp": round(self.timestamp, 3),
            "window_title": self.window_title,
            "window_text": self.window_text,
            "button_text": self.button_text,
            "clicked": self.clicked,
            "reason": self.reason,
            "process_id": self.process_id,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "AutoDialogEvent":
        return cls(
            window_title=str(record.get("window_title", "")),
            window_text=str(record.get("window_text", "")),
            button_text=str(record.get("button_text", "")),
            clicked=bool(record.get("clicked", False)),
            reason=str(record.get("reason", "")),
            process_id=record.get("process_id"),
            timestamp=float(record.get("timestamp", time.time())),
        )


@dataclass
class ConversionTask:
    input_file: Path
    output_file: Path
    source_root: Path | None = None
    status: str = STATUS_PENDING
    error: str | None = None
    created_files: list[Path] = field(default_factory=list)
    output_size: int | None = None
    output_mtime: float | None = None
    save_format: str | None = None
    export_method: str | None = None
    progid_used: str | None = None
    backup_file: Path | None = None
    retry_count: int = 0

    def to_record(self) -> dict[str, Any]:
        rec: dict[str, Any] = {
            "input_file": str(self.input_file),
            "output_file": str(self.output_file),
            "source_root": str(self.source_root) if self.source_root else None,
            "status": self.status,
            "detail": self.error or "",
        }
        if self.retry_count > 0:
            rec["retry_count"] = self.retry_count
        if self.created_files:
            rec["created_files"] = [str(p) for p in self.created_files]
        if self.output_size is not None:
            rec["output_size"] = self.output_size
        if self.output_mtime is not None:
            rec["output_mtime"] = round(self.output_mtime, 3)
        if self.save_format:
            rec["save_format"] = self.save_format
        if self.export_method:
            rec["export_method"] = self.export_method
        if self.progid_used:
            rec["progid_used"] = self.progid_used
        if self.backup_file:
            rec["backup_file"] = str(self.backup_file)
        return rec


@dataclass
class PlannedConversion:
    format_type: str
    same_location: bool
    output_path: str
    tasks: list[ConversionTask] = field(default_factory=list)
    skipped_tasks: list[ConversionTask] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    conflict_renamed_count: int = 0
    backup_enabled: bool = False
    backup_max_per_stem: int = BACKUP_MAX_FILES_PER_STEM
    pdf_export_mode: str = "saveas_first"


@dataclass
class ConversionSummary:
    format_type: str
    tasks: list[ConversionTask] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    elapsed_seconds: float | None = None
    progid_used: str | None = None
    mode: str = "real"
    auto_dialog_enabled: bool = False
    auto_dialog_events: list[AutoDialogEvent] = field(default_factory=list)
    backup_enabled: bool = False
    backup_count: int = 0
    pdf_export_mode: str = "saveas_first"

    def to_json_dict(self) -> dict[str, Any]:
        success = len([task for task in self.tasks if task.status == STATUS_SUCCESS])
        failed = len([task for task in self.tasks if task.status == STATUS_FAILED])
        skipped = len([task for task in self.tasks if task.status == STATUS_SKIPPED])
        clicked = len([event for event in self.auto_dialog_events if event.clicked])
        detected = len(self.auto_dialog_events)
        total_created = sum(len(t.created_files) for t in self.tasks if t.status == STATUS_SUCCESS)
        total_output_size = sum(t.output_size or 0 for t in self.tasks if t.status == STATUS_SUCCESS)

        return {
            "summary": {
                "format_type": self.format_type,
                "mode": self.mode,
                "total_requested": len(self.tasks),
                "success_count": success,
                "failed_count": failed,
                "skipped_count": skipped,
                "total_created_files": total_created or success,
                "total_output_size_bytes": total_output_size,
                "elapsed_seconds": self.elapsed_seconds,
                "progid_used": self.progid_used,
                "pdf_export_mode": self.pdf_export_mode if self.format_type == "PDF" else None,
                "backup_enabled": self.backup_enabled,
                "backup_count": self.backup_count,
                "warnings": dedupe_strings(self.warnings),
                "auto_dialog_enabled": self.auto_dialog_enabled,
                "auto_dialog_detected_count": detected,
                "auto_dialog_clicked_count": clicked,
            },
            "tasks": [task.to_record() for task in sorted(self.tasks, key=lambda item: str(item.input_file).lower())],
            "auto_dialog_events": [event.to_record() for event in self.auto_dialog_events],
        }


@dataclass
class RealWorkerResult:
    ok: bool
    error: str | None
    warnings: list[str] = field(default_factory=list)
    progid_used: str | None = None
    auto_dialog_events: list[AutoDialogEvent] = field(default_factory=list)
    created_files: list[Path] = field(default_factory=list)
    output_size: int | None = None
    output_mtime: float | None = None
    save_format: str | None = None
    export_method: str | None = None
    backup_file: Path | None = None
    final_output_file: Path | None = None


# ============================================================================
# 작업 플래너 (TaskPlanner)
# ============================================================================

class TaskPlanner:
    def build_tasks(
        self,
        *,
        sources: list[str],
        format_type: str,
        include_sub: bool,
        same_location: bool,
        output_path: str,
        allow_empty: bool,
        preserve_source_root: bool,
        backup_enabled: bool = False,
        backup_max_per_stem: int = BACKUP_MAX_FILES_PER_STEM,
        pdf_export_mode: str = "saveas_first",
    ) -> PlannedConversion:
        tasks: list[ConversionTask] = []
        skipped: list[ConversionTask] = []
        warnings: list[str] = []
        out_ext = FORMAT_TYPES[format_type]["ext"]

        if not sources:
            raise ValueError("파일 또는 폴더를 하나 이상 지정해주세요.")

        normalized_sources = [Path(canonicalize_path(src)) for src in sources]
        multiple_sources = len(normalized_sources) > 1
        explicit_output_root = Path(canonicalize_path(output_path)) if output_path else None

        if explicit_output_root:
            if not explicit_output_root.exists():
                try:
                    explicit_output_root.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    raise ValueError(f"출력 폴더를 생성할 수 없습니다: {explicit_output_root} ({e})") from e
            if not check_write_permission(explicit_output_root):
                warnings.append(f"출력 폴더에 쓰기 권한이 부족할 수 있습니다: {explicit_output_root}")

        if multiple_sources and explicit_output_root and not same_location and not preserve_source_root:
            warnings.append("여러 입력 소스를 함께 변환할 때 결과 추적이 중요하면 --preserve-source-root 사용을 권장합니다.")

        for source in normalized_sources:
            if not source.exists():
                raise ValueError(f"입력 경로가 존재하지 않습니다: {source}")

            source_root = source if source.is_dir() else source.parent
            source_files: list[Path]

            if source.is_file():
                if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    if allow_empty:
                        warnings.append(f"지원하지 않는 입력 파일을 건너뜀: {source}")
                        continue
                    raise ValueError(f"지원하지 않는 입력 파일입니다: {source}")
                source_files = [source]
            else:
                source_files = sorted(
                    iter_supported_files(source, include_sub=include_sub),
                    key=lambda path: str(path).lower(),
                )
                if not source_files:
                    warnings.append(f"지원 파일이 없는 폴더를 건너뜀: {source}")

            for input_file in source_files:
                if is_path_length_risky(input_file):
                    warnings.append(f"입력 파일 경로가 너무 깁니다 (240자 이상): {input_file.name}")

                if input_file.suffix.lower() == out_ext.lower():
                    skipped.append(
                        ConversionTask(
                            input_file=input_file,
                            output_file=input_file,
                            source_root=source_root,
                            status=STATUS_SKIPPED,
                            error=f"이미 {format_type} 형식입니다.",
                        )
                    )
                    continue

                if same_location:
                    output_file = input_file.parent / f"{input_file.stem}{out_ext}"
                else:
                    if explicit_output_root is None:
                        raise ValueError("--output-dir 또는 --same-location 중 하나가 필요합니다.")
                    output_file = self._build_output_file(
                        source=source,
                        input_file=input_file,
                        output_root=explicit_output_root,
                        output_ext=out_ext,
                        multiple_sources=multiple_sources,
                        preserve_source_root=preserve_source_root,
                    )

                if is_path_length_risky(output_file):
                    warnings.append(f"출력 파일 경로가 너무 깁니다 (240자 이상): {output_file.name}")

                tasks.append(
                    ConversionTask(
                        input_file=input_file,
                        output_file=output_file,
                        source_root=source_root,
                    )
                )

        if skipped:
            warnings.append(f"동일 형식 {len(skipped)}개는 자동으로 건너뜁니다.")

        if not tasks and not skipped:
            if allow_empty:
                warnings.append("변환 대상이 없습니다.")
            else:
                raise ValueError("변환할 지원 파일이 없습니다.")

        return PlannedConversion(
            format_type=format_type,
            same_location=same_location,
            output_path=output_path,
            tasks=tasks,
            skipped_tasks=skipped,
            warnings=warnings,
            backup_enabled=backup_enabled,
            backup_max_per_stem=backup_max_per_stem,
            pdf_export_mode=pdf_export_mode,
        )

    def _build_output_file(
        self,
        *,
        source: Path,
        input_file: Path,
        output_root: Path,
        output_ext: str,
        multiple_sources: bool,
        preserve_source_root: bool,
    ) -> Path:
        filename = f"{input_file.stem}{output_ext}"
        if preserve_source_root:
            prefix = self._source_prefix(source)
            if source.is_file():
                return output_root / prefix / filename
            relative_path = input_file.relative_to(source)
            return output_root / prefix / relative_path.parent / filename
        if source.is_file() or multiple_sources:
            return output_root / filename
        relative_path = input_file.relative_to(source)
        return output_root / relative_path.parent / filename

    def _source_prefix(self, source: Path) -> Path:
        if source.is_dir():
            return Path(source.name or "source")
        parent_name = source.parent.name or "files"
        return Path(parent_name)

    def allocate_output_path(
        self,
        task: ConversionTask,
        *,
        used_path_keys: set[str],
        overwrite: bool,
        format_type: str | None = None,
    ) -> bool:
        """충돌 없는 출력 파일 경로를 할당하고 변경 여부를 반환합니다."""
        original_path = task.output_file
        orig_key = artifact_key(original_path)
        batch_duplicate = orig_key in used_path_keys
        conflicts = [] if overwrite else existing_artifact_conflicts(original_path, format_type or "PDF")
        has_existing_conflict = bool(conflicts)

        if not (batch_duplicate or has_existing_conflict):
            used_path_keys.add(orig_key)
            return False

        counter = 1
        stem = original_path.stem
        ext = original_path.suffix
        parent = original_path.parent
        while counter <= MAX_FILENAME_COUNTER:
            candidate = parent / f"{stem} ({counter}){ext}"
            candidate_key = artifact_key(candidate)
            cand_conflicts = [] if overwrite else existing_artifact_conflicts(candidate, format_type or "PDF")
            if (candidate_key not in used_path_keys) and not cand_conflicts:
                task.output_file = candidate
                used_path_keys.add(candidate_key)
                return True
            counter += 1

        fallback_name = f"{stem}_{int(time.time())}{ext}"
        task.output_file = parent / fallback_name
        used_path_keys.add(artifact_key(task.output_file))
        return True

    def resolve_output_conflicts(
        self,
        tasks: list[ConversionTask],
        overwrite: bool,
        format_type: str | None = None,
    ) -> int:
        if overwrite:
            return 0
        used_path_keys: set[str] = set()
        renamed_count = 0
        for task in tasks:
            if self.allocate_output_path(task, used_path_keys=used_path_keys, overwrite=overwrite, format_type=format_type):
                renamed_count += 1
        return renamed_count


# ============================================================================
# Mock 변환기
# ============================================================================

class MockConverter:
    def __init__(self) -> None:
        self.progid_used = "mock"
        self.last_created_files: list[Path] = []
        self.last_output_size: int | None = None
        self.last_output_mtime: float | None = None
        self.last_save_format: str | None = None
        self.last_export_method: str | None = "MockSave"

    def initialize(self) -> bool:
        return True

    def convert_file(self, input_path: Path, output_path: Path, format_type: str = "PDF") -> tuple[bool, str | None]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if format_type.upper() == "PDF":
            payload = b"%PDF-1.4 Mock PDF stream for testing\n%%EOF\n"
            output_path.write_bytes(payload)
        else:
            payload_str = f"mock-converted:{Path(input_path).name}->{format_type}\n"
            output_path.write_text(payload_str, encoding="utf-8")

        self.last_created_files = [output_path]
        self.last_output_size = output_path.stat().st_size
        self.last_output_mtime = output_path.stat().st_mtime
        self.last_save_format = FORMAT_TYPES.get(format_type, {}).get("save_format", format_type)
        self.last_export_method = "MockSave"
        return True, None

    def cleanup(self) -> None:
        return None


# ============================================================================
# 요약 및 종료 코드
# ============================================================================

def render_human(summary: ConversionSummary) -> str:
    data = summary.to_json_dict()["summary"]
    lines = [
        f"형식: {data['format_type']} ({data['mode']})",
        f"총 {data['total_requested']}건 | 성공 {data['success_count']} | 실패 {data['failed_count']} | 건너뜀 {data['skipped_count']}",
    ]
    if summary.backup_enabled:
        lines.append(f"백업 생성: {data['backup_count']}건 (backup/ 폴더)")
    if data["warnings"]:
        lines.append("경고: " + " / ".join(data["warnings"]))
    if summary.auto_dialog_enabled:
        lines.append(
            f"보안 팝업 자동 허용: 감지 {data['auto_dialog_detected_count']} | 클릭 {data['auto_dialog_clicked_count']}"
        )
    failed = [task for task in summary.tasks if task.status == STATUS_FAILED]
    if failed:
        lines.append("실패 목록:")
        for task in failed[:10]:
            lines.append(f"- {task.input_file.name}: {task.error}")
    return "\n".join(lines)


def determine_exit_code(summary: ConversionSummary, allow_partial_success: bool) -> int:
    failed = len([task for task in summary.tasks if task.status == STATUS_FAILED])
    if failed and not allow_partial_success:
        return 1
    return 0
