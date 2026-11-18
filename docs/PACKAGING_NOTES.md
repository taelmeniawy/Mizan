# Windows packaging

The Phase 10 installer is a Windows x64 build. It installs for the current
user under `%LOCALAPPDATA%\Programs\Mizan`, adds a Start menu shortcut, and
registers Mizan in Installed apps. Python and Qt are included.

## Install and update

1. Run `Mizan-<version>-win-x64-setup.exe` and open **Mizan** from Start.
2. On a new profile, complete the first-run store and Owner setup. The default
   data directory is `%LOCALAPPDATA%\Mizan`.
3. Before an update, create a backup in Mizan, close the app, then run the new
   installer. The installer replaces the previous program files and reuses
   the existing data on the next launch.

Remove Mizan from Windows **Installed apps**. The uninstaller removes the
program and shortcut while preserving the database, documents and backups.
If Windows blocks the shortcut or registration, open `Mizan.exe` or
`Uninstall.exe` directly in `%LOCALAPPDATA%\Programs\Mizan`.
Keep the previous installer with the backup when testing a version that
changes the database schema.

## Build on Windows x64

Use standard x64 Python 3.12 and the [NSIS 3 compiler](https://nsis.sourceforge.io/Download).
From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[windows-build]"
.\.venv\Scripts\python.exe packaging/windows/build.py --makensis 'C:\path\to\makensis.exe'
```

The installer and its SHA-256 checksum are written to `artifacts/windows/`.
The standalone bundle and PyInstaller analysis are in that build's
`build-*/` directory. The installer is currently a local, unsigned build.

For the installer smoke test, add `--test-install` to the build command to
use a separate app registration. Run the test in an interactive Windows
session so it can detect the packaged Qt window:

```powershell
.\.venv\Scripts\python.exe packaging/windows/smoke.py artifacts/windows/MizanPackagingTest-0.1.0-win-x64-setup.exe
```

The test installs to a temporary directory within the artifacts folder,
launches the app with a fresh data profile, replaces the installation, then
removes it and compares the preserved test data.
Use `--files-only` to test those operations in an environment that blocks
registration in Windows. Run the default test on the acceptance machine.

## Bundled assets

The approved Arabic font is part of the `mizan` package data:

```text
mizan/assets/fonts/NotoNaskhArabic/
  NotoNaskhArabic-Regular.ttf
  NotoNaskhArabic-Bold.ttf
  LICENCE
  SOURCE.md
```

- Source/version/hashes: [mizan/assets/fonts/NotoNaskhArabic/SOURCE.md](../mizan/assets/fonts/NotoNaskhArabic/SOURCE.md)
- License: SIL Open Font License 1.1, copied from the pinned upstream commit.
- Only Regular and Bold are bundled; no Medium/SemiBold/variable/OTF/other assets.

## Python package

`pyproject.toml` includes:

```toml
[tool.setuptools]
include-package-data = true

[tool.setuptools.package-data]
mizan = ["assets/fonts/NotoNaskhArabic/*"]
```

`MANIFEST.in` also includes `mizan/assets/**` for source distributions. The
renderer loads the faces with `importlib.resources.files("mizan")` and
`QFontDatabase.addApplicationFont`, not from the current working directory.

## Application entry point

The desktop shell is started with:

```powershell
.\.venv\Scripts\python.exe -m mizan
# or, after installation, the GUI script declared in pyproject.toml:
#   [project.gui-scripts]
#   mizan = "mizan.app:main"
```

`--data-dir` selects the data root (database, documents, backups, logs). The
default is `%LOCALAPPDATA%\Mizan`; `MIZAN_DATA_DIR` overrides it.

## PyInstaller

The build passes `mizan/assets` to PyInstaller at the same relative path so
that `importlib.resources` can load the Arabic font with its `LICENCE` and
`SOURCE.md` files. The standalone directory keeps the Qt runtime, including
the Windows platform plugin, alongside the application.

## Remaining packaging work

- Run the installer acceptance checks on a clean Windows x64 machine.
- Complete code signing and the third-party distribution notice review before
  publishing a release.
