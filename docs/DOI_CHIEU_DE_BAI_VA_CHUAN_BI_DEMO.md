# Đối chiếu đề bài và chuẩn bị demo

Rà soát ngày 04/11/2026 từ [PDF đề bài 14 trang](<ĐỀ BÀI ĐỒ ÁN KẾT THÚC HỌC PHẦN.pdf>) và các phần liên quan trong sáu bộ slide tại docs. Trang PDF tính từ trang đầu; số slide là thứ tự slide trong file, có thể khác số in trên slide. [Đặc tả hiện hành V1.4](IOT_TU_THIET_BI_DAC_TA_CHUNG_V1_3.md) là thiết kế của nhóm; bảng này phân biệt yêu cầu giảng viên với quyết định triển khai.

**Bối cảnh mới nhất:** nhóm đã thuyết trình, còn một tuần chuẩn bị demo. PDF trang 14 ghi lịch học phần khác mốc một tuần; giữ thông tin nhóm cung cấp làm mốc công việc hiện tại, không tự suy ra ngày demo cụ thể.

## 1. Những điều đã xác nhận từ đề

| Câu hỏi | Kết quả đọc trực tiếp | Quyết định cho nhóm |
|---|---|---|
| Có bắt hai node cho nhóm 4 không? | 4.2, trang 3: bắt ít nhất một thiết bị vật lý hoạt động; hai node cho nhóm 4 là khuyến nghị | P0 một ESP32 thật, P1 RPM hoãn; đáp ứng mức tối thiểu không đồng nghĩa bảo đảm mức điểm cao nhất |
| Hai cảm biến có bắt buộc không? | 4.2: ít nhất một loại cảm biến; hai loại dữ liệu trở lên là khuyến nghị | DHT22 có nhiệt/ẩm, vẫn một module cảm biến; không tự gọi thành hai node |
| Django được dùng không? | 4.4, trang 4: backend tự xây hoặc tùy biến đáng kể nền tảng mã nguồn mở; không chấp nhận chỉ dashboard có sẵn thiếu quản lý | Dùng Django/DRF, nhóm tự xây MQTT/device lifecycle/scope/commands/alerts và kiểm thử |
| JWT có bắt buộc không? | 4.5, trang 6 yêu cầu xác thực và phân quyền; token là một ví dụ test, không quy định thuật toán/auth scheme | Django session hoặc JWT đều phải có kiểm soát, test và lý do chọn; session là đề xuất giảm công cho web/Admin |
| Có bắt React toàn bộ không? | 4.6, trang 7 yêu cầu giao diện chức năng, không chỉ định công nghệ | Admin + React là cách đáp ứng suy ra từ yêu cầu; phải demo được UI quản trị thật, không coi Admin mặc định chưa tùy biến là đủ |
| Có bắt users REST API riêng không? | 4.4a/4.6 yêu cầu quản lý tài khoản và UI; không nêu mỗi chức năng phải có endpoint REST riêng | User CRUD trong Admin, auth API cho React; cùng chính sách revoke/scope/audit |
| Có bắt group/location nhiều tầng không? | 4.4b, trang 5 cho gán thiết bị cho user, group, location hoặc khu vực | DeviceAccess gán user đáp ứng; label vị trí tùy chọn, chưa cần tổ chức nhiều tầng |
| Có bắt 27 bảng không? | Mục 6 trang 9 yêu cầu mô hình dữ liệu/ERD, không số bảng | Dùng models/framework tables phù hợp; schema thật và migrations là căn cứ |
| Bao nhiêu cảnh báo? | 4.4d yêu cầu ít nhất một rule và lịch sử/xử lý; ba role được khuyến nghị ở 4.4a | Nhóm giữ ba role và bốn loại cảnh báo, không gọi đây là số lượng bắt buộc từ đề |
| TLS bắt buộc vô điều kiện không? | 4.5: mã hóa nếu nền tảng triển khai cho phép | Nhóm chọn HTTPS/MQTTS cho release laptop có hỗ trợ; chưa chạy không ghi đạt |
| AI/OTA có bắt buộc không? | Mục 5 trang 7–9 là nâng cao, chỉ nên làm sau cơ bản ổn | Không triển khai trong tuần demo P0; không xem việc dùng AI viết code là chức năng AI IoT |
| Cần tối thiểu bao nhiêu security tests? | 4.5 trang 6: ít nhất hai | Ưu tiên scope/IDOR và thu hồi kết nối broker; thêm CSRF/expiry/payload/ACL theo thiết kế |

## 2. Ma trận yêu cầu → chức năng → bằng chứng demo

| Nguồn | Yêu cầu | Nơi thực hiện | Bằng chứng cần chuẩn bị |
|---|---|---|---|
| 4.1, trang 2–3 | Bài toán, đối tượng, lý do IoT/ràng buộc/tiêu chí | Báo cáo và hồ sơ thiết kế | Tủ/quạt/sensor thật, giới hạn làm mát và số đo |
| 4.2, trang 3 | Sensor/ID/remote command/heartbeat/reconnect | Firmware + MQTT worker | Nhiệt/ẩm thật, boot IDs, mất mạng/reconnect, AUTO |
| 4.3, trang 4 | Giao thức hai chiều/payload/time/unit/QoS/ACK | MQTT v3 + API | Fixture khớp firmware, topic ACL, command có ACK |
| 4.4a, trang 4–5 | Tạo/login/logout/password/profile/status/role | Admin + auth API | Tạo user, role khác nhau, khóa/reset, phiên cũ bị từ chối |
| 4.4b, trang 5 | Lifecycle từ register tới decommission | DeviceService/BrokerOperations + Admin | Register/activate/lock/decommission trên UI; broker disconnect và deny reconnect |
| 4.4b | Gán user, hardware/firmware version, lỗi/bảo trì | DeviceAccess + Device/Event/Admin | Grant, version thật, maintenance/fault history; không chỉ có version trong JSON mà bỏ khỏi model/UI |
| 4.4c | Validate/time storage/device/time lookup/chart/filter | Ingest/DB/React history/export | Dữ liệu hiện tại/lịch sử, from/to, dữ liệu lỗi bị reject; không bắt analytics riêng nếu đã lọc |
| 4.4d | Rule/alert history/ack hoặc xử lý | Applied config + AlertService/UI | Nâng nhiệt/ngưỡng thử → alert → ACK → clear/resolve |
| 4.4e, trang 6 | Lệnh từ giao diện, kiểm quyền, phản hồi, history | React → service/outbox → ESP32 ACK | Pending khác APPLIED; mất ACK hiện timeout; viewer bị deny |
| 4.5, trang 6 | Hash/auth/scope/credentials/revoke/audit/secrets/TLS/privacy | Accounts/permissions/broker/audit/deployment | Hai security tests tối thiểu, log redaction, kết nối mã hóa thực |
| 4.6, trang 7 | UI các quy trình chính | React + Admin | Thao tác quản trị và vận hành trên browser; command chỉ hỗ trợ kỹ thuật |
| Mục 6, trang 9 | Context/use case/layers/hardware/deploy/ERD/API/MQTT/2 sequence/lifecycle/role/threats | docs/database/hardware | Sơ đồ theo hệ thống đã triển khai, lý do từng quyết định |
| Mục 7, trang 9–10 | Functional/security/nonfunctional tests có bằng chứng | tests + bảng kết quả | Actual/commit/log, latency/loss/recovery/soak và phương pháp đo |
| 8.1, trang 10–11 | Repo đủ thư mục, lịch sử commit, tài khoản từng người, release/tag | GitHub/README | Cài/chạy được; backend Django dưới backend/django, không làm sai đường dẫn bàn giao |
| 8.2–8.4, trang 11–13 | Báo cáo/slide/demo trực tiếp/video dự phòng | Hồ sơ nhóm | Slide đã thuyết trình giữ lại; cập nhật báo cáo thực; demo trực tiếp, video không thay hoàn toàn |
| Mục 10, trang 13 | An toàn, privacy, khai báo bên thứ ba/AI | Phần cứng và báo cáo | DC thấp áp, không secret thật trên repo, khai báo AI và hiểu mã |

Đây là ma trận cần thực hiện, không phải chứng nhận các chức năng đã xong hoặc tests đã PASS.

## 3. Liên hệ với bài giảng để giải thích quyết định

| Tài liệu / phần tham khảo | Áp dụng cho hệ thống tủ thiết bị |
|---|---|
| [Chương 1](Chapter_1_IoT_Overview.pptx), slide 4/9/30 và 77 | Nối trạng thái vật lý → dữ liệu → quyết định → tác động; demo phải có sensing/control thay vì chỉ web |
| [Chương 2](Chapter_2_IoT_Architecture_and_Components.pptx), slide 5/15/31/66 | Phân trách nhiệm theo layer và ràng buộc; AUTO ngay ESP32, DB/API ở laptop, validate/dedup trước query/rules |
| [Chương 3](Chapter_3_IoT_Protocols_Networks_Standards.pptx), slide 65–69 | MQTT broker/pubsub/topic/LWT/retained/QoS/TLS. QoS1 có thể trùng nên cần idempotency; application ACK khác PUBACK; giới hạn publish của PubSubClient cần kiểm từ thư viện |
| [Chương 4](Chapter_4_IoT_Design_and_Development.pptx), slide 14/15/23/26/27 và 62/63/69 | Yêu cầu → interface → test; một luồng đầu cuối D2, giữ contract/schema/pinout/timing và test mất mạng/reboot |
| [Chương 5](Chapter_5_IoT_Data_Analytics_and_Supporting_Services.pptx), slide 10/22/31–34 | Chất lượng dữ liệu/time/context; dashboard freshness; threshold/persistence/hysteresis chống alert trùng/flapping; ACK và clear riêng |
| [Chương 6](Chapter_6_IoT_Security_Privacy_and_Applications.pptx), slide 10/12/15/27–29/34–35 | Identity khác permission; bảo vệ xuyên suốt trust boundaries, retention/privacy và fallback local khi mạng mất |

Gateway, cloud, AI và mọi giao thức xuất hiện trong slide không tự trở thành thành phần bắt buộc của prototype. Wi-Fi AP chỉ chuyển tiếp mạng; không gọi là gateway xử lý IoT nếu không có chức năng đó. Nêu rõ edge logic ở ESP32, không cần mua một gateway riêng cho P0.

## 4. Ưu tiên một tuần demo

1. Node thật, AUTO/relay, telemetry tới DB/API/React; command có ACK trước khi hoàn thiện màn phụ.
2. UI Admin quản lý users/device/scope/lifecycle và UI React vận hành; không chỉ demo bằng tài khoản superuser.
3. Mất mạng, FAULT, TTL, config/NVS; scope deny và broker revoke/disconnect thật.
4. Cảnh báo có history/ACK/clear; audit và metadata/version đúng.
5. Cài máy sạch/restore, số đo thực, diễn tập 10 bước trong khoảng 5–7 phút và mỗi người giải thích được phần mình.

Chuẩn bị trước tài khoản ba role, thiết bị test có thể register/activate, simulator không grant, ngưỡng cảnh báo tạm, mạng demo/CA/IP, cách ngắt mạng có kiểm soát và video dự phòng. Khôi phục ngưỡng/cấu hình sau mỗi lượt. Không để demo cuối chỉ còn thiết bị DECOMMISSIONED khiến lượt sau không chạy lại được; chuẩn bị quy trình cấp ID/credential mới phù hợp.

Mục 9 trang 13 nêu các rủi ro giới hạn đánh giá: không thiết bị thật, thiếu backend/auth, mã không chạy, không demo được đầu cuối hoặc không tham gia vấn đáp. PDF không có thang điểm chi tiết cho từng feature; không gán điểm cộng cố định cho JWT/Django/node2/OTA/AI. Đồ án chiếm 60% điểm tổng kết theo trang 14.

Các file học phần PDF/PPTX hiện được .gitignore bỏ qua. Không đổi chính sách này trong lần rà soát; giữ bảng yêu cầu và hồ sơ nhóm ở Markdown để repo vẫn đọc hiểu được khi không kèm toàn bộ học liệu.
