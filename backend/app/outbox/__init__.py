"""
Outbox Package (Thành viên 2 chủ trì)
Thực thi mẫu Transactional Outbox: Đảm bảo độ tin cậy khi dispatch lệnh điều khiển sang broker,
quản lý lease, retry có trễ (3s, 6s) và khôi phục khi worker bị crash.
"""
