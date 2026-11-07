from hashlib import sha256
from typing import Protocol


class Vault(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...


class KeyringVault:
    service = "PromVector Agent"

    @staticmethod
    def backend():
        import keyring

        backend = keyring.get_keyring()
        module = type(backend).__module__
        if not module.startswith(
            (
                "keyring.backends.macOS",
                "keyring.backends.Windows",
                "keyring.backends.SecretService",
                "keyring.backends.kwallet",
                "keyring.backends.libsecret",
            )
        ):
            raise RuntimeError("Unlock a supported operating-system credential store")
        return backend

    def get(self, name: str) -> str | None:
        return self.backend().get_password(self.service, name)

    def set(self, name: str, value: str) -> None:
        self.backend().set_password(self.service, name, value)


def secret_name(*parts: object) -> str:
    return sha256("\0".join(map(str, parts)).encode()).hexdigest()
