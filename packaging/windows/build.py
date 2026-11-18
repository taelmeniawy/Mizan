"""Build the standalone Windows app and its per-user installer."""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib
from importlib.metadata import distribution
from pathlib import Path


PACKAGING = Path(__file__).resolve().parent
PROJECT = PACKAGING.parents[1]


def copy_licenses(bundle: Path) -> None:
    licenses = bundle / "licenses"
    licenses.mkdir()
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", licenses / "Python.txt")
    for name in ("PySide6-Essentials", "shiboken6", "cryptography", "cffi", "pycparser"):
        package = distribution(name)
        for file in package.files or ():
            if file.parent.name == "licenses":
                target = licenses / name / file.name
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(package.locate_file(file), target)


def write_uninstall_files(bundle: Path, manifest: Path) -> None:
    entries = sorted(bundle.rglob("*"), key=lambda path: (len(path.parts), path), reverse=True)
    with manifest.open("w", encoding="utf-8-sig") as output:
        for entry in entries:
            relative = str(entry.relative_to(bundle)).replace("$", "$$")
            if relative == "Mizan.exe":
                continue  # The uninstaller checks this executable first.
            command = "RMDir" if entry.is_dir() else "Delete"
            output.write(f'    {command} "$INSTDIR\\{relative}"\n')


def compile_installer(
    compiler: str, build: Path, version: str, env: dict[str, str], *, test_install: bool
) -> Path:
    app_id = "MizanPackagingTest" if test_install else "Mizan"
    product_name = "Mizan Packaging Test" if test_install else "Mizan"
    setup = build / f"{app_id}-{version}-win-x64-setup.exe"
    compiler_temp = build / "nsis-temp"
    compiler_temp.mkdir(exist_ok=True)
    subprocess.run(
        [
            compiler,
            "/INPUTCHARSET",
            "UTF8",
            f"/DAppId={app_id}",
            f"/DProductName={product_name}",
            f"/DVersion={version}",
            f"/DFileVersion={version}.0",
            f"/DSetupPath={setup}",
            f"/DBundleDir={build / 'dist' / 'Mizan'}",
            f"/DUninstallFiles={build / 'uninstall-files.nsh'}",
            str(PACKAGING / "installer.nsi"),
        ],
        env=env | {"TEMP": str(compiler_temp), "TMP": str(compiler_temp)},
        check=True,
    )
    return setup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--makensis", default="makensis", help="NSIS 3 compiler")
    parser.add_argument("--test-install", action="store_true", help="Use a separate Windows app identity")
    args = parser.parse_args()
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        parser.error("An x64 Windows Python interpreter is required.")
    compiler = shutil.which(args.makensis)
    if not compiler:
        parser.error("Install NSIS 3 or specify the full path with --makensis.")

    version = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    release = PROJECT / "artifacts" / "windows"
    release.mkdir(parents=True, exist_ok=True)
    build = Path(tempfile.mkdtemp(prefix="build-", dir=release))
    env = os.environ.copy()
    # PyInstaller resolves DLLs through PATH. Exclude unrelated tool runtimes.
    windows = Path(env["SYSTEMROOT"])
    env["PATH"] = os.pathsep.join(
        str(path) for path in (Path(sys.executable).parent, windows / "System32", windows)
    )
    env.setdefault("PYINSTALLER_CONFIG_DIR", str(release / "cache"))
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onedir",
            "--windowed",
            "--noupx",
            "--name",
            "Mizan",
            "--paths",
            str(PROJECT),
            "--add-data",
            f"{PROJECT / 'mizan' / 'assets'}:mizan/assets",
            "--distpath",
            str(build / "dist"),
            "--workpath",
            str(build / "work"),
            "--specpath",
            str(build),
            str(PACKAGING / "launcher.py"),
        ],
        cwd=PROJECT,
        env=env,
        check=True,
    )
    bundle = build / "dist" / "Mizan"
    copy_licenses(bundle)
    write_uninstall_files(bundle, build / "uninstall-files.nsh")
    setup = compile_installer(compiler, build, version, env, test_install=args.test_install)
    filename = setup.name
    with setup.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    setup.replace(release / filename)
    (release / f"{filename}.sha256").write_text(f"{digest}  {filename}\n", encoding="ascii")
    print(f"Installer: {release / filename}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
