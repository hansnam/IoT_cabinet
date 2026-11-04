# Đánh giá tổng thể và quyết định chuyển sang Django

Ngày 04/11/2026. Bối cảnh: bài tập lớn 4 người, đã thuyết trình, còn 7 ngày chuẩn bị demo; nhóm xác nhận dùng Django. Đã bổ sung rà soát PDF đề bài 14 trang và các phần liên quan trong sáu bộ slide. [Đặc tả triển khai V1.4](IOT_TU_THIET_BI_DAC_TA_CHUNG_V1_3.md) thay quyết định framework/phạm vi/lịch của [V1.3 lịch sử](../Description/IOT_TU_THIET_BI_DAC_TA_CHUNG_V1_3.md). Căn cứ chi tiết tại [đối chiếu đề bài và checklist demo](DOI_CHIEU_DE_BAI_VA_CHUAN_BI_DEMO.md).

## 1. Kết luận khả thi

**Bài toán phù hợp nhóm 4 người; bản kế hoạch cũ quá rộng cho một tuần còn lại. Django là lựa chọn hợp lý để giảm phần quản trị và hạ tầng tài khoản, nhưng chuyển framework giữa chừng vẫn có rủi ro đáng kể.** Khả thi có điều kiện nếu tái dùng logic IoT đã có, dùng Django Admin, giữ một node, tích hợp thật ngay D2 và dành D5–D7 cho kiểm thử/bàn giao. Không đủ căn cứ bảo đảm kịp chỉ vì có AI.

Điểm mạnh của V1.3 là đã phân biệt đúng điều khiển relay với đo quay, đặt AUTO/safety tại ESP32, có MQTT contract, ACK/idempotency, desired/applied, scope và kiểm thử lỗi mạng. Giữ những điều này. Điểm cần giảm là hạ tầng tự xây cho users/role/session, màn CRUD riêng, ràng buộc số bảng, nhiều schema P1/P2 dự phòng và phần tài liệu mở rộng lấn phần tích hợp bắt buộc.

Flask không phải Python thuần; nó chủ động cung cấp nền nhỏ. Nhận xét “phải tự ghép nhiều phần” đúng với cách chọn stack và đặc tả hiện tại. Django phù hợp hơn với nhu cầu có sẵn ORM/migrations/auth/admin. Đổi framework không tự tạo nghiệp vụ IoT, quyền từng thiết bị, broker ACL hoặc firmware an toàn.

## 2. Hiện trạng nhìn thấy trong kho

Đây là kết quả đọc mã/tài liệu, **không phải báo cáo chạy test hay xác nhận phần cứng hoạt động**.

| Quan sát | Căn cứ trong repo | Hệ quả |
|---|---|---|
| Backend Flask đã có nền và logic đáng kể | backend/app/__init__.py, models, auth, permissions, devices, telemetry, commands, mqtt | Không viết lại mọi thuật toán; port từng lát cắt và giữ test case có ý nghĩa |
| Có migration Alembic 27 bảng và SQL schema cũ | backend/migrations/versions/fcb8c798de10_p0_core_27_tables.py; database/iot_cabinet_27_tables.sql | Không thể chỉ đổi Flask thành Django trong requirements; cần DB/migration đích và data mapping |
| Có tests auth/quyền/session và MQTT/config/commands | tests/tv3, tests/tv2 | Có tài sản kiểm thử để port; chưa có bằng chứng chúng đã PASS trong môi trường hiện tại |
| Alert worker còn ghi chờ triển khai evaluator | backend/workers/alert_worker.py | Cảnh báo chưa thể coi đã xong chỉ vì có worker file |
| React có sẵn layout/mock/fetch | frontend/src/App.jsx | Giữ UI dùng được, sửa auth/CSRF và mapping payload; mock có CABINET-01 và fan_output dạng số khác contract |
| Compose mới gồm PostgreSQL/Mosquitto/Adminer | deployment/docker-compose.yml | Còn API/workers/proxy/TLS/static/health để thành deployment chuẩn |
| firmware/hardware trong kho mới thấy .gitkeep | firmware/, hardware/ | Không xác nhận code/mạch của TV1 đã có ở nơi khác; phải kiểm bo thật ngay ngày đầu |
| .env.example có giá trị mẫu dạng credential/khóa cụ thể | .env.example | Đổi thành placeholder khi triển khai; credential thật nếu từng public phải thay, không lặp giá trị trong tài liệu |
| Hai bản đặc tả giống nhau trước lần sửa | docs/ và Description/ | docs là đích hiện hành; Description giữ lịch sử, không sửa song song tạo hai nguồn quyết định |
| Đã bổ sung PDF đề bài và sáu PPTX học phần | docs/; đã trích đọc PDF 14 trang và phần slide liên quan | Xác nhận một node vật lý bắt buộc, hai node khuyến nghị; không JWT/framework/số bảng cố định; lịch một tuần cho demo theo nhóm |

## 3. Đánh giá từng mặt

| Mặt | Đánh giá | Điều chỉnh cụ thể |
|---|---|---|
| Bài toán | Rõ, có phần cứng và mục đích IoT | Chứng minh đo/điều khiển thật; không tuyên bố hiệu quả làm mát chưa đo |
| Phạm vi | PDF 4.2 xác nhận một node thật là tối thiểu, hai node nhóm 4 là khuyến nghị | Giữ P0 một node; không bảo đảm mức điểm cao nhất chỉ vì đủ tối thiểu; hoãn RPM/AI/OTA/backfill |
| Backend | Flask làm được nhưng nhiều hạ tầng thủ công | Django/DRF + Admin; CRUD dùng sẵn, IoT dùng service |
| Auth | JWT không mặc định an toàn hơn session; bản cũ vẫn kiểm DB mỗi request nên lợi ích phi trạng thái ít | Đề xuất session/CSRF cho web cùng origin và Admin để giảm công; Django không bắt bỏ JWT; giữ RBAC/scope/revoke/audit như nhau |
| Phân quyền | Role kết hợp device scope đúng nhu cầu | Django Groups/Permissions thay catalog tự xây; DeviceAccess vẫn phải viết và lọc queryset |
| Database | 27 bảng không tự sai, nhưng biến số bảng thành chỉ tiêu làm khó chuyển đổi | Bỏ số bảng cố định, dùng bảng framework; hợp audit/activity, bỏ schema dự phòng chưa dùng |
| Lệnh/MQTT | ACK/retry/dedup/outbox là cần thiết để tránh thành công giả | Giữ semantics và MQTT v3; không thay bằng publish ngay trong HTTP rồi coi xong |
| Firmware | AUTO/NVS/TTL đúng hướng | Ưu tiên chứng minh loop không bị reconnect/TLS block; làm rõ fail-safe phụ thuộc nguồn/relay |
| Frontend | Có nền React; PDF 4.6 không chỉ định cùng framework toàn UI | Giám sát/vận hành ở React, quản trị ở Admin; register/activate/lock phải có UI, command chỉ hỗ trợ kỹ thuật |
| Cảnh báo | Bốn loại đủ P0; incident ACK/clear/resolve hợp lý | Code cố định, threshold từ applied config, không rule engine tùy biến |
| Triển khai | Kiến trúc mong muốn đúng nhưng Compose thực chưa đầy đủ | Cùng origin + worker/static/health/TLS, thử mạng demo sớm |
| Phân công | TV2/TV4 dễ thành điểm nghẽn | TV3 nhận Admin/proxy/TLS, TV2 hỗ trợ queries/alerts, mọi người tự viết báo cáo/test |
| Lịch | Nhóm đã thuyết trình, còn một tuần demo; lịch PDF không tự thay thông tin mới | D1–D7 cho demo, gate thật D2, freeze D5, D6 restore/diễn tập, D7 buffer; không làm lại bài thuyết trình |
| Đánh giá | Có nhiều ca tốt nhưng tài liệu dài không thay bằng chứng | Matrix thực, latency/soak/revoke/restore; chưa chạy ghi NOT_RUN |

## 4. Những thay đổi đã đưa vào đặc tả

1. Chọn Django 5.2 LTS/DRF, ORM/migrations/auth/password/admin tích hợp; không chỉ thay tên Flask.
2. Đề xuất web session cookie + CSRF cùng origin để tận dụng Django/Admin; đây là lựa chọn triển khai, không hạ yêu cầu bảo mật hoặc kết luận JWT kém an toàn. Reload còn hạn tiếp tục login, 15 phút tuyệt đối; logout/lock/reset registry revoke; xử lý đúng DRF 403/login CSRF.
3. Group/Permission framework, một role/user qua service; DeviceAccess và scope ở queryset/create/action. Cả Admin và worker dùng cùng policy.
4. DB Django mới, giữ DB/migrations Flask. Kiểm kê/import dữ liệu cần giữ, không tự xóa hoặc sao raw password hash. Bỏ ràng buộc đúng 27 bảng.
5. Một nguồn AuditLog cho kiểm toán/nhật ký; shadow projection và rules cố định giảm bảng/code chưa cần. Nhật ký vẫn có actor/function/time/outcome và redaction.
6. Admin thay React CRUD quản trị, lifecycle dùng action/command cùng service; telemetry/applied/log không sửa tùy ý.
7. Giữ MQTT schema3, canonical hash, outbox/retry/ACK, AUTO/NVS/TTL và distinction relay/RPM.
8. Sửa phân công và lịch 7 ngày; bỏ lịch cũ và lượng EXT không thực hiện khỏi cam kết tuần này.
9. Test chuyển sang Django/DRF với CSRF enforcement, PostgreSQL race và broker thật; không coi file tests có sẵn là PASS.

## 5. Thứ tự làm để ít rủi ro

**Đầu tiên xác nhận bo thật và lát cắt dữ liệu**, song song scaffold auth của Django. Đến cuối D2 phải thấy số đo thật trên React và một lệnh có ACK; nếu không, tập trung toàn nhóm sửa đường này. Không dành hai ngày đầu chỉ tạo đủ models/CRUD hoặc làm lại giao diện.

Port phần ít phụ thuộc trước: topic/parser/range/hash/fixtures và thuật toán TTL/dedup; sau đó models/queries/transactions và DRF views. Mã đang import db.session/app_context/Flask-JWT không copy nguyên sang Django. Permission helpers, outbox lease và service có ORM phải viết adapter/query mới rồi kiểm theo ca cũ.

Giữ code/DB Flask làm nguồn tham khảo. Một worker sở hữu ingest/dispatch tại một thời điểm. Không dùng Alembic tạo schema Django, không dual-write, không cố chạy hai backend trên cùng bảng để giảm công import. Rollback có quy trình giữ dữ liệu mới trước khi quay lại.

AI có ích khi task rõ, ví dụ “port telemetry validator giữ nguyên fixture”, “tạo serializer với fields allowlist”, “viết admin action gọi service”. Mỗi PR phải có người đọc/test; người nộp phải giải thích được luồng quyền, transaction, ACK và safety. Không giao AI yêu cầu chung “viết toàn bộ hệ thống” rồi nối các kết quả bằng thay contract.

## 6. Rủi ro cần theo dõi trong tuần

| Dấu hiệu | Xử lý |
|---|---|
| D2 chưa thấy dữ liệu thật hoặc chưa ACK | Dừng màn phụ, họp tích hợp TV1/TV2/TV4; kiểm payload/broker/clock/network/worker bằng log cùng ID |
| Quản trị chiếm quá nhiều thời gian | Dùng Admin form/action cho quy trình giao diện, command chỉ hỗ trợ provisioning; hoãn React Users/onboarding wizard |
| Scope leak hoặc Admin bỏ qua service | Khóa route/action đó, sửa policy/queryset/shared service trước mở lại; không chỉ ẩn nút |
| TLS chỉ làm ngày cuối | Chạy từ D3–D4 trên LAN demo, kiểm CA/SAN/clock/hotspot; ghi phần chưa đạt nếu lỗi còn |
| DB migration muốn “drop làm lại” để nhanh | Dùng DB mới và snapshot, giữ cũ; dữ liệu cần giữ phải mapping/import |
| D5 vẫn thêm tính năng | Freeze, sửa lỗi P0, dành thời gian soak/restore/báo cáo |
| Giảng viên thông báo yêu cầu mới khác PDF | Ghi rõ nguồn thay đổi và chỉnh task; PDF hiện không bắt hai node/users REST riêng; giữ đủ UI quản trị và dữ liệu đầu cuối |

Đã đối chiếu tài liệu học phần; chưa chạy phần mềm/phần cứng nên chưa xác nhận kết quả triển khai. Bằng chứng đầu cuối, độ đúng và khả năng giải thích của nhóm quyết định mức hoàn thành; số bảng hoặc lượng mã AI sinh không thay các tiêu chí đó.
