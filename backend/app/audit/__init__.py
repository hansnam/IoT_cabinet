"""
Audit Package (Thành viên 3 chủ trì)
Ghi nhận kiểm toán các hành động nhạy cảm của hệ thống (audit_logs),
bảo mật tuyệt đối, không ghi token, password hoặc secret.
"""

from backend.app.audit.service import AuditService

__all__ = ["AuditService"]
