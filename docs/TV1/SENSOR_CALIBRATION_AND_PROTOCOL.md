Tài liệu mô tả cách đọc và kiểm tra dữ liệu từ cảm biến DHT22, phương pháp đối chiếu/hiệu chỉnh số đo, các giới hạn vận hành của mô hình TV1 và hợp đồng trao đổi dữ liệu MQTT giữa Node và Worker.
Phạm vi P0: Mô hình hiện tại sử dụng cảm biến nhiệt độ/độ ẩm DHT22 (AM2302), ESP32 và quạt DC 5V điều khiển qua relay. Các nội dung về cảm biến khí MQ-3 chỉ là ghi chú tham khảo, không thuộc cấu hình P0 đang triển khai.

Mục lục
1. Đơn vị đo và khung dữ liệu cảm biến
2. Kiểm tra dữ liệu và hiệu chỉnh cảm biến
3. Giới hạn kỹ thuật của mô hình
4. Hợp đồng giao thức MQTT v3
5. Lưu ý triển khai và kiểm thử
1. Đơn vị đo và khung dữ liệu cảm biến
1.1. Các đại lượng đo
Trường dữ liệu	Đại lượng	Đơn vị	Phạm vi đo	Độ phân giải
temperature_c	Nhiệt độ không khí	°C	-40 đến 80 °C	0,1 °C
humidity_pct	Độ ẩm tương đối	%RH	0 đến 100 %RH	0,1 %RH


Lưu ý: Độ phân giải là bước thay đổi nhỏ nhất mà cảm biến có thể biểu diễn; không đồng nghĩa với độ chính xác của phép đo. Theo thông số linh kiện, DHT22 có sai số điển hình khoảng ±0,5 °C đối với nhiệt độ và ±2 %RH đối với độ ẩm.
1.2. Cấu trúc khung dữ liệu DHT22
Mỗi lần đọc, DHT22 truyền một khung dữ liệu gồm 40 bit (5 byte):
Byte	Nội dung	Mô tả
1	Độ ẩm - byte cao	Phần nguyên của độ ẩm
2	Độ ẩm - byte thấp	Phần thập phân của độ ẩm
3	Nhiệt độ - byte cao	Phần nguyên và bit dấu của nhiệt độ
4	Nhiệt độ - byte thấp	Phần thập phân của nhiệt độ
5	Checksum	Byte kiểm tra tính toàn vẹn dữ liệu


Checksum được tính từ tổng 8 bit thấp của bốn byte dữ liệu đầu tiên. Nếu checksum không hợp lệ, mẫu đo phải được xem là lỗi và không được sử dụng làm dữ liệu vận hành.
2. Kiểm tra dữ liệu và hiệu chỉnh cảm biến
2.1. Điều kiện chấp nhận mẫu đo
Firmware chỉ chấp nhận một mẫu đo khi đáp ứng đầy đủ các điều kiện sau:
1. Thư viện DHTesp đọc cảm biến thành công và không báo lỗi checksum.
2. Giá trị nhiệt độ và độ ẩm là số hữu hạn (isfinite).
3. Giá trị nằm trong miền đo hợp lệ của DHT22.
Đại lượng	Điều kiện hợp lệ
Nhiệt độ (temperature_c)	-40 ≤ T ≤ 80 °C
Độ ẩm (humidity_pct)	0 ≤ RH ≤ 100 %RH


Khi đọc cảm biến thất bại hoặc dữ liệu không hợp lệ:
- Firmware đánh dấu trạng thái cảm biến là lỗi (sensor_ok: false).
- Giá trị đo tương ứng được gửi dưới dạng null trong JSON.
- Không thay dữ liệu lỗi bằng số 0, vì 0 có thể bị hiểu nhầm là một phép đo hợp lệ.
2.2. Quy trình đối chiếu và hiệu chỉnh (Calibration)
Để đánh giá độ lệch của DHT22, thực hiện đối chiếu với một nhiệt ẩm kế tham chiếu:
1. Đặt DHT22 và thiết bị tham chiếu trong cùng một không gian, gần nhau và tránh luồng khí nóng/lạnh thổi trực tiếp.
2. Chờ môi trường và hai thiết bị ổn định.
3. Thu thập ít nhất 30 cặp mẫu đo trong điều kiện tương đối ổn định.
4. Tính độ lệch trung bình giữa cảm biến và thiết bị tham chiếu.
Với nhiệt độ:
\[
\Delta T = T_{sensor} - T_{reference}
\]
Giá trị nhiệt độ sau bù sai lệch:
\[
T_{corrected} = T_{raw} - \Delta T
\]
Trong đó:
- T_sensor: nhiệt độ DHT22 đo được.
- T_reference: nhiệt độ từ thiết bị tham chiếu.
- ΔT: độ lệch trung bình của cảm biến so với thiết bị tham chiếu.
- T_corrected: nhiệt độ sau khi firmware bù offset.
Có thể áp dụng cách tính tương tự cho độ ẩm nếu quá trình đối chiếu cho thấy độ lệch ổn định và có đủ cơ sở hiệu chỉnh.
Phân biệt hai thao tác: Bù offset nhằm cải thiện giá trị đo được; thay đổi fan_on_c hoặc fan_off_c chỉ điều chỉnh ngưỡng bật/tắt quạt. Thay đổi ngưỡng điều khiển không có nghĩa là đã hiệu chuẩn lại cảm biến.

Các ngưỡng điều khiển có thể được cập nhật thông qua lệnh APPLY_CONFIG theo thiết kế firmware.
2.3. Ghi chú tham khảo: cảm biến khí tương tự MQ-3
Phần này không thuộc cấu hình P0 đang sử dụng DHT22. Nội dung chỉ áp dụng khi hệ thống được mở rộng bằng cảm biến khí có đầu ra analog, chẳng hạn MQ-3.

Với mạch phân áp có cách mắc phù hợp, điện áp đầu ra V_out được dùng để ước lượng điện trở cảm biến:
\[
R_s = R_L \times \frac{V_{cc} - V_{out}}{V_{out}}
\]
Trong đó:
- R_s: điện trở cảm biến tại điều kiện đo.
- R_L: điện trở tải của mạch.
- V_cc: điện áp cấp cho mạch cảm biến.
- V_out: điện áp tại điểm đọc ADC.
Sau đó, cần xác định điện trở chuẩn R_0 theo quy trình của nhà sản xuất và sử dụng tỷ số R_s/R_0 cùng đường đặc tuyến trong datasheet để ước lượng nồng độ khí. Việc quy đổi sang ppm hoặc mg/L phụ thuộc loại khí, điều kiện hiệu chuẩn và đặc tuyến của đúng cảm biến; không nên áp dụng một hệ số quy đổi chung.

Khi triển khai MQ-3 cần lưu ý thời gian làm nóng (preheat), đặc tính ADC của ESP32, cách mắc mạch phân áp và ảnh hưởng của nhiệt độ/độ ẩm môi trường.
3. Giới hạn kỹ thuật của mô hình
Hạng mục	Giới hạn / yêu cầu	Ý nghĩa khi vận hành
Chu kỳ đọc DHT22	Lớn hơn 2 giây; firmware đặt 2,1 giây	Tránh đọc cảm biến quá nhanh so với thời gian đáp ứng/khuyến nghị của DHT22
Vị trí lắp cảm biến	Đặt tại vị trí đại diện cho không khí trong tủ	Tránh sát linh kiện tỏa nhiệt cục bộ hoặc ngay trước luồng gió của quạt
Phản hồi quạt ở P0	Quạt DC 5V hai dây, đóng/ngắt qua relay	Chưa có tín hiệu xác nhận tốc độ quay thực tế
Trạng thái fan_output	Phản ánh trạng thái đầu ra điều khiển relay	true nghĩa là firmware đang yêu cầu relay đóng; không chứng minh cánh quạt đang quay
Giám sát RPM	Chưa triển khai trong P0	Có thể bổ sung ở P1 bằng quạt có dây TACH hoặc cảm biến tốc độ phù hợp


Điểm cần phân biệt: fan_output: true xác nhận trạng thái điều khiển đầu ra theo logic firmware, không phải phép đo phản hồi vật lý của quạt. Muốn xác nhận quạt đang quay, hệ thống cần thêm phần cứng phản hồi như tín hiệu TACH và logic đọc RPM.
4. Hợp đồng giao thức MQTT v3
4.1. Quy ước topic
Các bản tin của thiết bị sử dụng tiền tố topic:
cabinet/v3/devices/{device_id}
Trong đó {device_id} là mã định danh thiết bị, ví dụ cabinet-001.
Topic đầy đủ	Chiều truyền	QoS	Retained	Tần suất / mục đích
cabinet/v3/devices/{device_id}/telemetry	Node → Worker	0	Không nêu trong cấu hình	Gửi dữ liệu đo, chu kỳ 5 giây
cabinet/v3/devices/{device_id}/status	Node → Worker	0	Có	Cập nhật trạng thái và sức khỏe, chu kỳ heartbeat 15 giây
cabinet/v3/devices/{device_id}/status - bản tin LWT	Broker → Subscriber	1	Có	Broker phát khi phát hiện Node mất kết nối bất thường
cabinet/v3/devices/{device_id}/commands	Worker → Node	1	Không nêu trong cấu hình	Worker gửi lệnh điều khiển/cấu hình
cabinet/v3/devices/{device_id}/acks	Node → Worker	0	Không nêu trong cấu hình	Node phản hồi kết quả xử lý lệnh


Lưu ý về LWT (Last Will and Testament): LWT không phải bản tin heartbeat được gửi định kỳ. Node đăng ký nội dung LWT với MQTT Broker khi kết nối; Broker sẽ phát bản tin này khi kết nối của Node bị mất bất thường. Heartbeat trạng thái ONLINE được Node gửi định kỳ theo cấu hình riêng.

4.2. Bản tin Telemetry
Chiều truyền: Node → Worker
QoS: 0
Chu kỳ gửi: 5 giây

Các trường dữ liệu được mô tả trong hợp đồng:
Trường	Ý nghĩa
temperature_c	Nhiệt độ đo được, đơn vị °C; có thể là null khi lỗi đọc
humidity_pct	Độ ẩm tương đối, đơn vị %RH; có thể là null khi lỗi đọc
sensor_ok	Trạng thái hợp lệ của cảm biến
fan_output	Trạng thái đầu ra điều khiển relay/quạt
mode	Chế độ vận hành hiện tại
seq	Số thứ tự bản tin telemetry
boot_id	Định danh lần khởi động của Node
connection_id	Định danh phiên kết nối
config_version	Phiên bản cấu hình đang áp dụng
config_hash	Giá trị băm dùng nhận diện/đối chiếu cấu hình


4.3. Bản tin Status và LWT
Bản tin Status định kỳ:
- Chiều truyền: Node → Worker.
- QoS 0, retained.
- Chu kỳ heartbeat: 15 giây.
- Các trạng thái được sử dụng: ONLINE và OFFLINE.
- Trường health biểu diễn tình trạng thiết bị: OK hoặc FAULT.
- status_seq dùng để theo dõi thứ tự bản tin trạng thái.
Last Will and Testament (LWT):
- Được Node đăng ký với Broker khi thiết lập kết nối MQTT.
- Theo cấu hình hiện tại: QoS 1, retained.
- Broker phát bản tin LWT khi phát hiện kết nối Node bị ngắt bất thường.
- Subscriber có thể nhận trạng thái retained gần nhất khi đăng ký theo dõi topic.
4.4. Bản tin Commands
Chiều truyền: Worker → Node
QoS: 1

Node hỗ trợ các loại lệnh sau:
Lệnh	Chức năng
SET_MODE	Yêu cầu thay đổi chế độ vận hành
SET_FAN	Yêu cầu điều khiển trạng thái quạt
APPLY_CONFIG	Yêu cầu áp dụng cấu hình mới, ví dụ các ngưỡng điều khiển


Chi tiết cấu trúc JSON, kiểu dữ liệu, trường bắt buộc và quy tắc xác thực của từng lệnh cần tuân theo schema được triển khai trong firmware/Worker.
4.5. Bản tin Acknowledgement (ACK)
Chiều truyền: Node → Worker
QoS: 0

Sau khi xử lý lệnh, Node phản hồi một trong hai kết quả:
Kết quả	Ý nghĩa
APPLIED	Lệnh hợp lệ và đã được áp dụng
REJECTED	Lệnh bị từ chối, không được áp dụng


Bản tin ACK cần chứa thông tin đối chiếu:
- command_id: ID của lệnh mà Node đang phản hồi.
- device_command_seq: số thứ tự lệnh phía thiết bị.
Worker sử dụng các trường này để ghép ACK với lệnh tương ứng và theo dõi trình tự xử lý.
5. Lưu ý triển khai và kiểm thử
Trước khi nghiệm thu, nên kiểm tra tối thiểu các trường hợp sau:
- [ ] DHT22 trả về số đo hợp lệ trong điều kiện nhiệt độ/độ ẩm bình thường.
- [ ] Khi ngắt dây DATA hoặc làm cảm biến không phản hồi, firmware đánh dấu lỗi và gửi null, không gửi 0 thay thế.
- [ ] Khoảng thời gian giữa hai lần đọc DHT22 không nhỏ hơn giới hạn đã đặt (2,1 giây trong firmware).
- [ ] Khi đạt điều kiện điều khiển, fan_output phản ánh đúng trạng thái relay được yêu cầu.
- [ ] Telemetry được phát theo chu kỳ 5 giây và Status heartbeat theo chu kỳ 15 giây.
- [ ] Worker nhận được các lệnh SET_MODE, SET_FAN, APPLY_CONFIG trên đúng topic.
- [ ] Node trả ACK APPLIED hoặc REJECTED với command_id và device_command_seq tương ứng.
- [ ] Kiểm tra LWT bằng cách ngắt kết nối Node bất thường và xác nhận Broker phát bản tin trạng thái đã đăng ký.
