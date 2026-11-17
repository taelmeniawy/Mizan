# Packaging notes — bundled Noto Naskh Arabic

**Status:** Phase 7 font packaging notes; full application packaging/signing remains Phase 10.

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

Verification performed: install the project into a temporary target and load
the font from that installed package:

```powershell
.\\.venv\\Scripts\\python.exe -m pip install --no-deps --target .\tmp_pkg .
# run from tmp_pkg (or with tmp_pkg on PYTHONPATH) so the source tree cannot shadow it
.\\.venv\\Scripts\\python.exe -c "from mizan.rendering import load_bundled_fonts; print(load_bundled_fonts())"
```

## PyInstaller

When the Windows executable is built in Phase 10, include the package data
explicitly; do not rely on the development tree being present:

```text
--add-data "mizan/assets;mizan/assets"
```

or in a `.spec` file:

```python
datas=[("mizan/assets", "mizan/assets")]
```

The `LICENCE` and `SOURCE.md` files must ship with the executable's bundled
assets. The font is loaded from `importlib.resources`, so the PyInstaller data
destination must keep the `mizan/assets/...` relative layout.

## Remaining packaging work

- Actual PyInstaller executable build, clean-machine install, and code signing
  are Phase 10 work and are not claimed here.
- The renderer uses the bundled font for Arabic; Qt may use a system fallback for
  Latin characters/codes/punctuation that Noto Naskh Arabic does not contain.
