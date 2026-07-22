from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from ..config import DATA_ROOT


class SecretStore:
    """Local encrypted secret store; ciphertext is kept in SQLite, key in a local protected file."""

    def __init__(self) -> None:
        self.key_path = DATA_ROOT / ".secret.key"

    def _fernet(self) -> Fernet:
        if not self.key_path.exists():
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            self.key_path.write_bytes(Fernet.generate_key())
        return Fernet(self.key_path.read_bytes())

    def encrypt(self, value: str) -> str:
        if not value:
            return ""
        return self._fernet().encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        if not value:
            return ""
        try:
            return self._fernet().decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError("模型 API 密钥无法解密") from exc


secret_store = SecretStore()
