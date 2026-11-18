"""Exercise installation, frozen app startup, replacement, and uninstallation."""

from __future__ import annotations

import argparse
from contextlib import closing
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import winreg


REGISTRY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\MizanPackagingTest"
USER32 = ctypes.WinDLL("user32")
ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
USER32.EnumWindows.argtypes = [ENUM_WINDOWS_PROC, wintypes.LPARAM]
USER32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
USER32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
USER32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


def install_location() -> Path | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY) as key:
            return Path(winreg.QueryValueEx(key, "InstallLocation")[0])
    except FileNotFoundError:
        return None


def main_window(pid: int) -> int | None:
    windows = []

    @ENUM_WINDOWS_PROC
    def record(hwnd: int, _lparam: int) -> bool:
        owner = wintypes.DWORD()
        USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            title = ctypes.create_unicode_buffer(256)
            USER32.GetWindowTextW(hwnd, title, len(title))
            if title.value.startswith("ميزان — إدارة المتجر"):
                windows.append(hwnd)
        return True

    USER32.EnumWindows(record, 0)
    return windows[0] if windows else None


def launch(install: Path, profile: Path, work: Path) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("PYTHON", "QT_", "MIZAN_"))}
    windows = Path(env["SYSTEMROOT"])
    env["PATH"] = os.pathsep.join((str(windows / "System32"), str(windows)))
    env["LOCALAPPDATA"] = str(profile)
    with subprocess.Popen([install / "Mizan.exe"], cwd=work, env=env) as process:
        try:
            deadline = time.monotonic() + 45
            while process.poll() is None and time.monotonic() < deadline:
                hwnd = main_window(process.pid)
                if hwnd:
                    USER32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
                    if process.wait(timeout=15) == 0:
                        return
                    break
                time.sleep(0.2)
            raise RuntimeError(f"Mizan's main window failed to start and exit normally (exit={process.poll()}).")
        finally:
            if process.poll() is None:
                process.terminate()


def uninstall(install: Path, env: dict[str, str]) -> None:
    subprocess.run([install / "Uninstall.exe", "/S"], env=env, check=True, timeout=60)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if install_location() is None and not (install / "Uninstall.exe").exists():
            return
        time.sleep(0.2)
    raise RuntimeError("Uninstall did not finish.")


def verify_bundle(install: Path) -> None:
    internal = install / "_internal"
    assert (internal / "PySide6" / "plugins" / "platforms" / "qwindows.dll").is_file()
    assert (internal / "Qt6PrintSupport.dll").is_file()
    fonts = Path("mizan/assets/fonts/NotoNaskhArabic")
    for name in ("NotoNaskhArabic-Regular.ttf", "NotoNaskhArabic-Bold.ttf", "LICENCE", "SOURCE.md"):
        source = Path(__file__).resolve().parents[2] / fonts / name
        assert (internal / fonts / name).read_bytes() == source.read_bytes()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installer", type=Path)
    parser.add_argument("--files-only", action="store_true", help="Allow Windows integration to be unavailable")
    args = parser.parse_args()
    setup = args.installer.resolve(strict=True)
    if not setup.name.startswith("MizanPackagingTest-") or install_location() is not None:
        parser.error("Use a test installer from build.py --test-install and remove any previous test installation.")

    work = Path(tempfile.mkdtemp(prefix="smoke-", dir=setup.parent))
    install = work / "Application Files"
    profile = work / "Test Profile"
    profile.mkdir()
    temp = work / "temp"
    temp.mkdir()
    windows = Path(os.environ["SYSTEMROOT"])
    installer_env = os.environ | {
        "PATH": os.pathsep.join((str(windows / "System32"), str(windows))),
        "TEMP": str(temp),
        "TMP": str(temp),
    }
    data = profile / "Mizan"
    database = data / "mizan.db"

    # NSIS requires an unquoted /D value as the final argument, including spaces.
    install_command = f'"{setup}" /S /D={install}'
    try:
        subprocess.run(install_command, env=installer_env, check=True, timeout=120)
        expected_locations = {install, None} if args.files_only else {install}
        assert install_location() in expected_locations
        verify_bundle(install)
        launch(install, profile, work)
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] > 0

        expected = {database: hashlib.sha256(database.read_bytes()).digest()}
        for folder in ("documents", "backups"):
            file = data / folder / "test-marker.txt"
            file.write_bytes(folder.encode("ascii"))
            expected[file] = hashlib.sha256(file.read_bytes()).digest()
        unrelated = install / "keep.txt"
        unrelated.write_text("not a program file", encoding="ascii")

        subprocess.run(install_command, env=installer_env, check=True, timeout=120)
        assert install_location() in expected_locations
        verify_bundle(install)
        assert all(hashlib.sha256(file.read_bytes()).digest() == digest for file, digest in expected.items())
        launch(install, profile, work)
        expected[database] = hashlib.sha256(database.read_bytes()).digest()
    finally:
        if (install / "Uninstall.exe").is_file():
            uninstall(install, installer_env)

    assert list(install.iterdir()) == [unrelated]
    assert all(hashlib.sha256(file.read_bytes()).digest() == digest for file, digest in expected.items())
    print(f"Passed installation, GUI, replacement, data retention and removal checks in {work}")


if __name__ == "__main__":
    main()
