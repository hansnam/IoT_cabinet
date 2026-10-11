# ĐẶC TẢ CHUNG V1 — IoT giám sát nhiệt độ và điều khiển thông gió tủ thiết bị

Ngày chốt: 30/09/2026. Nhóm: 4 người. Giữ phân công TV1–TV4 đã giao.

Đây là hợp đồng triển khai của nhóm, không phải báo cáo kết quả đã thực hiện. Các số thời gian/ngưỡng dưới đây là mục tiêu thiết kế hoặc cấu hình thử nghiệm; chỉ đưa vào phần kết quả khi đã đo thật.

## 1. Bài toán, phạm vi và tiêu chí hoàn thành

**Tên đề:** Hệ thống IoT giám sát nhiệt độ, độ ẩm và điều khiển thông gió tủ thiết bị mạng.

Người dùng: quản trị hệ thống và người phụ trách vận hành tủ thiết bị. Đo nhiệt/ẩm trong tủ, tự điều khiển quạt, xem dữ liệu và cảnh báo từ xa, lưu lịch sử và quản lý quyền vận hành.

- Demo một node ESP32 thật, DHT22 và quạt DC trong hộp có cửa lấy/thoát gió.
- Không cần router/switch/server thật. Không tuyên bố hiệu quả giảm nhiệt nếu chưa có phép thử nguồn nhiệt và số đo đối chiếu.
- Quạt tạo thông gió; không làm nhiệt độ thấp hơn không khí bên ngoài như máy lạnh.
- Độ ẩm được đo và cảnh báo; không tuyên bố quạt điều chỉnh độ ẩm về một mức chính xác.
- Một node thật là mức tối thiểu theo đề bài; hai node là khuyến nghị cho nhóm 4 người. Làm một node ổn định trước; node thứ hai chỉ bổ sung sau, dùng cùng hợp đồng dữ liệu.
- Không mở rộng sang nhà kính, tưới cây, nhà thông minh, báo cháy hoặc chẩn đoán hỏng quạt.
- OLED tùy chọn; không ảnh hưởng hoàn thành bản cơ bản.
- Cần xác nhận đăng ký phạm vi với giảng viên vì các đề khác có tên rất rộng.

**Đạt bản V1 khi:** chạy đủ 10 thao tác demo trong đề; có phân quyền thực ở backend và ACL ở broker; khóa thiết bị làm mất quyền kết nối; có lịch sử dữ liệu/lệnh/cảnh báo; mất mạng vẫn AUTO; có tài liệu và bằng chứng kiểm thử.

## 2. Công nghệ thống nhất

| Khối | Chốt dùng | Lý do và quy tắc |
|---|---|---|
| Bo | ESP32 DevKit dòng WROOM-32, 30 chân, CH340, USB-C, đã hàn chân | Bám hướng dẫn ESP32 thường; CH340 chỉ là cầu USB–UART. Nếu đã mua CP2102/bo 38 chân vẫn dùng được, phải ghi lại pinout thật |
| Cảm biến | Module DHT22 ba chân có điện trở kéo lên | Đo nhiệt/ẩm; tốt hơn DHT11 về sai số nhiệt độ. DHT11 dùng thử được nhưng bản bàn giao thống nhất DHT22 |
| Quạt | DC 5V hai dây | Chỉ bật/tắt; không làm PWM trong V1 |
| Điều khiển quạt | Relay module một kênh (hoặc dùng một kênh của module hai kênh), nhận tín hiệu 3,3V | Xác minh mức kích HIGH/LOW và điện áp nuôi theo module thực tế |
| Firmware | C/C++, Arduino IDE, Arduino core ESP32 | Cả nhóm dùng một phiên bản core đã kiểm tra; TV1 ghi phiên bản vào README |
| Thư viện firmware | DHT sensor library của Adafruit + Adafruit Unified Sensor; PubSubClient; ArduinoJson; Preferences | Khóa phiên bản sau khi build thử. Wokwi có thể cần DHTesp cho cảm biến mô phỏng; không coi khác biệt mô phỏng là đổi hợp đồng firmware |
| Mạng | Wi-Fi 2,4GHz, MQTT | Thiết bị cố định có nguồn; MQTT đi hai chiều |
| Broker | Eclipse Mosquitto + Dynamic Security plugin | Có tài khoản riêng, ACL và thu hồi kết nối khi đang chạy |
| Backend | Python + Flask; SQLAlchemy; Flask-Migrate; paho-mqtt; Flask-JWT-Extended | Một ứng dụng chung chia Blueprint; một worker MQTT |
| Mật khẩu người dùng | Werkzeug generate_password_hash/check_password_hash | Không tự viết thuật toán băm |
| DB | PostgreSQL 16, database `iot_cabinet` | DB duy nhất cho tích hợp, kiểm thử và bàn giao; không chia MySQL/SQLite/Firebase |
| Frontend | React + Vite, JavaScript; React Router; Recharts; fetch | Web responsive; V1 polling API mỗi 2 giây để giảm độ phức tạp |
| Triển khai chuẩn | Docker Compose cho PostgreSQL, Mosquitto và backend/worker | TV4 tổ chức; TV2/TV3 cung cấp config phần mình. ESP32 chạy ngoài Docker |
| Công cụ | GitHub, VS Code, Arduino IDE, Wokwi, Postman hoặc Bruno, MQTT Explorer, DBeaver | Wokwi thử phần nhúng; API và broker thử riêng bằng máy tính |

**Phiên bản:** TV2/TV4 tạo requirements và package-lock; TV1 ghi version Arduino core/library. Sau commit nền chạy được, nhóm không tự nâng cấp phiên bản riêng. Mosquitto phải chọn bản có Dynamic Security đã thử, ghi chính xác tag vào deployment, không dùng tag `latest` ở bản bàn giao.

**Cấu hình demo:** laptop chạy dịch vụ; ESP32 và máy mở web cùng mạng có thể giao tiếp với laptop. Điện thoại hotspot không tự biến backend thành dịch vụ truy cập từ Internet. Broker URL trong ESP32 dùng IP LAN laptop, không dùng `localhost`. Bản LAN ban đầu có thể dùng HTTP/MQTT; đây là kết nối chưa mã hóa. Khi nền tảng triển khai cho phép, cấu hình HTTPS/MQTTS và ghi rõ trạng thái mã hóa trong báo cáo; không tuyên bố có TLS nếu chưa chạy được.

## 3. Linh kiện và sơ đồ phải chốt

| Món | Số lượng cho 1 node | Ghi chú |
|---|---:|---|
| ESP32 + cáp USB có dữ liệu | 1 bộ | Nếu starter kit đã có thì không mua trùng |
| Module DHT22 3 chân | 1 | DATA pull-up về 3,3V; kiểm tra nhãn chân |
| Quạt DC 5V 2 dây | 1 | Ghi dòng định mức trên ảnh/BOM |
| Relay module tương thích tín hiệu 3,3V | 1 | Không suy ra tương thích chỉ từ chữ relay 5V |
| Breadboard và dây đực–đực/đực–cái | 1 bộ | Có dây cái–cái nếu dùng module đã hàn chân |
| LED + điện trở 330Ω 1/4W | 2 bộ | 220Ω trong starter kit dùng được cho LED báo từ GPIO 3,3V |
| Module còi có đầu vào điều khiển tương thích 3,3V | 1 | Nếu còi rời, xác minh dòng hoặc dùng tầng transistor phù hợp |
| Nguồn 5V đủ dòng, dự kiến 5V/2A | 1 | TV1 tính lại theo tải; cần đầu nối đưa nguồn ra dây để lắp |
| Hộp mô hình thông gió | 1 | Lỗ lấy gió và quạt thoát gió; cố định bo/cảm biến |
| Nhiệt kế đối chiếu | 1, có thể mượn | Ghi giới hạn của thiết bị đối chiếu |
| OLED I2C 0,96 inch | Tùy chọn | Thêm sau; kiểm tra điện áp module |

### 3.1 Pin đề xuất cho ESP32 WROOM-32

| Chức năng | GPIO |
|---|---:|
| DHT22 DATA | 23 |
| Relay IN | 18 |
| LED kết nối | 19 |
| LED cảnh báo | 25 |
| Còi module IN | 26 |
| OLED SDA/SCL nếu thêm | 21/22 |

- GPIO là tên chân chức năng, không phải thứ tự chân trên bo. TV1 xác nhận pinout bo mua thật trước khi nối.
- DHT22 module: cấp 3,3V khi module hỗ trợ, GND về GND, DATA về GPIO23; tín hiệu vào ESP32 phải ở mức 3,3V.
- Relay: điện áp nguồn nuôi theo datasheet module; IN nhận GPIO18, GND nối đúng sơ đồ module. Đặt `RELAY_ACTIVE_LOW` theo kết quả thử để ON/OFF không bị ngược.
- Nhánh tải quạt: nguồn +5V → COM → NO → cực dương quạt; cực âm quạt → âm nguồn quạt. Không cấp dòng quạt từ GPIO.
- Không đưa 12V vào nhánh quạt 5V hoặc ESP32. Không đấu điện lưới cho mô hình.
- Không tự nối đồng thời USB và nguồn ngoài vào chân 5V bo nếu chưa xác minh cách cấp nguồn của bo. TV1 vẽ sơ đồ nguồn cụ thể, không chỉ ghi chung “nguồn 5V”.
- Mỗi LED rời có điện trở nối tiếp. Không sao chép mô phỏng LED không có điện trở sang mạch thật.
- Không thể xác định quạt quay thật/hỏng chỉ từ trạng thái relay. Giao diện ghi rõ “đầu ra quạt ON/OFF”; V1 không có cảm biến tốc độ.

## 4. Logic thiết bị thống nhất

| Tham số | Mặc định V1 |
|---|---|
| Đọc DHT22 | Mỗi 2 giây |
| Telemetry | Mỗi 5 giây |
| Heartbeat | Mỗi 15 giây |
| Offline ở backend | Không có bản tin hợp lệ trong 45 giây hoặc nhận LWT |
| AUTO bật/tắt quạt | `fan_on_c=30`, `fan_off_c=28` |
| Cảnh báo nhiệt | `temp_alert_c=35`, tồn tại 10 giây |
| Cảnh báo ẩm cao | `humidity_alert_pct=80`, tồn tại 10 giây |
| MANUAL | Lệnh thủ công có thời hạn; mặc định 60 giây rồi trở về AUTO |
| Mất Wi-Fi/MQTT | Trở về AUTO ngay; giữ cấu hình đã áp dụng |
| Lỗi cảm biến | 3 lần đọc liên tiếp lỗi → FAULT, bật quạt dự phòng, LED/còi báo; không gửi 0/NaN thay số đo |
| Cảm biến hồi phục | 3 lần đọc hợp lệ liên tiếp → hết FAULT, chạy AUTO |

Ngưỡng trên chỉ cho mô hình học tập, không phải tiêu chuẩn vận hành cho mọi tủ. Khi nằm giữa hai ngưỡng quạt, giữ trạng thái trước đó (hysteresis). Kiểm tra `fan_off_c < fan_on_c`; các ngưỡng nhiệt nằm trong miền đo cảm biến, độ ẩm 0–100; telemetry interval 5–60 giây. Ngưỡng cảnh báo độc lập với ngưỡng bật quạt để có thể demo cảnh báo bằng cách thay cấu hình mà không phải làm nóng mô hình quá mức.

- Khởi động: tải cấu hình đã lưu trong Preferences, mặc định AUTO, khởi tạo đầu ra quạt theo chính sách dự phòng trước khi có số đo; sau số đo hợp lệ áp dụng AUTO.
- Mất mạng vẫn đo và điều khiển. V1 không yêu cầu lưu toàn bộ số đo để gửi bù; web thể hiện khoảng trống dữ liệu.
- Telemetry đọc lỗi: `temperature_c=null`, `humidity_pct=null`, `sensor_ok=false` và fault code; không dùng giá trị cũ như số đo mới.
- LED mạng phân biệt kết nối broker và offline; còi cảnh báo có thể chạy theo nhịp, tránh chặn vòng lặp firmware.
- Thời gian: đồng bộ NTP; ISO8601 UTC. Khi chưa đồng bộ, `measured_at=null`, `clock_synced=false`; backend vẫn lưu received_at và uptime. Không chấp nhận lệnh thủ công cần kiểm tra hết hạn khi đồng hồ chưa đồng bộ.
- Cấu hình thiết bị nhận phải được validate và lưu bền vững; ACK sau khi áp dụng/lưu thành công. Không dùng delay dài làm mất khả năng xử lý MQTT.

## 5. Vai trò và quyền

Tài khoản gồm `ADMIN`, `OPERATOR`, `VIEWER`. Quyền hiệu lực là giao của vai trò tài khoản và quyền được cấp trên thiết bị. Admin được thao tác toàn hệ thống; người còn lại chỉ thấy thiết bị được gán.

| Hành động | ADMIN | OPERATOR có OPERATE trên thiết bị | VIEWER có VIEW |
|---|---|---|---|
| Xem số đo, lịch sử, cảnh báo | Có | Có | Có |
| Gửi lệnh, đổi cấu hình | Có | Có | Không |
| Xác nhận/xử lý cảnh báo | Có | Có | Không |
| Đổi mật khẩu/thông tin bản thân | Có | Có | Có |
| Tạo/khóa tài khoản, đổi vai trò | Có | Không | Không |
| Onboarding/cấp khóa/kích hoạt thiết bị | Có | Không | Không |
| Gán thiết bị và cấp quyền | Có | Không | Không |
| Bảo trì/khóa/thu hồi/decommission | Có | Không | Không |

Operator được cấp VIEW thì chỉ xem. Viewer dù bị gán nhầm OPERATE vẫn không được điều khiển. Backend phải kiểm tra; giao diện ẩn nút chỉ phục vụ trải nghiệm.

### 5.1 Xác thực web

- Admin tạo tài khoản trong V1; không cần đăng ký công khai. Có script tạo admin đầu tiên, mật khẩu qua biến môi trường.
- JWT access token hết hạn sau 30 phút, dùng Authorization Bearer. V1 không có refresh token: hết hạn đăng nhập lại.
- Mỗi lần đăng nhập sinh `auth_sessions.jti`; mỗi API bảo vệ kiểm tra JWT, session chưa revoke, user ACTIVE và quyền hiện tại.
- Logout/khóa user/đổi mật khẩu thu hồi session tương ứng (đổi mật khẩu thu hồi mọi session, yêu cầu đăng nhập lại).
- Token chỉ giữ trong memory frontend, không đưa vào URL hay localStorage. Tải lại trang cần đăng nhập lại trong V1.
- 401 cho xác thực thiếu/sai/hết hạn; 403 cho đã xác thực nhưng thiếu quyền; 409 cho xung đột trạng thái.
- TV3 cung cấp `require_roles(...)` và `require_device_access(device_id, action)`. TV2/TV4 bắt buộc gọi các hàm này trước truy vấn nhạy cảm hoặc gửi lệnh.

## 6. Database DUY NHẤT: PostgreSQL / iot_cabinet

Quy tắc: snake_case; khóa UUID trừ device_id; mọi thời gian TIMESTAMPTZ UTC; enum triển khai bằng TEXT + CHECK để migration dễ; SQLAlchemy + Flask-Migrate là nguồn schema chuẩn. TV2 duyệt migration chung, TV3 viết migration module mình nhưng không tự tạo schema khác.

| Bảng | Trường chính cần có | Ràng buộc |
|---|---|---|
| `users` | id UUID PK; username VARCHAR(64); password_hash TEXT; full_name; role; status; created_at; updated_at | username unique, chuẩn hóa lowercase; role ADMIN/OPERATOR/VIEWER; status ACTIVE/LOCKED |
| `auth_sessions` | id UUID PK; user_id FK; jti VARCHAR(128); expires_at; revoked_at nullable; created_at | jti unique; FK users |
| `devices` | device_id VARCHAR(64) PK; name; location; owner_user_id nullable FK; lifecycle_state; connection_state; health_state; firmware_version; hardware_version; mqtt_username unique nullable; last_seen_at nullable; desired_config_version INT; applied_config_version INT; maintenance_note nullable; created_at; updated_at | Không lưu MQTT password plaintext; lifecycle REGISTERED/PROVISIONED/ACTIVE/MAINTENANCE/LOCKED/DECOMMISSIONED; connection UNKNOWN/ONLINE/OFFLINE; health OK/FAULT/UNKNOWN |
| `device_access` | id UUID PK; device_id FK; user_id FK; access_level; granted_by FK; created_at | unique(device_id,user_id); VIEW/OPERATE; chủ sở hữu được gán quyền tương ứng, không dùng ownership để bypass quyền |
| `device_configs` | id UUID PK; device_id FK; version INT; fan_on_c REAL; fan_off_c REAL; temp_alert_c REAL; humidity_alert_pct REAL; telemetry_interval_s INT; manual_timeout_s INT; updated_by FK; created_at | unique(device_id,version); immutable mỗi phiên bản; desired/applied ở devices |
| `telemetry` | id UUID PK; device_id FK; boot_id UUID; seq BIGINT; measured_at nullable; received_at; uptime_ms BIGINT; clock_synced BOOL; temperature_c nullable REAL; humidity_pct nullable REAL; sensor_ok BOOL; fan_output BOOL; mode; config_version INT | unique(device_id,boot_id,seq); mode AUTO/MANUAL; không lưu NaN/Infinity; index(device_id,received_at) |
| `device_events` | id UUID PK; device_id FK; event_type; details JSONB; created_at | Heartbeat, reboot, offline/online, fault/recovered và lifecycle; index(device_id,created_at); có thể gộp heartbeat theo khoảng để tránh log quá nhiều |
| `commands` | command_id UUID PK; device_id FK; requested_by nullable FK; type; params JSONB; config_version nullable INT; status; requested_at; expires_at; sent_at nullable; ack_at nullable; attempts INT; result JSONB nullable | type SET_MODE/SET_FAN/APPLY_CONFIG; status PENDING/SENT/APPLIED/APPLIED_LATE/REJECTED/TIMEOUT/FAILED; hệ thống re-sync config có requested_by null và audit actor SYSTEM |
| `alerts` | id UUID PK; device_id FK; type; severity; status; opened_at; last_seen_at; cleared_at nullable; acknowledged_at nullable; acknowledged_by nullable FK; resolved_at nullable; resolved_by nullable FK; observed_value nullable; threshold nullable; message | type HIGH_TEMPERATURE/HIGH_HUMIDITY/OFFLINE/SENSOR_FAULT; status OPEN/ACKNOWLEDGED/RESOLVED; chỉ một incident chưa hết điều kiện cho mỗi device/type |
| `audit_logs` | id UUID PK; actor_user_id nullable FK; actor_type; device_id nullable FK; action; target_type; target_id; success BOOL; detail JSONB; created_at | actor_type USER/SYSTEM; không ghi mật khẩu/token/secrets vào detail |

**Khóa thiết bị:** giữ lịch sử, không DELETE cascade dữ liệu đồ án. Ngừng sử dụng là trạng thái cuối; muốn dùng lại đăng ký một ID mới. Khóa tạm có thể mở lại bằng admin, còn decommission không có nút khôi phục V1.

**Dữ liệu mẫu:** admin_demo, operator_demo, viewer_demo; cabinet-001 thật, cabinet-002 chỉ bản ghi/test fixture để thử phân quyền. cabinet-002 không được quảng cáo là node vật lý thứ hai. Mật khẩu seed lấy từ .env, không hardcode lên GitHub. Dữ liệu mock gắn nhãn và không trộn với số đo thực trong báo cáo.

## 7. REST API thống nhất /api/v1

JSON dùng snake_case; thời gian UTC ISO8601, ví dụ `2026-09-30T12:00:00Z`. Thành công trả `{ "data": ... }`; danh sách thêm `meta` (page,page_size,total). Lỗi trả `{ "error": { "code": "FORBIDDEN", "message": "..." } }`. Không trả password_hash hay MQTT secret trong API đọc.

| Method + endpoint | Chức năng | Chủ trì |
|---|---|---|
| POST `/auth/login` | username/password → access_token, expires_in=1800, user | TV3 |
| POST `/auth/logout` | Revoke jti hiện tại | TV3 |
| GET `/auth/me` | Hồ sơ và vai trò | TV3 |
| PATCH `/auth/me` | Cập nhật full_name | TV3 |
| POST `/auth/change-password` | old_password/new_password; revoke sessions | TV3 |
| GET/POST `/users` | Admin xem/tạo tài khoản | TV3 |
| PATCH `/users/{id}` | Admin đổi role/status/full_name; chống tự khóa admin cuối | TV3 |
| GET/POST `/devices` | Danh sách theo quyền; admin đăng ký thiết bị | TV2 |
| GET/PATCH `/devices/{id}` | Chi tiết; admin đổi tên/vị trí/chủ và bảo trì | TV2 |
| POST `/devices/{id}/provision` | Tạo MQTT client+ACL; trả credentials MỘT LẦN | TV2 + TV3 |
| POST `/devices/{id}/activate` | Kích hoạt thiết bị đã provision | TV2 |
| GET/PUT `/devices/{id}/access` | Admin xem/thay danh sách quyền | TV3 + TV2 |
| POST `/devices/{id}/maintenance` | Ghi nhận trạng thái/note bảo trì; online có thể vẫn giám sát | TV2 |
| POST `/devices/{id}/lock` | Disable MQTT client, disconnect, chuyển LOCKED | TV2 + TV3 |
| POST `/devices/{id}/unlock` | Admin enable lại và về ACTIVE nếu chưa decommission | TV2 + TV3 |
| POST `/devices/{id}/decommission` | Revoke kết nối, ngừng sử dụng, giữ lịch sử | TV2 + TV3 |
| GET `/devices/{id}/latest` | Số đo cuối + received_at + trạng thái; đánh dấu stale khi offline | TV2 |
| GET `/devices/{id}/telemetry` | from/to/page/page_size, lọc theo received_at | TV2 |
| GET `/devices/{id}/statistics` | from/to: min/max/avg/count nhiệt/ẩm, bỏ null | TV2 |
| GET `/devices/{id}/config` | desired config + applied_version | TV2 |
| PUT `/devices/{id}/config` | Validate, tạo version, tạo APPLY_CONFIG command; không báo đã áp dụng khi mới gửi | TV2 |
| POST `/devices/{id}/commands` | SET_MODE/SET_FAN; authorize, lưu rồi dispatch | TV2 |
| GET `/devices/{id}/commands` | Lịch sử lệnh và kết quả | TV2 |
| GET `/commands/{command_id}` | Theo dõi ACK/timeout trong phạm vi thiết bị | TV2 |
| GET `/alerts` | Lọc device_id/type/status/from/to theo quyền | TV4 |
| POST `/alerts/{id}/acknowledge` | Operator/admin xác nhận cảnh báo | TV4 |
| POST `/alerts/{id}/resolve` | Chỉ resolve nếu điều kiện đã hết; chưa hết trả 409 | TV4 |
| GET `/audit-logs` | Admin toàn bộ; operator/viewer chỉ log vận hành thiết bị được phép, bỏ chi tiết quản trị tài khoản | TV3 |

**SET_MODE request:** `{ "type":"SET_MODE", "params":{"mode":"MANUAL","duration_s":60} }`.

**SET_FAN request:** `{ "type":"SET_FAN", "params":{"on":true} }`; nếu thiết bị AUTO trả 409, người dùng phải chuyển MANUAL trước. Lệnh dùng BOOL trạng thái tuyệt đối, không dùng toggle.

**Response gửi lệnh:** HTTP 202, data gồm command_id/status. UI polling tới APPLIED/REJECTED/TIMEOUT; không coi HTTP 202 là quạt đã thực hiện.

- Lệnh trực tiếp bị từ chối nếu device OFFLINE/LOCKED/DECOMMISSIONED/MAINTENANCE hoặc FAULT.
- Cấu hình có thể lưu desired khi ACTIVE nhưng offline, hiển thị pending và gửi khi online. LOCKED/DECOMMISSIONED từ chối.
- Chỉ cho cấu hình nếu ACTIVE; provision/activate không yêu cầu có telemetry trước nên không tạo vòng phụ thuộc onboarding.
- from/to ISO8601 UTC, page_size mặc định 100, tối đa 500; thống kê giới hạn khoảng thời gian hợp lý.
- Các endpoint mutation phải audit. TV2/TV4 dùng helper audit của TV3.

## 8. MQTT contract v1

Prefix cố định `cabinet/v1/devices/{device_id}`. client_id và MQTT username của node đều là device_id (ví dụ cabinet-001); mật khẩu ngẫu nhiên riêng từng node. Broker ràng buộc client_id/username.

| Topic suffix | Chiều | QoS / retained | Mục đích |
|---|---|---|---|
| `/telemetry` | ESP32 → worker | QoS 0, không retained | Số đo + đầu ra + mode |
| `/status` | ESP32 → worker | Node publish QoS 0, retained; LWT QoS 1 retained | Online/heartbeat/offline |
| `/commands` | Worker → ESP32 | QoS 1, không retained | Lệnh và apply config |
| `/acks` | ESP32 → worker | QoS 0, không retained | Xác nhận ở tầng ứng dụng |

PubSubClient publish chỉ QoS 0; không ghi firmware gửi QoS 1 nếu vẫn dùng thư viện này. ACK+retry là bảo đảm ở tầng ứng dụng; không tuyên bố exactly-once delivery. Set buffer phù hợp, tối thiểu 1024 byte cho payload V1, validate kích thước ở worker.

### 8.1 Telemetry mẫu (dữ liệu minh họa)

```json
{
  "schema_version": 1,
  "device_id": "cabinet-001",
  "boot_id": "4e5b9a32-c830-49c0-b86a-3b561a596389",
  "seq": 12,
  "measured_at": "2026-09-30T12:00:00Z",
  "clock_synced": true,
  "uptime_ms": 60000,
  "temperature_c": 29.4,
  "humidity_pct": 62.0,
  "sensor_ok": true,
  "fan_output": false,
  "mode": "AUTO",
  "config_version": 1
}
```

Đơn vị cố định bởi tên trường: °C, %RH, ms. Worker phải kiểm tra device_id trùng ID trong topic, thiết bị được ACTIVE/MAINTENANCE, schema_version, kiểu, range vật lý (-40..80°C, 0..100%RH), không NaN/Infinity. `received_at` do backend gắn, không tin device gửi. `seq` tăng với mỗi telemetry; boot_id đổi mỗi lần boot, unique key chống insert trùng.

### 8.2 Status

```json
{
  "schema_version": 1,
  "device_id": "cabinet-001",
  "connection": "ONLINE",
  "health": "OK",
  "firmware_version": "1.0.0",
  "hardware_version": "esp32-wroom32-dht22-relay5v-v1",
  "uptime_ms": 60000,
  "clock_synced": true,
  "fault_code": null
}
```

LWT tối giản: `{ "schema_version":1, "device_id":"cabinet-001", "connection":"OFFLINE", "reason":"LWT" }`. Giữ riêng last_seen_at với lúc nhận LWT; retained ONLINE cũ khi worker restart không được tự coi là heartbeat mới. Chỉ status live/telemetry live mới cập nhật last_seen_at; worker có thể nhận retained để tham khảo, sau đó chờ bản tin mới trước khi kết luận online.

### 8.3 Command và ACK

```json
{
  "schema_version": 1,
  "device_id": "cabinet-001",
  "command_id": "d5cf70aa-f944-46bf-8c2d-fd36450f3276",
  "type": "SET_FAN",
  "params": {"on": true},
  "issued_at": "2026-09-30T12:00:00Z",
  "expires_at": "2026-09-30T12:00:15Z"
}
```

```json
{
  "schema_version": 1,
  "device_id": "cabinet-001",
  "command_id": "d5cf70aa-f944-46bf-8c2d-fd36450f3276",
  "status": "APPLIED",
  "executed_at": "2026-09-30T12:00:01Z",
  "result": {"fan_output": true, "mode": "MANUAL", "config_version": 1}
}
```

- SET_MODE params gồm mode/duration_s; duration_s 10–300, mặc định 60. AUTO xóa manual timeout.
- APPLY_CONFIG params gồm config_version và toàn bộ fan_on_c/fan_off_c/temp_alert_c/humidity_alert_pct/telemetry_interval_s/manual_timeout_s. Cấu hình mới không tự chuyển sang MANUAL.
- ESP32 validate: đúng schema/id, chưa hết hạn, đồng hồ đủ tin cậy, type/params đúng; trả REJECTED + code nếu không đáp ứng.
- Worker timeout chờ ACK là 10 giây; retry cùng command_id tối đa 2 lần ở giây 3 và 6. Lệnh hết hạn 15 giây; retry không reset issued_at/expires_at.
- ESP32 cache kết quả 20 command_id gần nhất trong RAM; nhận trùng gửi lại ACK đã có, không thực hiện lại. Không retained lệnh, không gửi lại lệnh thủ công cũ khi reconnect.
- Timeout có nghĩa chưa nhận xác nhận, không chứng minh quạt chưa chạy. ACK đến muộn đổi TIMEOUT → APPLIED_LATE và lưu thời gian.
- applied config version tăng đơn điệu; config cũ không ghi đè config mới. Cùng version/nội dung có thể ACK idempotent; cùng version khác nội dung bị từ chối.
- Khi ONLINE lại, backend so desired/applied rồi tạo APPLY_CONFIG mới cho desired version hiện tại. Không đẩy lại mọi config cũ. Có sẵn cấu hình desired ban đầu version 1 khi tạo thiết bị.

### 8.4 Broker security và onboarding

1. Admin tạo device REGISTERED, ID do backend cấp dạng cabinet-001 (có unique constraint, không dựa vào số thứ tự client gửi).
2. Provision sinh mật khẩu ngẫu nhiên, tạo MQTT client disabled và role riêng; trả credential một lần qua API admin. Device chuyển PROVISIONED.
3. TV1 đưa credential vào file secrets bị gitignore/NVS; không commit. Broker lưu dạng bảo vệ của plugin; DB không lưu password plaintext.
4. Activate enable client rồi chuyển ACTIVE sau khi broker xác nhận. Nếu thao tác broker thất bại API phải báo lỗi; không chuyển trạng thái thành công giả.
5. Node chỉ publish telemetry/status/acks của chính mình và chỉ subscribe/receive commands của chính mình. Cấu hình quyền mặc định deny cho publish/subscribe/receive, sau đó cấp allow theo topic cần thiết cho từng role; không dựa vào default của plugin. Không cấp quyền wildcard toàn nhóm. Service backend và broker controller được cấp quyền riêng để vẫn hoạt động sau khi thay default.
6. Backend worker có service credential riêng; frontend KHÔNG kết nối trực tiếp MQTT và KHÔNG cầm broker admin password.
7. Broker admin credential chỉ ở backend/deployment secret. Dynamic Security controller helper do TV3 viết, TV2 gọi từ provision/lock/unlock/decommission.
8. Khóa: disable client để disconnect và ngăn reconnect; backend ngừng chấp nhận dữ liệu/lệnh. Chỉ đổi cờ DB mà broker vẫn cho kết nối là chưa đạt demo thu hồi.
9. Cấp lại credential: revoke mật khẩu cũ, hiển thị mật khẩu mới một lần; audit nhưng không ghi secret. Có thể làm sau bản cơ bản nếu không cần demo xoay khóa.

## 9. Vòng đời, cảnh báo và giao diện

### 9.1 Vòng đời

`REGISTERED → PROVISIONED → ACTIVE ↔ MAINTENANCE`; ACTIVE/MAINTENANCE có thể LOCKED; admin unlock trở ACTIVE; DECOMMISSIONED là cuối.

ONLINE/OFFLINE là trạng thái kết nối, OK/FAULT là sức khỏe; không trộn thành một trường với vòng đời. Ví dụ ACTIVE + OFFLINE + UNKNOWN, hoặc ACTIVE + ONLINE + FAULT.

### 9.2 Cảnh báo

- HIGH_TEMPERATURE/HIGH_HUMIDITY: backend dựa telemetry hợp lệ và thời gian nhận, cần vượt ngưỡng liên tục 10 giây; nếu có khoảng trống dữ liệu lớn hơn 2 chu kỳ gửi thì reset bộ đếm, không suy diễn liên tục.
- SENSOR_FAULT: firmware báo fault và worker mở incident.
- OFFLINE: timer 45 giây hoặc LWT. Node LOCKED/DECOMMISSIONED không sinh cảnh báo mất mạng mới; log là thao tác quản trị.
- Không tạo một cảnh báo mới cho từng telemetry; cập nhật incident đang mở. Khi điều kiện hết ghi cleared_at; người vận hành có thể resolve. Acknowledge chỉ ghi đã đọc, không làm hết điều kiện lỗi.
- Hạ nhiệt về dưới temp_alert_c − 1°C, độ ẩm dưới humidity_alert_pct − 3% để đánh dấu hết điều kiện; các biên này là cấu hình V1 nội bộ, ghi rõ trong report.
- Còi/LED tại node hoạt động theo số đo/cấu hình đã áp dụng, không phụ thuộc web mở. UI hiển thị nếu desired version chưa được node áp dụng.

### 9.3 Trang web tối thiểu

1. Login; hồ sơ/đổi mật khẩu.
2. Users (admin), giao diện do TV3 thực hiện.
3. Devices: tên/vị trí, lifecycle/online/fault/version, onboarding và thao tác admin.
4. Device detail: nhiệt/ẩm, thời điểm số đo, stale, đầu ra quạt, AUTO/MANUAL, biểu đồ và bảng lịch sử.
5. Configuration: desired/applied, form ngưỡng, trạng thái áp dụng.
6. Commands: kết quả ACK/timeout; nút điều khiển đúng quyền/trạng thái.
7. Alerts: lọc, acknowledge/resolve.
8. Activity: lịch sử lệnh, vòng đời, audit theo quyền.

Polling 2 giây cho latest/commands/alerts khi đang mở màn hình tương ứng; biểu đồ lịch sử tải lúc chọn khoảng thời gian, không tải toàn DB mỗi tick. Frontend không chứa SQL hay truy cập DB trực tiếp.

## 10. Phân công giữ nguyên + điểm bàn giao

| Người | Phần sở hữu | Bàn giao đầu tiên | Không tự đổi |
|---|---|---|---|
| TV1 — Thiết bị/giao thức | firmware, hardware; cùng TV2 thống nhất MQTT | Mạch đo và quạt AUTO; sau đó telemetry/status/ACK theo payload mẫu | ID, GPIO, tên field/topic, đơn vị, timing |
| TV2 — Backend thiết bị/dữ liệu | DB tổng, models thiết bị/data/config/commands/events, API thiết bị, worker telemetry/status/command | Skeleton Flask + PostgreSQL migration + seed fixture, API với data mẫu | Schema user/auth của TV3; contract auth; role rules |
| TV3 — Tài khoản/bảo mật | users/auth_sessions/device_access/audit helpers; auth API, UI Users; JWT/decorators, broker controller/ACL | Login/logout/me/password + helper permission + auth tests với fixture devices | Không dựng backend/DB độc lập; không chờ toàn TV2 xong |
| TV4 — Dashboard/cảnh báo | web ngoài Users, backend alerts module + timer, deployment, tổ chức tích hợp | Layout + mock JSON đúng contract; UI phân biệt pending/applied | Không đổi payload theo nhu cầu UI; mọi người tự sửa phần mình |

**Các việc chung phải phối hợp:**
- TV2 tạo app factory, DB object chung và migration setup. TV3 thêm Blueprint auth/users, dùng cùng db/session, không tạo app riêng chỉ chạy tạm rồi ghép thủ công.
- TV2 cung cấp device fixture cabinet-001/002 và models ngay buổi đầu; TV3 kiểm thử quyền trên đó bằng Flask test client. Chưa có MQTT/quạt vẫn làm auth và role được.
- TV3 bàn giao decorator/service permission, helper audit và interface `provision_client`, `enable_client`, `disable_client`. TV2 gọi chúng; TV4 gọi permission/audit cho alerts.
- TV4 đưa frontend app/route chuẩn; TV3 làm Users/Profile trên nhánh riêng cùng codebase. Login UI phối hợp TV3/TV4, không tạo hai phiên bản.
- MQTT worker một instance, không chạy mỗi Flask web worker một subscriber. Flask viết commands/outbox vào DB; worker polling PENDING mỗi 0,5 giây, dispatch và cập nhật ACK. Không phụ thuộc frontend còn mở.
- Alert worker một instance; TV4 viết service dùng models/query của TV2 và gọi từ worker. Không tạo cấu trúc data store riêng.
- TV1/TV2 xác nhận từng mẫu payload bằng MQTT Explorer và log; TV3 sau đó bật ACL và kiểm thử; TV4 ghép UI với dữ liệu thật.

### 10.1 GitHub

```text
README.md
.env.example
.gitignore
firmware/
backend/
  app/
    __init__.py
    extensions.py
    models/
    auth/
    users/
    permissions/
    devices/
    telemetry/
    commands/
    alerts/
    audit/
    mqtt/
  migrations/
  requirements.txt
frontend/
database/
deployment/
hardware/
tests/
docs/
```

`database/` giữ ERD, data dictionary và hướng dẫn seed; migration chạy thật ở backend/migrations, không giữ hai schema SQL mâu thuẫn. Mỗi người commit bằng tài khoản của mình, nhánh theo chức năng, PR có người khác review; release cuối v1.0.0. Thay contract phải cập nhật tài liệu và báo những người liên quan trước merge.

## 11. Tài liệu cần có trước và trong triển khai

### 11.1 Trước khi làm song song (1–2 buổi đầu)

- Đặc tả chung này: phạm vi, BOM/pinout, công nghệ, schema, API, MQTT, roles, thời gian, phân công.
- ERD nháp và migration nền; payload JSON mẫu và API response mẫu.
- README chạy backend/frontend/DB/broker; .env.example không chứa secret thật.
- Ma trận quyền và dữ liệu kiểm thử chung; nhãn rõ mock/real.
- Danh sách task GitHub và tiêu chí nhận bàn giao từng task.

### 11.2 Phải hoàn thành trước nộp theo đề thầy

| Tài liệu | Người viết chính |
|---|---|
| Bài toán, đối tượng, yêu cầu chức năng/phi chức năng, chi phí/mạng/năng lượng và lý do IoT | Cả nhóm; TV4 tập hợp |
| Context, use case, kiến trúc tổng thể/phân lớp và deployment | TV2 + TV4 |
| Sơ đồ khối, nối dây/nguồn, BOM và ảnh thật | TV1 |
| MQTT topic/payload/QoS/retry/timing | TV1 + TV2 |
| ERD/data dictionary/migration; API | TV2, TV3 viết phần auth |
| Ít nhất 2 sequence: telemetry → dashboard; lệnh → ACK | TV2 + TV4, TV1 kiểm tra |
| State diagram vòng đời và online/offline riêng | TV2 |
| Role matrix, threat model, kiểm soát bảo mật/quyền riêng tư | TV3 |
| Test plan, kết quả/bằng chứng, số đo độ trễ và nhận xét | Mỗi người phần mình; TV4 tập hợp |
| Báo cáo 20–30 trang khuyến nghị, slide 8–12 trang khuyến nghị, video dự phòng | Mỗi người viết phần mình; không giao một người viết hết |
| Hướng dẫn chạy từ máy sạch, GitHub release, khai báo tài liệu/thư viện/AI hỗ trợ | Cả nhóm |

Không cần mọi sơ đồ hoàn chỉnh ngay ngày đầu; contract phải chốt trước, sơ đồ và báo cáo cập nhật song song với triển khai. Không lấy báo cáo tham khảo làm bằng chứng kiểm thử của nhóm.

## 12. Mốc làm việc và kiểm thử

### 12.1 Mốc theo tuần tính từ lúc bắt đầu

| Mốc | Điều kiện xong |
|---|---|
| Buổi 1–2 | Repo chung, Compose DB, Flask skeleton, schema/API/MQTT/roles chốt; TV1 xác minh bo và module |
| Tuần 1 | DHT22 thật + quạt AUTO; auth chạy; API nhận/lưu telemetry; web mock đúng contract |
| Tuần 2 | Luồng dữ liệu thật đến dashboard; MANUAL/SET_FAN/config có ACK; Users/Devices UI |
| Tuần 3 | Onboarding/lifecycle/ACL/thu hồi, alerts/history; offline/reconnect, fault và command timeout |
| Tuần 4 | Test đầy đủ, hộp demo, đo độ trễ, tài liệu/slide, tập demo |

Mục tiêu có V1 trước đầu tháng 11 để sẵn sàng lịch thuyết trình trong PDF. Hạn báo cáo/source ghi trong PDF: 24/11/2026; demo 25/11 và 02/12. Theo thông báo mới của thầy nếu có thay đổi.

### 12.2 Demo bắt buộc 10 bước

1. Login các vai trò, chứng minh quyền khác nhau.
2. Đăng ký/provision/kích hoạt device.
3. ESP32 gửi nhiệt/ẩm thật.
4. Dashboard cập nhật, có timestamp và lịch sử.
5. MANUAL bật/tắt quạt, thấy ACK và command history.
6. Đổi config; applied version đổi; AUTO phản ứng theo ngưỡng.
7. Ngắt mạng, offline; node AUTO; nối lại và recovery.
8. Tạo cảnh báo bằng ngưỡng thử thấp hơn số đo thật; acknowledge; hết điều kiện rồi resolve.
9. Viewer gọi API điều khiển trực tiếp bị 403; không chỉ ẩn nút.
10. Lock/decommission, broker disconnect và từ chối reconnect; lịch sử còn nguyên.

### 12.3 Kiểm thử tối thiểu

- Đăng nhập đúng/sai, logout xong token cũ bị từ chối; tài khoản khóa bị từ chối.
- Viewer không điều khiển; operator không đọc/điều khiển device không được giao.
- MQTT node không publish/subscribe topic device khác; device khóa không reconnect được.
- Telemetry sai schema/range/ID/NaN bị từ chối; null lỗi cảm biến được xử lý đúng.
- Telemetry trùng unique key không tạo bản ghi mới; command trùng không thực thi lại.
- ACK lỗi/timeout/late ACK không hiển thị thành công giả.
- Mất mạng >45 giây, hồi phục mạng, giữ AUTO/config; worker restart không tin retained online cũ.
- Chạy ổn định tối thiểu 30 phút; thử nhiều lần on/off để phát hiện reset nguồn; ghi reboot/fault.
- Đo ít nhất 20 lượt độ trễ command từ requested_at tới ack_at; báo median/p95 và phương pháp. Đây là độ trễ xác nhận backend, không đo tốc độ cơ học của quạt.
- Độ trễ sensor→web: nếu clock đồng bộ, measured_at→received_at và thời điểm UI hiển thị; ghi sai số đồng hồ và ảnh hưởng polling. Không so hai đồng hồ chưa đồng bộ.
- Đối chiếu DHT với nhiệt kế ở vài điều kiện ổn định; báo độ lệch, không gọi là hiệu chuẩn chuẩn đo lường.
- Nếu thử tải bằng simulator, ghi rõ số node giả/nhịp gửi; không thay thế node thật.

## 13. Nguồn tham khảo để nhóm tự kiểm tra

- Đề bài đồ án kết thúc học phần PDF người dùng đã cung cấp: nguồn chuẩn cho yêu cầu bắt buộc.
- ESP32IO phần đo/relay/quạt: https://esp32io.com/tutorials/esp32-cooling-system-using-dht-sensor (kiểm tra lại pinMode output, nguồn và mức kích relay; không chép nguyên firmware).
- Mosquitto Dynamic Security: https://mosquitto.org/documentation/dynamic-security/ (disabled client bị disconnect và không reconnect; plugin cần được bật/cấu hình).
- PubSubClient: https://github.com/knolleary/pubsubclient (giới hạn QoS publish; packet buffer).
- Flask Blueprints: https://flask.palletsprojects.com/en/stable/blueprints/
- Flask testing: https://flask.palletsprojects.com/en/stable/testing/
- Wokwi: https://docs.wokwi.com/parts/wokwi-dht22 và https://docs.wokwi.com/guides/esp32-wifi (gateway công cộng không truy cập LAN laptop).
- Báo cáo PTIT tham khảo thiết kế: https://www.studocu.vn/vn/document/hoc-vien-cong-nghe-buu-chinh-vien-thong/iot-va-ung-dung/bao-cao-lon-hoc-phan-iot-va-ung-dung-b23dcat078/172887010

**Quy tắc chốt:** ưu tiên đủ yêu cầu và chạy ổn định; không thêm AI/OTA/PWM/app Kotlin trước khi V1 đạt. Không thay DB/giao thức/schema giữa chừng mà không cập nhật hợp đồng và thống nhất cả nhóm.
