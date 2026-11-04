import uuid
from sqlalchemy import (
    Column, String, Text, ForeignKey, Index, func
)
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from backend.app.extensions import db

class Location(db.Model):
    """Bảng locations: Vị trí lắp đặt thiết bị / tủ mạng"""
    __tablename__ = "locations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(64), unique=True, nullable=False)
    name = Column(String(128), nullable=False)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class DeviceGroup(db.Model):
    """Bảng device_groups: Nhóm thiết bị phục vụ quản lý / tổ chức"""
    __tablename__ = "device_groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(64), unique=True, nullable=False)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class DeviceGroupMember(db.Model):
    """Bảng device_group_members: Thành viên thuộc nhóm thiết bị"""
    __tablename__ = "device_group_members"

    group_id = Column(UUID(as_uuid=True), ForeignKey("device_groups.id", ondelete="CASCADE"), primary_key=True)
    device_id = Column(String(64), ForeignKey("devices.device_id", ondelete="CASCADE"), primary_key=True)
    added_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_device_group_members_device_id", "device_id"),
    )
