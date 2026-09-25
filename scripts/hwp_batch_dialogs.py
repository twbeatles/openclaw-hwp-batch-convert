from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

from hwp_batch_core import AutoDialogEvent

DIALOG_TITLE_WHITELIST = {"한글"}
DIALOG_TEXT_KEYWORDS = ("접근하려는 시도",)
DIALOG_ALLOW_BUTTONS = ("모두 허용", "허용")
POLL_INTERVAL_SECONDS = 0.35
WINDOW_SCAN_TIMEOUT_SECONDS = 3.0
BM_CLICK = 0x00F5
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E

USER32 = ctypes.WinDLL("user32", use_last_error=True)
USER32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
USER32.GetWindowThreadProcessId.restype = wintypes.DWORD

# 호환 확인 창 응답(PostMessageW)은 순수 정수만 전달하므로 64비트 HWND 절단을 막기 위해 시그니처 고정.
USER32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
USER32.PostMessageW.restype = wintypes.BOOL
USER32.IsWindowVisible.argtypes = [wintypes.HWND]
USER32.IsWindowVisible.restype = wintypes.BOOL


def _get_window_text(hwnd: int) -> str:
    length = USER32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    USER32.SendMessageW(hwnd, WM_GETTEXT, length + 1, ctypes.byref(buffer))
    return buffer.value.strip()


def _get_class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    USER32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def _get_window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


class AutoAllowDialogWatcher:
    def __init__(
        self,
        *,
        enabled: bool = False,
        allowed_pids: set[int] | None = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ) -> None:
        self.enabled = enabled
        self.allowed_pids = None if allowed_pids is None else set(allowed_pids)
        self.poll_interval = poll_interval
        self.events: list[AutoDialogEvent] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._handled_hwnds: set[int] = set()
        self._recorded_signatures: dict[int, tuple[str, str, str, str]] = {}

    def start(self) -> None:
        if not self.enabled:
            return
        self._thread = threading.Thread(target=self._run, name="hwp-auto-allow-dialogs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def snapshot_events(self) -> list[AutoDialogEvent]:
        with self._lock:
            return list(self.events)

    def click_once_for_test(self, timeout_seconds: float = WINDOW_SCAN_TIMEOUT_SECONDS) -> bool:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self._scan_once():
                return True
            time.sleep(self.poll_interval)
        return False

    def _run(self) -> None:
        while not self._stop_event.wait(self.poll_interval):
            self._scan_once()

    def _scan_once(self) -> bool:
        matched = False
        hwnds: list[int] = []
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(lambda hwnd, _: hwnds.append(hwnd) or True)
        USER32.EnumWindows(enum_proc, 0)
        active_hwnds = set(hwnds)

        for hwnd in hwnds:
            if hwnd in self._handled_hwnds or not USER32.IsWindowVisible(hwnd):
                continue

            pid = _get_window_pid(hwnd)
            if self.allowed_pids is not None and pid not in self.allowed_pids:
                continue

            title_length = USER32.GetWindowTextLengthW(hwnd)
            if title_length <= 0:
                continue

            title_buffer = ctypes.create_unicode_buffer(title_length + 1)
            USER32.GetWindowTextW(hwnd, title_buffer, title_length + 1)
            title_text = title_buffer.value.strip()
            if title_text not in DIALOG_TITLE_WHITELIST:
                continue

            text_parts, allow_button_hwnd, allow_button_text = self._inspect_dialog(hwnd)
            window_text = " ".join(part for part in text_parts if part).strip()
            reason = self._classify_candidate(title_text, window_text, allow_button_text)
            signature = (title_text, window_text, allow_button_text, reason)

            if reason != "match":
                previous = self._recorded_signatures.get(hwnd)
                if window_text and previous != signature:
                    self._record_event(
                        AutoDialogEvent(
                            window_title=title_text,
                            window_text=window_text,
                            button_text=allow_button_text,
                            clicked=False,
                            reason=reason,
                            process_id=pid,
                        )
                    )
                    self._recorded_signatures[hwnd] = signature
                continue

            clicked = False
            if allow_button_hwnd:
                USER32.SendMessageW(allow_button_hwnd, BM_CLICK, 0, 0)
                clicked = True

            self._record_event(
                AutoDialogEvent(
                    window_title=title_text,
                    window_text=window_text,
                    button_text=allow_button_text,
                    clicked=clicked,
                    reason="clicked" if clicked else "allow-button-not-found",
                    process_id=pid,
                )
            )
            self._handled_hwnds.add(hwnd)
            self._recorded_signatures.pop(hwnd, None)
            matched = matched or clicked

        self._handled_hwnds.intersection_update(active_hwnds)
        self._recorded_signatures = {
            hwnd: signature
            for hwnd, signature in self._recorded_signatures.items()
            if hwnd in active_hwnds
        }
        return matched

    def _inspect_dialog(self, hwnd: int) -> tuple[list[str], int | None, str]:
        parts: list[str] = []
        allow_button_hwnd: int | None = None
        allow_button_text = ""
        child_hwnds: list[int] = []
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(lambda child, _: child_hwnds.append(child) or True)
        USER32.EnumChildWindows(hwnd, enum_proc, 0)

        for child in child_hwnds:
            text = _get_window_text(child)
            class_name = _get_class_name(child)
            if text:
                parts.append(text)
            if "BUTTON" in class_name.upper() and text in DIALOG_ALLOW_BUTTONS and allow_button_hwnd is None:
                allow_button_hwnd = child
                allow_button_text = text
        return parts, allow_button_hwnd, allow_button_text

    def _classify_candidate(self, title: str, window_text: str, allow_button_text: str) -> str:
        if title not in DIALOG_TITLE_WHITELIST:
            return "title-mismatch"
        if not all(keyword in window_text for keyword in DIALOG_TEXT_KEYWORDS):
            return "text-mismatch"
        if not allow_button_text:
            return "button-mismatch"
        return "match"

    def _record_event(self, event: AutoDialogEvent) -> None:
        with self._lock:
            self.events.append(event)


COMPAT_DIALOG_TITLES = frozenset({"변환 문서"})

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
VK_Y = 0x59
SCAN_Y = 0x15

COMPAT_POLL_INTERVAL_SECONDS = 0.2
COMPAT_PER_WINDOW_COOLDOWN_SECONDS = 1.0


def _post_continue_key(hwnd: int) -> bool:
    """확인 창에 Y(계속) 키를 보낸다. Win32 버튼이 없어 BM_CLICK이 불가한 창용."""
    down_lparam = 1 | (SCAN_Y << 16)
    up_lparam = down_lparam | 0xC0000000
    ok_down = bool(USER32.PostMessageW(hwnd, WM_KEYDOWN, VK_Y, down_lparam))
    USER32.PostMessageW(hwnd, WM_CHAR, ord("y"), down_lparam)
    ok_up = bool(USER32.PostMessageW(hwnd, WM_KEYUP, VK_Y, up_lparam))
    return ok_down and ok_up


def _compat_window_title(hwnd: int) -> str:
    length = USER32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    USER32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value.strip()


class HwpCompatDialogResponder:
    """DOCX/RTF 등 변환 시 뜨는 '변환 문서(배치가 변경될 수 있습니다. 계속?)' 확인 창에 자동 응답.

    SetMessageBoxMode로 제어되지 않고 Win32 버튼 컨트롤도 없어 BM_CLICK이
    불가하므로 Y 키 메시지로 '계속'을 선택한다. 소유 HWP PID 범위로만 동작하며,
    같은 창에는 쿨다운 간격으로만 응답해 스팸을 막는다.
    """

    def __init__(
        self,
        pids_provider,
        *,
        poll_interval: float = COMPAT_POLL_INTERVAL_SECONDS,
        cooldown: float = COMPAT_PER_WINDOW_COOLDOWN_SECONDS,
    ) -> None:
        self._pids_provider = pids_provider
        self.poll_interval = max(0.02, float(poll_interval))
        self.cooldown = max(0.0, float(cooldown))
        self.response_count = 0
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_response_at: dict[int, float] = {}

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="hwp-compat-dialogs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop_event.wait(self.poll_interval):
            self._poll_once()

    def __enter__(self) -> 'HwpCompatDialogResponder':
        self._stop_event.clear()
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def _poll_once(self) -> int:
        try:
            pids = set(self._pids_provider())
        except Exception:
            return 0
        if not pids:
            return 0
        hwnds: list[int] = []
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(
            lambda hwnd, _: hwnds.append(hwnd) or True
        )
        try:
            USER32.EnumWindows(enum_proc, 0)
        except Exception:
            return 0
        now = time.monotonic()
        responded = 0
        for hwnd in hwnds:
            try:
                if not USER32.IsWindowVisible(hwnd):
                    continue
                if _get_window_pid(hwnd) not in pids:
                    continue
                if _compat_window_title(hwnd) not in COMPAT_DIALOG_TITLES:
                    continue
            except Exception:
                continue
            last = self._last_response_at.get(hwnd, 0.0)
            if now - last < self.cooldown:
                continue
            try:
                if _post_continue_key(hwnd):
                    self._last_response_at[hwnd] = now
                    responded += 1
            except Exception:
                continue
        if responded:
            with self._lock:
                self.response_count += responded
        return responded
