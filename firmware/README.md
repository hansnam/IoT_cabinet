# Firmware Node Điều Khiển Tủ Thiết Bị (cabinet-001)

Thư mục này chứa mã nguồn nhúng (Firmware) cho bo mạch ESP32-WROOM-32 đóng vai trò CONTROLLER trong giai đoạn P0.

## 1. Chức năng chính
- Đọc nhiệt độ, độ ẩm từ cảm biến DHT22 tại GPIO23 theo chu kỳ 2,1 giây.
- Điều khiển quạt thông gió qua Relay tại GPIO18 theo chế độ tự động AUTO (ngưỡng trễ hysteresis mặc định: bật ở 30^C, tắt ở 28^C) hoặc chế độ cưỡng bức MANUAL có thời hạn (TTL).
- Tự động chuyển sang trạng thái an toàn FAULT (bật quạt, sáng LED đỏ GPIO25, gửi số đo null) khi cảm biến lỗi 3 lần liên tiếp và tự phục hồi khi có 3 mẫu hợp lệ liên tiếp.
- Giao tiếp MQTT hai chiều theo hợp đồng v3 (cabinet/v3/devices/cabinet-001/...) với bộ đệm 2048 byte, lưu cấu hình bền vững trong NVS và chống trùng lặp lệnh.

## 2. Hướng dẫn cài đặt và nạp code
1. Sao chép file cấu hình mẫu:
   bash
   cp include/config.example.h include/config.h
   
     1. Cập nhật WIFI_SSID, WIFI_PASSWORD, MQTT_HOST (IP LAN của máy chạy Mosquitto Broker) và MQTT_PASSWORD được cấp khi provision thiết bị
     2. Biên dịch và nạp qua PlatformIO:
          pio run --target upload
          pio device monitor -b 115200
