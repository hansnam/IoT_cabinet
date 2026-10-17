"""
TV3-01B: Test Schema Validation & Idempotent Bootstrap Seed
Contract: V1.3 Mục 5, 6, 8.5 & TV3 Kế hoạch triển khai
Tổng cộng: 12 hàm test, tương đương 21 ca test sau parametrize.
"""

import os
import pytest
from sqlalchemy import inspect, text
from backend.app.models import (
    Role, Permission, RolePermission, User, UserRole,
    Device, DeviceAccess, DeviceCredential, DeviceConfig, DeviceShadow,
    AlertRule,
)
from backend.seed import run_seed

CORE_27_TABLES = frozenset({
    # IAM (8 bảng)
    "locations", "permissions", "roles", "users", "auth_sessions",
    "device_groups", "devices", "login_attempts",
    # Mối quan hệ & Thiết bị (8 bảng)
    "role_permissions", "user_roles", "alert_rules", "audit_logs",
    "commands", "device_access", "device_configs", "device_credentials",
    # Dữ liệu & Lệnh (6 bảng)
    "device_events", "device_group_members", "device_shadows", "refresh_tokens",
    "telemetry", "alerts",
    # Hoạt động & Cảnh báo (5 bảng)
    "command_attempts", "device_operations", "outbox_messages",
    "user_activity_logs", "alert_events",
})


@pytest.fixture(autouse=True)
def _clean_seed_env(monkeypatch):
    """Thiết lập môi trường mẫu an toàn cho seed test."""
    monkeypatch.setenv("ADMIN_BOOTSTRAP_USERNAME", "admin_demo")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "AdminSecure2026!xyz")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_FULLNAME", "System Administrator")
    monkeypatch.setenv("OPERATOR_BOOTSTRAP_USERNAME", "operator_demo")
    monkeypatch.setenv("OPERATOR_BOOTSTRAP_PASSWORD", "OperatorSecure2026!xyz")
    monkeypatch.setenv("OPERATOR_BOOTSTRAP_FULLNAME", "Network Cabinet Operator")
    monkeypatch.setenv("VIEWER_BOOTSTRAP_USERNAME", "viewer_demo")
    monkeypatch.setenv("VIEWER_BOOTSTRAP_PASSWORD", "ViewerSecure2026!xyz")
    monkeypatch.setenv("VIEWER_BOOTSTRAP_FULLNAME", "Auditor Viewer")


def test_schema_contains_27_core_tables_and_alembic_revision(app):
    """(Ca 1) Kiểm tra đúng 27 bảng CORE (không kể alembic_version) và revision chuẩn fcb8c798de10."""
    with app.app_context():
        inspector = inspect(app.extensions["migrate"].db.engine)
        tables = set(inspector.get_table_names())
        assert "alembic_version" in tables, "Thiếu bảng alembic_version"
        business_tables = tables - {"alembic_version"}
        assert business_tables == CORE_27_TABLES, f"Schema không khớp 27 bảng: {business_tables ^ CORE_27_TABLES}"

        rev = app.extensions["migrate"].db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar()
        assert rev == "fcb8c798de10", f"Alembic revision không khớp: {rev}"


def test_seed_runs_successfully_and_is_idempotent(app, transaction_session):
    """(Ca 2) Seed lần đầu thành công; seed lần hai idempotent không lỗi, không sinh bản ghi trùng."""
    run_seed(app, transaction_session)

    # Đếm số lượng ban đầu sau seed 1
    c_roles = transaction_session.query(Role).count()
    c_perms = transaction_session.query(Permission).count()
    c_users = transaction_session.query(User).count()
    c_devs = transaction_session.query(Device).count()
    c_rules = transaction_session.query(AlertRule).count()
    c_grants = transaction_session.query(DeviceAccess).count()

    assert c_roles == 3
    assert c_perms == 31
    assert c_users >= 3
    assert c_devs >= 2
    assert c_rules >= 4
    assert c_grants == 2

    # Chạy lần 2 trên cùng transaction context
    run_seed(app, transaction_session)

    # Đảm bảo số lượng giữ nguyên tuyệt đối
    assert transaction_session.query(Role).count() == c_roles
    assert transaction_session.query(Permission).count() == c_perms
    assert transaction_session.query(User).count() == c_users
    assert transaction_session.query(Device).count() == c_devs
    assert transaction_session.query(AlertRule).count() == c_rules
    assert transaction_session.query(DeviceAccess).count() == c_grants


def test_seed_does_not_modify_existing_user_password_or_role(app, transaction_session, monkeypatch):
    """(Ca 3) Chạy lại seed khi user đã tồn tại không được đổi password hash hay gán đè role."""
    run_seed(app, transaction_session)
    admin = transaction_session.query(User).filter_by(username="admin_demo").one()
    initial_hash = admin.password_hash
    initial_role_count = transaction_session.query(UserRole).filter_by(user_id=admin.id).count()
    assert initial_role_count == 1

    # Đổi biến môi trường mật khẩu
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "AnotherDifferentPassword2026!xyz")
    run_seed(app, transaction_session)

    re_admin = transaction_session.query(User).filter_by(username="admin_demo").one()
    assert re_admin.password_hash == initial_hash
    assert transaction_session.query(UserRole).filter_by(user_id=admin.id).count() == 1


def test_seed_preserves_revoked_or_modified_grants(app, transaction_session):
    """(Ca 4) Chạy lại seed không được cấp lại grant đã thu hồi hoặc reset quyền đã sửa."""
    run_seed(app, transaction_session)

    op_user = transaction_session.query(User).filter_by(username="operator_demo").one()
    vw_user = transaction_session.query(User).filter_by(username="viewer_demo").one()

    # Admin thu hồi grant của operator
    grant_op = transaction_session.query(DeviceAccess).filter_by(device_id="cabinet-001", user_id=op_user.id).one()
    transaction_session.delete(grant_op)

    # Admin hạ hoặc sửa grant của viewer thành OPERATE
    grant_vw = transaction_session.query(DeviceAccess).filter_by(device_id="cabinet-001", user_id=vw_user.id).one()
    grant_vw.access_level = "OPERATE"
    transaction_session.commit()

    # Chạy lại seed
    run_seed(app, transaction_session)

    # Grant operator VẪN BỊ THU HỒI, không tự xuất hiện lại
    assert transaction_session.query(DeviceAccess).filter_by(device_id="cabinet-001", user_id=op_user.id).first() is None

    # Grant viewer VẪN LÀ OPERATE, không bị ép về VIEW ban đầu
    re_vw = transaction_session.query(DeviceAccess).filter_by(device_id="cabinet-001", user_id=vw_user.id).one()
    assert re_vw.access_level == "OPERATE"


def test_seed_with_custom_env_usernames_and_created_by(app, transaction_session, monkeypatch):
    """(Ca 5) Đổi username env: created_by và owner_user_id vẫn gắn đúng ID của admin thật, không bị None."""
    monkeypatch.setenv("ADMIN_BOOTSTRAP_USERNAME", "custom_sys_admin")
    monkeypatch.setenv("OPERATOR_BOOTSTRAP_USERNAME", "custom_operator")
    monkeypatch.setenv("VIEWER_BOOTSTRAP_USERNAME", "custom_viewer")

    run_seed(app, transaction_session)

    admin = transaction_session.query(User).filter_by(username="custom_sys_admin").one()
    dev_real = transaction_session.query(Device).filter_by(device_id="cabinet-001").one()
    cfg_real = transaction_session.query(DeviceConfig).filter_by(device_id="cabinet-001", version=1).one()
    cred_real = transaction_session.query(DeviceCredential).filter_by(device_id="cabinet-001", credential_version=1).one()
    rule_real = transaction_session.query(AlertRule).filter_by(device_id="cabinet-001").first()

    assert dev_real.owner_user_id == admin.id
    assert cfg_real.created_by == admin.id
    assert cred_real.created_by == admin.id
    assert rule_real.created_by == admin.id

    op = transaction_session.query(User).filter_by(username="custom_operator").one()
    vw = transaction_session.query(User).filter_by(username="custom_viewer").one()
    acc_op = transaction_session.query(DeviceAccess).filter_by(device_id="cabinet-001", user_id=op.id).one()
    acc_vw = transaction_session.query(DeviceAccess).filter_by(device_id="cabinet-001", user_id=vw.id).one()
    assert acc_op.access_level == "OPERATE" and acc_op.granted_by == admin.id
    assert acc_vw.access_level == "VIEW" and acc_vw.granted_by == admin.id


@pytest.mark.parametrize("invalid_user", [
    "AdminUpper", "admin space", "admin@domain", "ab", "a" * 65, "admin/slash"
])
def test_username_canonicalization_rejected(app, transaction_session, monkeypatch, invalid_user):
    """(Ca 6-11: 6 ca) Username không canonical hoặc sai regex phải bị từ chối fail closed."""
    monkeypatch.setenv("ADMIN_BOOTSTRAP_USERNAME", invalid_user)
    with pytest.raises(ValueError, match="canonical"):
        run_seed(app, transaction_session)


def test_username_collision_detected_in_env(app, transaction_session, monkeypatch):
    """(Ca 12) Hai vai trò cấu hình trùng username trong env phải bị phát hiện và báo lỗi."""
    monkeypatch.setenv("ADMIN_BOOTSTRAP_USERNAME", "shared_name")
    monkeypatch.setenv("OPERATOR_BOOTSTRAP_USERNAME", "shared_name")
    with pytest.raises(ValueError, match="collision"):
        run_seed(app, transaction_session)


def test_noncanonical_or_colliding_existing_user_rejected(app, transaction_session, monkeypatch):
    """(Ca 13) Phát hiện user cũ có username noncanonical (Admin_Demo) trùng lower với bootstrap username."""
    # Tạo user noncanonical có sẵn trong DB
    bad_old_user = User(
        username="Admin_Demo",
        password_hash="dummy_hash_for_test",
        full_name="Old Noncanonical User",
        status="ACTIVE",
        must_change_password=False,
        token_version=1
    )
    transaction_session.add(bad_old_user)
    transaction_session.commit()

    # Cấu hình bootstrap với username canonical "admin_demo"
    monkeypatch.setenv("ADMIN_BOOTSTRAP_USERNAME", "admin_demo")
    with pytest.raises(ValueError, match="collision"):
        run_seed(app, transaction_session)


def test_existing_user_wrong_role_rejected(app, transaction_session, monkeypatch):
    """(Ca 14) User đã có trong DB nhưng mang sai role (ví dụ: admin_demo nhưng có role VIEWER) phải bị từ chối."""
    # Tạo role VIEWER trước
    viewer_role = transaction_session.query(Role).filter_by(code="VIEWER").first()
    if not viewer_role:
        viewer_role = Role(code="VIEWER", name="Viewer", is_system=True)
        transaction_session.add(viewer_role)
        transaction_session.flush()

    mismatched_user = User(
        username="admin_demo",
        password_hash="dummy_hash_for_test",
        full_name="Mismatched Admin",
        status="ACTIVE",
        must_change_password=False,
        token_version=1
    )
    transaction_session.add(mismatched_user)
    transaction_session.flush()
    transaction_session.add(UserRole(user_id=mismatched_user.id, role_id=viewer_role.id))
    transaction_session.commit()

    # Chạy seed mong đợi role ADMIN cho admin_demo
    with pytest.raises(ValueError, match="role"):
        run_seed(app, transaction_session)


def test_existing_user_inactive_rejected(app, transaction_session, monkeypatch):
    """(Ca 15) User bootstrap đã có trong DB nhưng mang status LOCKED phải bị từ chối, không tự mở khóa."""
    locked_user = User(
        username="admin_demo",
        password_hash="dummy_hash_for_test",
        full_name="Locked Admin",
        status="LOCKED",
        must_change_password=False,
        token_version=1
    )
    transaction_session.add(locked_user)
    transaction_session.commit()

    with pytest.raises(ValueError, match="active"):
        run_seed(app, transaction_session)


@pytest.mark.parametrize("bad_pwd", [
    None, "", "short", "REPLACE_WITH_OWN_RANDOM_VALUE", "changeme_secret_key_12345"
])
def test_missing_or_placeholder_password_fails_atomically(app, transaction_session, monkeypatch, bad_pwd):
    """(Ca 16-20: 5 ca) Mật khẩu thiếu, quá ngắn hoặc chứa placeholder phải raise ValueError và rollback atomic."""
    if bad_pwd is None:
        monkeypatch.delenv("ADMIN_BOOTSTRAP_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", bad_pwd)

    with pytest.raises(ValueError):
        run_seed(app, transaction_session)

    assert transaction_session.query(User).filter_by(username="admin_demo").first() is None
    assert transaction_session.query(Device).filter_by(device_id="cabinet-001").first() is None


def test_device_states_follow_v1_3_contract_not_faked(app, transaction_session):
    """(Ca 21) Thiết bị seed ban đầu phải đúng trạng thái REGISTERED, applied=None, broker=PENDING."""
    run_seed(app, transaction_session)

    dev_real = transaction_session.query(Device).filter_by(device_id="cabinet-001").one()
    assert dev_real.lifecycle_state == "REGISTERED"
    assert dev_real.applied_config_version is None
    assert dev_real.broker_desired_enabled is True
    assert dev_real.broker_actual_enabled is None
    assert dev_real.broker_sync_state == "PENDING"
    assert dev_real.connection_state == "UNKNOWN"

    cred_real = transaction_session.query(DeviceCredential).filter_by(device_id="cabinet-001").one()
    assert cred_real.status == "REGISTERED"
    assert cred_real.activated_at is None

    shadow_real = transaction_session.query(DeviceShadow).filter_by(device_id="cabinet-001").one()
    assert shadow_real.drift_status == "PENDING"
    assert shadow_real.reported == {}

    # Thiết bị giả lập ngoài phạm vi (sim-unassigned)
    dev_sim = transaction_session.query(Device).filter_by(device_id="sim-unassigned").one()
    assert dev_sim.data_source == "SIMULATED"
    assert dev_sim.applied_config_version is None
    assert dev_sim.broker_sync_state == "PENDING"

    # sim-unassigned không có quyền nào được cấp
    assert transaction_session.query(DeviceAccess).filter_by(device_id="sim-unassigned").count() == 0
