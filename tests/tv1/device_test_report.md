> **Phạm vi:** Kiểm thử cảm biến DHT22, điều khiển quạt, xử lý lỗi, kết nối mạng và cấu hình thiết bị ESP32  
> **Node:** `cabinet-001`  
> **Số lượng kịch bản:** 05

## 1. Tổng quan kết quả kiểm thử

Tài liệu mô tả các kịch bản kiểm thử phần cứng và firmware của hệ thống TV1. Mỗi kịch bản xác định điều kiện thực hiện, trình tự thao tác và kết quả cần quan sát.

| Mã ca | Kịch bản kiểm thử | Nội dung kiểm thử chính | Trạng thái |
|:---:|---|---|:---:|
| **HW-01** | Đọc cảm biến và điều khiển AUTO hysteresis | Kiểm tra ngưỡng bật/tắt quạt theo nhiệt độ | **PASS** |
| **HW-02** | Điều khiển quạt MANUAL và hết hạn TTL | Kiểm tra lệnh điều khiển thủ công và thời hạn hiệu lực | **PASS** |
| **HW-03** | Phát hiện lỗi cảm biến và tự phục hồi | Kiểm tra trạng thái `FAULT`, cảnh báo và cơ chế phục hồi | **PASS** |
| **HW-04** | Xử lý mất mạng và kết nối lại | Kiểm tra khả năng điều khiển tại chỗ khi mất kết nối | **PASS** |
| **HW-05** | Áp dụng cấu hình và lưu NVS | Kiểm tra xác thực cấu hình và khả năng lưu sau khởi động lại | **PASS** |

## 2. Chi tiết các kịch bản kiểm thử

### HW-01 - Đọc cảm biến và điều khiển AUTO hysteresis

| Thuộc tính | Nội dung |
|---|---|
| **Mục tiêu** | Xác minh ESP32 đọc được nhiệt độ từ DHT22 và điều khiển quạt tự động theo cơ chế hysteresis. |
| **Các bước thực hiện** | 1. Tăng nhiệt độ DHT22 từ `29 °C` lên `30,5 °C`.<br>2. Giảm nhiệt độ về `29 °C`.<br>3. Tiếp tục giảm nhiệt độ xuống `27,8 °C`. |
| **Kết quả mong đợi** | - Quạt bật khi nhiệt độ đạt hoặc vượt `30 °C`.<br>- Quạt tiếp tục bật khi nhiệt độ giảm về `29 °C`.<br>- Quạt tắt khi nhiệt độ giảm xuống mức `28 °C` hoặc thấp hơn.<br>- Thiết bị gửi bản tin telemetry theo chu kỳ 5 giây. |
| **Trạng thái** | **PASS** |

### HW-02 - Điều khiển quạt thủ công (MANUAL) và tự hết hạn TTL

| Thuộc tính | Nội dung |
|---|---|
| **Mục tiêu** | Kiểm tra khả năng điều khiển quạt thủ công trong một khoảng thời gian xác định và tự động quay về chế độ AUTO khi TTL hết hạn. |
| **Các bước thực hiện** | 1. Gửi lệnh `SET_MODE` với `mode=MANUAL` và `duration_s=60`.<br>2. Gửi lệnh `SET_FAN` với `on=true`.<br>3. Chờ quá 60 giây để TTL hết hạn. |
| **Kết quả mong đợi** | - Relay đóng tiếp điểm, cấp nguồn cho quạt hoạt động.<br>- Thiết bị gửi ACK với trạng thái `APPLIED` cho lệnh được áp dụng.<br>- Khi TTL hết hạn, thiết bị tự động chuyển về chế độ `AUTO`. |
| **Trạng thái** | **PASS** |

### HW-03 - Phát hiện lỗi cảm biến (FAULT) và tự phục hồi

| Thuộc tính | Nội dung |
|---|---|
| **Mục tiêu** | Xác minh hệ thống phát hiện lỗi đọc DHT22, chuyển sang trạng thái an toàn và tự phục hồi khi cảm biến hoạt động lại. |
| **Các bước thực hiện** | 1. Ngắt dây `DATA` tại chân `GPIO23` trong hơn 3 chu kỳ đọc (xấp xỉ 6,5 giây).<br>2. Quan sát LED đỏ và trạng thái hoạt động của quạt.<br>3. Nối lại dây `DATA` và chờ cảm biến trả về 3 mẫu hợp lệ liên tiếp. |
| **Kết quả mong đợi** | - LED đỏ tại `GPIO25` sáng để báo lỗi cảm biến.<br>- Hệ thống ép bật quạt nhằm đưa thiết bị về trạng thái an toàn.<br>- Telemetry chứa `temperature_c: null` và `sensor_ok: false` khi dữ liệu cảm biến không hợp lệ.<br>- Sau khi nhận được 3 mẫu đo hợp lệ liên tiếp, hệ thống tự phục hồi và quay về chế độ `AUTO`. |
| **Trạng thái** | **PASS** |

### HW-04 - Xử lý mất mạng và tự động kết nối lại

| Thuộc tính | Nội dung |
|---|---|
| **Mục tiêu** | Kiểm tra khả năng duy trì điều khiển cục bộ khi Wi-Fi hoặc MQTT Broker bị gián đoạn, đồng thời xác minh thiết bị có thể kết nối lại sau khi mạng phục hồi. |
| **Các bước thực hiện** | 1. Đưa thiết bị vào chế độ `MANUAL OFF`.<br>2. Ngắt kết nối Wi-Fi hoặc dừng MQTT Broker.<br>3. Tăng nhiệt độ DHT22 lên `31 °C`.<br>4. Khởi động lại MQTT Broker hoặc khôi phục kết nối Wi-Fi. |
| **Kết quả mong đợi** | - LED xanh tại `GPIO19` tắt khi mất kết nối MQTT.<br>- Thiết bị hủy chế độ `MANUAL`, chuyển về `AUTO` ngay khi phát hiện mất kết nối theo thiết kế firmware.<br>- Bộ điều khiển cục bộ tiếp tục hoạt động và bật quạt khi nhiệt độ đạt ngưỡng `30 °C`.<br>- Khi kết nối được khôi phục, ESP32 tự động reconnect và gửi bản tin `/status` với trạng thái `ONLINE`. |
| **Trạng thái** | **PASS** |

### HW-05 - Áp dụng cấu hình mới và lưu bền vững trong NVS

| Thuộc tính | Nội dung |
|---|---|
| **Mục tiêu** | Xác minh thiết bị kiểm tra tính hợp lệ của cấu hình, lưu cấu hình mới vào NVS trước khi xác nhận và giữ nguyên cấu hình sau khi khởi động lại. |
| **Các bước thực hiện** | 1. Gửi lệnh `APPLY_CONFIG` với `config_version=2` và `content_hash` hợp lệ.<br>2. Theo dõi quá trình kiểm tra SHA-256, ghi cấu hình vào NVS và phản hồi ACK.<br>3. Nhấn nút Reset trên ESP32 để khởi động lại thiết bị.<br>4. Kiểm tra phiên bản cấu hình sau khi thiết bị khởi động hoàn tất. |
| **Kết quả mong đợi** | - Thiết bị xác minh SHA-256 của cấu hình trước khi chấp nhận áp dụng.<br>- Cấu hình được lưu thành công vào NVS trước khi thiết bị gửi ACK `APPLIED`.<br>- Sau khi Reset và khởi động lại, thiết bị vẫn sử dụng cấu hình phiên bản `v2`. |
| **Trạng thái** | **PASS** |

