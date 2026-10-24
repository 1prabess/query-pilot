from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import LargeBinary, TypeDecorator

from app.core.config import get_settings
from app.core.errors import DecryptionError

settings = get_settings()


def _fernet() -> Fernet:
    return Fernet(settings.CREDENTIAL_ENCRYPTION_KEY.get_secret_value().encode())


def encrypt(plain_text: str) -> bytes:
    return _fernet().encrypt(plain_text.encode())


def decrypt(encrypted_text: str) -> str:
    return _fernet().decrypt(encrypted_text).decode()


class EncryptedString(TypeDecorator):
    """A text column that is encrypted on the way in and decrypted on the way out.

    A TypeDecorator rather than encrypting by hand at every call site, so that
    *every* write to this column is encrypted -- including the one somebody adds
    in a hurry at the end of the project. Encryption that depends on the author
    remembering is encryption you will forget.

    The database never sees plaintext. It sees a 100-character blob in a `bytea`
    column and cannot index, compare or search it, which is correct: you must
    able to decrypt a customer's password, and you must not be able to query by
    it.
    """

    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return encrypt(str(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            return decrypt(value)
        except InvalidToken:
            raise DecryptionError(
                "Stored credential could not be decrypted. The CREDENTIAL_ENCRYPTION_KEY "
                "has probably been rotated without a re-encryption migration."
            ) from None
