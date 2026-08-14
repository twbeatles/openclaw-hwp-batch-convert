from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

SECURITY_MODULE_DLL_NAME = "FilePathCheckerModuleExample.dll"
SECURITY_MODULE_ALIAS = "FilePathCheckerModuleExample"
# 한컴 FilePathCheckerModuleExample.dll 기본 SHA-256
SECURITY_MODULE_DLL_SHA256 = "9ac5b97c47ac8aed1e8bca27a3eef39411361d8f68c262509f0c40a8f9d21bb6"
EXPECTED_DLL_SHA256 = SECURITY_MODULE_DLL_SHA256.lower()

# Automation(HWPFrame) 과 Ctrl 경로 모두 등록 (한컴 버전·ProgID 별 조회 위치 차이 대응)
REGISTRY_KEY_PATHS = (
    r"Software\HNC\HwpAutomation\Modules",
    r"Software\HNC\HwpCtrl\Modules",
    r"Software\Hnc\HwpAutomation\Modules",
    r"Software\Hnc\HwpCtrl\Modules",
)
REGISTRY_KEY_PATH = REGISTRY_KEY_PATHS[0]


def runtime_security_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "HwpMate" / "security"
    return Path.home() / ".hwp_converter" / "security"


def bundled_dll_candidates() -> list[Path]:
    candidates: list[Path] = []
    
    # 1. PyInstaller _MEIPASS
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        base = Path(meipass)
        candidates.append(base / "resources" / "security" / SECURITY_MODULE_DLL_NAME)
        candidates.append(base / SECURITY_MODULE_DLL_NAME)

    # 2. 현재 스크립트 상대 위치 (resources/security 또는 같은 폴더)
    try:
        script_dir = Path(__file__).resolve().parent
        candidates.append(script_dir / "resources" / "security" / SECURITY_MODULE_DLL_NAME)
        candidates.append(script_dir.parent / "resources" / "security" / SECURITY_MODULE_DLL_NAME)
        candidates.append(script_dir / SECURITY_MODULE_DLL_NAME)
    except OSError:
        pass

    # 3. 로컬 HwpMate 저장소 경로 후보 (개발/연동 환경)
    hwp_mate_dirs = [
        Path(r"D:\twbeatles-repos\HwpMate\hwpmate\resources\security"),
        Path(r"D:\github\HwpMate\hwpmate\resources\security"),
    ]
    for d in hwp_mate_dirs:
        candidates.append(d / SECURITY_MODULE_DLL_NAME)

    # 4. 실행 파일 디렉터리
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "security" / SECURITY_MODULE_DLL_NAME)
        candidates.append(exe_dir / SECURITY_MODULE_DLL_NAME)

    return candidates


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_dll_integrity(path: Path, *, expected_sha256: str = EXPECTED_DLL_SHA256) -> None:
    actual = sha256_file(path)
    if actual.lower() != expected_sha256.lower():
        raise ValueError(
            f"보안 모듈 DLL 무결성 검증 실패: {path} "
            f"(expected={expected_sha256[:16]}..., actual={actual[:16]}...)"
        )


def find_bundled_security_dll() -> Optional[Path]:
    for path in bundled_dll_candidates():
        try:
            if path.is_file() and path.stat().st_size > 0:
                return path
        except OSError:
            continue
    return None


def install_security_dll() -> Path:
    dest_dir = runtime_security_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / SECURITY_MODULE_DLL_NAME

    source = find_bundled_security_dll()
    if source is None:
        if dest.is_file() and dest.stat().st_size > 0:
            verify_dll_integrity(dest)
            return dest
        raise FileNotFoundError(
            f"보안 모듈 DLL을 찾을 수 없습니다: {SECURITY_MODULE_DLL_NAME}."
        )

    verify_dll_integrity(source)

    try:
        need_copy = True
        if dest.is_file() and dest.stat().st_size > 0:
            try:
                verify_dll_integrity(dest)
                need_copy = (
                    dest.stat().st_size != source.stat().st_size
                    or dest.stat().st_mtime < source.stat().st_mtime
                )
            except ValueError:
                need_copy = True

        if need_copy:
            tmp = dest.with_suffix(".dll.tmp")
            shutil.copy2(source, tmp)
            tmp.replace(dest)
            verify_dll_integrity(dest)
    except OSError:
        if dest.is_file() and dest.stat().st_size > 0:
            verify_dll_integrity(dest)
            return dest
        raise
    return dest


def write_security_module_registry(dll_path: Path, alias: str = SECURITY_MODULE_ALIAS) -> str:
    try:
        import winreg
    except ImportError as e:
        raise RuntimeError("winreg를 사용할 수 없는 환경입니다.") from e

    dll_abs = str(dll_path.resolve())
    if dll_abs.startswith('"') and dll_abs.endswith('"'):
        dll_abs = dll_abs[1:-1]

    written = 0
    last_error: Exception | None = None
    for reg_path in REGISTRY_KEY_PATHS:
        try:
            key = winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                reg_path,
                0,
                winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
            )
            try:
                winreg.SetValueEx(key, alias, 0, winreg.REG_SZ, dll_abs)
                value, reg_type = winreg.QueryValueEx(key, alias)
                if reg_type != winreg.REG_SZ or str(value).strip().strip('"') != dll_abs:
                    raise RuntimeError(f"레지스트리 검증 실패: {reg_path}\\{alias}={value!r}")
            finally:
                winreg.CloseKey(key)
            written += 1
        except Exception as e:
            last_error = e

    if written == 0:
        raise RuntimeError(f"보안 모듈 레지스트리 등록 실패: {last_error}")
    return alias


def read_security_module_registry(alias: str = SECURITY_MODULE_ALIAS) -> Optional[str]:
    try:
        import winreg
    except ImportError:
        return None
    for reg_path in REGISTRY_KEY_PATHS:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_QUERY_VALUE)
            try:
                value, _ = winreg.QueryValueEx(key, alias)
                return str(value).strip().strip('"')
            finally:
                winreg.CloseKey(key)
        except OSError:
            continue
    return None


def ensure_hwp_security_module(alias: str = SECURITY_MODULE_ALIAS) -> tuple[bool, str, Optional[str]]:
    """보안 모듈 DLL 설치 + 레지스트리 등록을 보장합니다.

    Returns:
        (ok, message, alias_or_none)
    """
    try:
        dll_path = install_security_dll()
        if not dll_path.is_file():
            return False, f"DLL 파일이 없습니다: {dll_path}", None
        verify_dll_integrity(dll_path)
        registered_alias = write_security_module_registry(dll_path, alias=alias)
        verified = read_security_module_registry(registered_alias)
        if not verified or not Path(verified).is_file():
            return (
                False,
                f"레지스트리 경로 검증 실패: alias={registered_alias}, path={verified!r}",
                None,
            )
        verify_dll_integrity(Path(verified))
        return True, f"DLL={verified}", registered_alias
    except Exception as e:
        return False, str(e), None
