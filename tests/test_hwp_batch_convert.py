from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import hwp_batch_convert as cli
from hwp_batch_core import (
    FORMAT_TYPES,
    MockConverter,
    TaskPlanner,
    com_path_candidates,
    create_backup,
    existing_artifact_conflicts,
    is_path_length_risky,
    is_valid_path_name,
    prune_old_backups,
    to_extended_win_path,
)
from hwp_batch_dialogs import AutoAllowDialogWatcher
from hwp_batch_print import is_valid_pdf_file, remove_incomplete_output


def make_sample_tree(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.hwp").write_text("a", encoding="utf-8")
    (src / "b.hwpx").write_text("b", encoding="utf-8")
    (src / "sub" / "c.hwp").write_text("c", encoding="utf-8")
    return src


def test_preserve_source_root_and_source_root_field(tmp_path: Path) -> None:
    src1 = tmp_path / "src1"
    src2 = tmp_path / "src2"
    (src1 / "nested").mkdir(parents=True)
    (src2 / "deep").mkdir(parents=True)
    (src1 / "nested" / "a.hwp").write_text("a", encoding="utf-8")
    (src2 / "deep" / "b.hwp").write_text("b", encoding="utf-8")
    out = tmp_path / "out"

    args = cli.parse_args(
        [
            str(src1),
            str(src2),
            "--format",
            "PDF",
            "--output-dir",
            str(out),
            "--preserve-source-root",
            "--plan-only",
        ]
    )
    summary = cli.run_conversion(args)
    payload = summary.to_json_dict()

    outputs = {Path(task["output_file"]).relative_to(out).as_posix() for task in payload["tasks"]}
    source_roots = {Path(task["source_root"]).name for task in payload["tasks"]}

    assert outputs == {"src1/nested/a.pdf", "src2/deep/b.pdf"}
    assert source_roots == {"src1", "src2"}


def test_unsupported_single_file_is_error(tmp_path: Path) -> None:
    note = tmp_path / "note.txt"
    note.write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="지원하지 않는 입력 파일"):
        cli.run_conversion(
            cli.parse_args(
                [
                    str(note),
                    "--format",
                    "PDF",
                    "--output-dir",
                    str(tmp_path / "out"),
                ]
            )
        )


def test_allow_empty_returns_summary(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    summary = cli.run_conversion(
        cli.parse_args(
            [
                str(empty),
                "--format",
                "PDF",
                "--output-dir",
                str(tmp_path / "out"),
                "--allow-empty",
                "--json",
            ]
        )
    )

    assert summary.mode == "real"
    assert summary.tasks == []
    assert "변환 대상이 없습니다." in summary.warnings


def test_same_location_and_output_dir_are_mutually_exclusive(tmp_path: Path) -> None:
    src = make_sample_tree(tmp_path)
    with pytest.raises(SystemExit):
        cli.parse_args(
            [
                str(src),
                "--format",
                "PDF",
                "--same-location",
                "--output-dir",
                str(tmp_path / "out"),
            ]
        )


def test_failed_tasks_return_nonzero_without_partial_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src = make_sample_tree(tmp_path)

    def fake_convert(self, input_path, output_path, format_type="PDF"):
        if Path(input_path).name == "a.hwp":
            return False, "boom"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("ok", encoding="utf-8")
        return True, None

    monkeypatch.setattr(cli.MockConverter, "convert_file", fake_convert)

    exit_code = cli.main(
        [
            str(src),
            "--format",
            "PDF",
            "--output-dir",
            str(tmp_path / "out"),
            "--mode",
            "mock",
            "--json",
        ]
    )
    capsys.readouterr()

    assert exit_code == 1


def test_allow_partial_success_keeps_zero_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src = make_sample_tree(tmp_path)

    def fake_convert(self, input_path, output_path, format_type="PDF"):
        if Path(input_path).name == "a.hwp":
            return False, "boom"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("ok", encoding="utf-8")
        return True, None

    monkeypatch.setattr(cli.MockConverter, "convert_file", fake_convert)

    exit_code = cli.main(
        [
            str(src),
            "--format",
            "PDF",
            "--output-dir",
            str(tmp_path / "out"),
            "--mode",
            "mock",
            "--json",
            "--allow-partial-success",
        ]
    )
    capsys.readouterr()

    assert exit_code == 0


def test_report_json_written_on_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src = make_sample_tree(tmp_path)
    report = tmp_path / "report.json"

    def boom(_args):
        raise RuntimeError("planned failure")

    monkeypatch.setattr(cli, "run_conversion", boom)

    exit_code = cli.main(
        [
            str(src),
            "--format",
            "PDF",
            "--output-dir",
            str(tmp_path / "out"),
            "--json",
            "--report-json",
            str(report),
        ]
    )
    capsys.readouterr()

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["error"] == "planned failure"


def test_backup_creation_and_prune(tmp_path: Path) -> None:
    sample_file = tmp_path / "doc.hwp"
    sample_file.write_text("original content", encoding="utf-8")

    # 1. 백업 생성
    b1 = create_backup(sample_file, max_files=2)
    assert b1.exists()
    assert b1.parent.name == "backup"
    assert b1.name.startswith("doc_")

    b2 = create_backup(sample_file, max_files=2)
    assert b2.exists()

    b3 = create_backup(sample_file, max_files=2)
    assert b3.exists()

    # max_files=2 이므로 backup 폴더 내 파일은 최대 2개만 남아야 함
    backups = list((tmp_path / "backup").glob("doc_*.hwp"))
    assert len(backups) == 2
    assert b3 in backups


def test_auxiliary_artifact_conflicts(tmp_path: Path) -> None:
    out_html = tmp_path / "document.html"
    out_files_dir = tmp_path / "document.files"
    out_files_dir.mkdir()
    (out_files_dir / "image1.png").write_text("img", encoding="utf-8")

    # HTML 보조 디렉터리 충돌 감지 확인
    conflicts = existing_artifact_conflicts(out_html, "HTML")
    assert any(c == out_files_dir for c in conflicts)

    # TaskPlanner가 충돌 시 새 번호 할당하는지 확인
    planner = TaskPlanner()
    src = tmp_path / "document.hwp"
    src.write_text("data", encoding="utf-8")

    plan = planner.build_tasks(
        sources=[str(src)],
        format_type="HTML",
        include_sub=False,
        same_location=False,
        output_path=str(tmp_path),
        allow_empty=False,
        preserve_source_root=False,
    )
    renamed = planner.resolve_output_conflicts(plan.tasks, overwrite=False, format_type="HTML")
    assert renamed == 1
    assert plan.tasks[0].output_file.name == "document (1).html"


def test_pdf_magic_and_cleanup(tmp_path: Path) -> None:
    valid_pdf = tmp_path / "valid.pdf"
    valid_pdf.write_bytes(b"%PDF-1.4 header and some dummy content here to make it over 64 bytes long for testing validity\n%%EOF")
    assert is_valid_pdf_file(valid_pdf) is True

    invalid_pdf = tmp_path / "invalid.pdf"
    invalid_pdf.write_bytes(b"NOT A PDF")
    assert is_valid_pdf_file(invalid_pdf) is False

    # 불완전 파일 정리
    remove_incomplete_output(invalid_pdf)
    assert not invalid_pdf.exists()


def test_path_utilities() -> None:
    assert is_valid_path_name("C:\\Valid\\Path\\File.hwp") is True
    assert is_valid_path_name("C:\\Invalid|Path<>.hwp") is False

    extended = to_extended_win_path("C:\\docs\\test.hwp")
    assert extended.startswith("\\\\?\\")

    cands = com_path_candidates("C:\\docs\\test.hwp")
    assert len(cands) >= 1
    assert "C:\\docs\\test.hwp" in cands[0] or "C:\\docs\\test.hwp" in cands[-1]

    assert is_path_length_risky("A" * 250) is True
    assert is_path_length_risky("A" * 50) is False


def test_mock_conversion_audit_metadata_and_backup(tmp_path: Path) -> None:
    src = make_sample_tree(tmp_path)
    out = tmp_path / "out"

    args = cli.parse_args(
        [
            str(src),
            "--format",
            "PDF",
            "--output-dir",
            str(out),
            "--mode",
            "mock",
            "--backup",
            "--retry-count",
            "2",
            "--json",
        ]
    )
    summary = cli.run_conversion(args)
    payload = summary.to_json_dict()

    assert payload["summary"]["success_count"] == 3
    assert payload["summary"]["backup_enabled"] is True
    assert payload["summary"]["backup_count"] == 3
    assert payload["summary"]["total_created_files"] == 3

    for task in payload["tasks"]:
        assert task["status"] == "성공"
        assert task["created_files"]
        assert task["output_size"] is not None
        assert task["output_mtime"] is not None
        assert task["save_format"] == "PDF"
        assert task["export_method"] == "MockSave"
        assert task["backup_file"] is not None
        assert Path(task["backup_file"]).exists()


def test_retry_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sample_file = tmp_path / "retry_doc.hwp"
    sample_file.write_text("data", encoding="utf-8")
    out = tmp_path / "out"

    attempts = 0

    def fail_then_succeed(self, input_path, output_path, format_type="PDF"):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return False, "temporary glitch"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"%PDF-1.4 mock stream\n%%EOF")
        return True, None

    monkeypatch.setattr(cli.MockConverter, "convert_file", fail_then_succeed)

    args = cli.parse_args(
        [
            str(sample_file),
            "--format",
            "PDF",
            "--output-dir",
            str(out),
            "--mode",
            "mock",
            "--retry-count",
            "2",
        ]
    )
    summary = cli.run_conversion(args)
    payload = summary.to_json_dict()

    assert payload["summary"]["success_count"] == 1
    assert attempts == 2
    assert payload["tasks"][0]["retry_count"] == 1


def _launch_dialog_process(*, delayed_button: bool) -> subprocess.Popen[str]:
    if delayed_button:
        script = r"""
Add-Type -AssemblyName System.Windows.Forms
$form = New-Object System.Windows.Forms.Form
$form.Text = '한글'
$form.Width = 420
$form.Height = 170
$form.StartPosition = 'CenterScreen'
$label = New-Object System.Windows.Forms.Label
$label.AutoSize = $true
$label.Left = 20
$label.Top = 20
$label.Text = '한글 문서에 접근하려는 시도를 허용하시겠습니까?'
$form.Controls.Add($label)
$buttonTimer = New-Object System.Windows.Forms.Timer
$buttonTimer.Interval = 700
$buttonTimer.Add_Tick({
    $button = New-Object System.Windows.Forms.Button
    $button.Text = '모두 허용'
    $button.Left = 20
    $button.Top = 70
    $button.Width = 100
    $button.Add_Click({ $form.Close() })
    $form.Controls.Add($button)
    $buttonTimer.Stop()
})
$closeTimer = New-Object System.Windows.Forms.Timer
$closeTimer.Interval = 4000
$closeTimer.Add_Tick({
    $closeTimer.Stop()
    $form.Close()
})
$buttonTimer.Start()
$closeTimer.Start()
[void]$form.ShowDialog()
"""
    else:
        script = r"""
Add-Type -AssemblyName System.Windows.Forms
$form = New-Object System.Windows.Forms.Form
$form.Text = '한글'
$form.Width = 420
$form.Height = 170
$form.StartPosition = 'CenterScreen'
$label = New-Object System.Windows.Forms.Label
$label.AutoSize = $true
$label.Left = 20
$label.Top = 20
$label.Text = '한글 문서에 접근하려는 시도를 허용하시겠습니까?'
$form.Controls.Add($label)
$button = New-Object System.Windows.Forms.Button
$button.Text = '모두 허용'
$button.Left = 20
$button.Top = 70
$button.Width = 100
$button.Add_Click({ $form.Close() })
$form.Controls.Add($button)
$closeTimer = New-Object System.Windows.Forms.Timer
$closeTimer.Interval = 2000
$closeTimer.Add_Tick({
    $closeTimer.Stop()
    $form.Close()
})
$closeTimer.Start()
[void]$form.ShowDialog()
"""
    return subprocess.Popen(["powershell", "-NoProfile", "-STA", "-Command", script], text=True)


def test_dialog_watcher_retries_until_button_appears() -> None:
    proc = _launch_dialog_process(delayed_button=True)
    watcher = AutoAllowDialogWatcher(enabled=True, allowed_pids={proc.pid}, poll_interval=0.1)
    try:
        assert watcher.click_once_for_test(timeout_seconds=3.5) is True
        proc.wait(timeout=3)
        events = watcher.snapshot_events()
        assert any(event.reason == "button-mismatch" for event in events)
        assert any(event.clicked for event in events)
    finally:
        if proc.poll() is None:
            proc.kill()


def test_dialog_watcher_respects_allowed_pid() -> None:
    proc = _launch_dialog_process(delayed_button=False)
    watcher = AutoAllowDialogWatcher(enabled=True, allowed_pids={proc.pid + 100000}, poll_interval=0.1)
    try:
        assert watcher.click_once_for_test(timeout_seconds=1.5) is False
        proc.wait(timeout=3)
    finally:
        if proc.poll() is None:
            proc.kill()
