"""
Password Primitives Module
Chịu trách nhiệm xác thực định dạng mật khẩu, băm mật khẩu bằng Argon2id và kiểm tra rehash.
Tuân thủ nghiêm ngặt nguyên tắc:
- Mật khẩu mới có độ dài 12–128 ký tự; không trim, không cắt ngắn, không ghi log hoặc echo mật khẩu.
- Xác minh sai hoặc chuỗi hash không hợp lệ (malformed) phải trả về kết quả an toàn (False).
- Chỉ bắt các ngoại lệ mật mã đã biết của argon2 (VerifyMismatchError, InvalidHashError, VerificationError).
  Không bắt Exception chung để tránh nuốt các lỗi hệ thống ngoài dự kiến.
- Helper KHÔNG tự ghi DB; caller bước login/change-password chịu trách nhiệm cập nhật DB trong transaction.
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError, VerificationError

from backend.app.extensions import get_password_hasher
from backend.app.auth.messages import AuthMessage

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128

def validate_password_complexity(password: str) -> None:
    """Kiểm tra độ dài mật khẩu mới trong khoảng 12-128 ký tự.
    Tuyệt đối không trim khoảng trắng, không cắt ngắn và không echo mật khẩu ra ngoài thông báo lỗi.
    """
    if not isinstance(password, str):
        raise ValueError(AuthMessage.PASSWORD_INVALID_TYPE)
    if not (MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH):
        raise ValueError(AuthMessage.PASSWORD_LENGTH_INVALID)

def hash_password(password: str, hasher: PasswordHasher | None = None) -> str:
    """Băm mật khẩu sử dụng Argon2id explicit.
    - password: Chuỗi mật khẩu thô từ người dùng.
    - hasher: Đối tượng PasswordHasher tùy chọn (nếu None sẽ dùng cấu hình chung từ get_password_hasher).
    Trả về chuỗi hash định dạng Argon2id tiêu chuẩn (ví dụ bắt đầu bằng '$argon2id$v=19$m=').
    """
    validate_password_complexity(password)
    h = hasher or get_password_hasher()
    return h.hash(password)

def verify_password(
    password: str,
    password_hash: str,
    hasher: PasswordHasher | None = None
) -> bool:
    """Xác minh mật khẩu với chuỗi hash Argon2id.
    - Trả về True nếu mật khẩu trùng khớp với hash.
    - Trả về False nếu mật khẩu không đúng, hoặc chuỗi hash bị rỗng, hỏng cấu trúc (malformed).
    - Chỉ bắt các ngoại lệ mật mã đã biết từ thư viện argon2 (VerifyMismatchError, InvalidHashError, VerificationError).
      Các ngoại lệ hệ thống ngoài dự kiến (như MemoryError, OS/CFFI error) không bị nuốt.
    - Không thực hiện bất kỳ thao tác ghi DB nào.
    """
    if not isinstance(password, str) or not password:
        return False
    if not isinstance(password_hash, str) or not password_hash:
        return False

    h = hasher or get_password_hasher()
    try:
        return h.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        return False

def needs_rehash(
    password_hash: str,
    hasher: PasswordHasher | None = None
) -> bool:
    """Kiểm tra xem chuỗi hash hiện tại có cần được băm lại theo cấu hình Argon2id mới hay không.
    Trả về True nếu tham số hash (memory_cost, time_cost, parallelism) khác với cấu hình của hasher.
    """
    if not isinstance(password_hash, str) or not password_hash:
        return False

    h = hasher or get_password_hasher()
    try:
        return h.check_needs_rehash(password_hash)
    except (InvalidHashError, VerificationError):
        return False

def verify_and_rehash(
    password: str,
    password_hash: str,
    hasher: PasswordHasher | None = None
) -> tuple[bool, str | None]:
    """Xác minh mật khẩu và đồng thời kiểm tra nhu cầu băm lại (rehash).
    Trả về bộ đôi (is_valid, new_hash_if_rehash_needed):
    - (False, None): Nếu mật khẩu sai hoặc chuỗi hash malformed.
    - (True, None): Nếu mật khẩu đúng và hash vẫn thỏa mãn cấu hình hiện hành.
    - (True, new_hash): Nếu mật khẩu đúng nhưng hash cũ cần nâng cấp tham số bảo mật.
    Caller bước login chịu trách nhiệm nhận new_hash và cập nhật DB trong cùng database transaction.
    """
    h = hasher or get_password_hasher()
    if not verify_password(password, password_hash, hasher=h):
        return False, None
    if needs_rehash(password_hash, hasher=h):
        new_hash = hash_password(password, hasher=h)
        return True, new_hash

    return True, None