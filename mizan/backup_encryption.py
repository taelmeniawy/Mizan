"""Versioned, authenticated envelope for a verified Mizan backup directory."""

import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


MAGIC = b"MIZAN-BACKUP\x00\x01"
BLOCK = 1024 * 1024


class EncryptionError(ValueError):
    pass


def generate_key() -> bytes:
    return os.urandom(32)


def _aes(key: bytes | None):
    if not isinstance(key, bytes) or len(key) != 32:
        raise EncryptionError("a 32-byte backup recovery key is required")
    return algorithms.AES(key)


def seal(directory: Path, output: Path, key: bytes) -> None:
    nonce = os.urandom(12)
    header = MAGIC + nonce
    cipher = Cipher(_aes(key), modes.GCM(nonce)).encryptor()
    cipher.authenticate_additional_data(header)
    with tempfile.TemporaryFile() as archive:
        with zipfile.ZipFile(archive, "w") as package:
            for path in directory.rglob("*"):
                if path.is_file():
                    package.write(path, path.relative_to(directory).as_posix())
        archive.seek(0)
        with output.open("xb") as encrypted:
            encrypted.write(header)
            while chunk := archive.read(BLOCK):
                encrypted.write(cipher.update(chunk))
            encrypted.write(cipher.finalize())
            encrypted.write(cipher.tag)


def unseal(source: Path, directory: Path, key: bytes | None) -> None:
    algorithm = _aes(key)
    with source.open("rb") as encrypted, tempfile.TemporaryFile() as archive:
        header = encrypted.read(len(MAGIC) + 12)
        if len(header) != len(MAGIC) + 12 or not header.startswith(MAGIC):
            raise EncryptionError("unsupported encrypted backup format")
        remaining = encrypted.seek(0, 2) - len(header) - 16
        if remaining <= 0:
            raise EncryptionError("incomplete encrypted backup")
        encrypted.seek(-16, 2)
        cipher = Cipher(algorithm, modes.GCM(header[-12:], encrypted.read(16))).decryptor()
        cipher.authenticate_additional_data(header)
        encrypted.seek(len(header))
        while remaining:
            chunk = encrypted.read(min(BLOCK, remaining))
            if not chunk:
                raise EncryptionError("incomplete encrypted backup")
            archive.write(cipher.update(chunk))
            remaining -= len(chunk)
        try:
            archive.write(cipher.finalize())
        except InvalidTag as exc:
            raise EncryptionError("incorrect recovery key or damaged backup") from exc
        archive.seek(0)
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                name = member.filename
                parts = PurePosixPath(name).parts
                if (not parts or name != "/".join(parts) or "\\" in name or ":" in name
                        or PurePosixPath(name).is_absolute() or ".." in parts
                        or any(PureWindowsPath(part).is_reserved() or part.endswith((".", " ")) for part in parts)
                        or member.compress_type != zipfile.ZIP_STORED):
                    raise EncryptionError("invalid encrypted backup member")
                target = directory.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(member) as data, target.open("xb") as output:
                    shutil.copyfileobj(data, output, BLOCK)
