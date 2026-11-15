#ifndef CONFIG_H
#define CONFIG_H

// Định danh thiết bị và phiên bản phần cứng/firmware 
#define DEVICE_ID           "cabinet-001"
#define DEVICE_PROFILE      "CONTROLLER"
#define FIRMWARE_VERSION    "1.2.0"
#define HARDWARE_VERSION    "esp32-wroom32-dht22-relay-controller-v1"

// Cấu hình mạng Wi-Fi (2.4 GHz)
#define WIFI_SSID           "YOUR_WIFI_SSID"
#define WIFI_PASSWORD       "YOUR_WIFI_PASSWORD"

// Cấu hình MQTT Broker (Dùng IP LAN của laptop chạy Docker Mosquitto)
#define MQTT_HOST           "192.168.1.100"
#define MQTT_PORT           1883
#define MQTT_USERNAME       "cabinet-001"
#define MQTT_PASSWORD       "YOUR_PROVISIONED_SECRET"

// Phân bổ chân GPIO
#define PIN_DHT             23  // Chân DATA của DHT22
#define PIN_RELAY           18  // Điều khiển Relay quạt (HIGH = ON, LOW = OFF)
#define PIN_LED_NET         19  // LED xanh báo kết nối MQTT sẵn sàng
#define PIN_LED_FAULT       25  // LED đỏ báo lỗi cảm biến (FAULT)
#define PIN_DEMAND_MIRROR   27  // (Dự phòng P1) Xuất trạng thái yêu cầu quạt sang Node 2
#define PIN_ALIVE_HEARTBEAT 32  // (Dự phòng P1) Đảo mức mỗi 500ms báo vòng lặp điều khiển sống

#define RELAY_ON            HIGH
#define RELAY_OFF           LOW

// Tham số chu kỳ hoạt động cố định
#define DHT_SAMPLE_MS       2100UL   // Chu kỳ đọc DHT22: 2.1 giây
#define STATUS_INTERVAL_MS  15000UL  // Chu kỳ gửi heartbeat status: 15 giây
#define ALIVE_TOGGLE_MS     500UL    // Chu kỳ đảo chân alive: 500 ms
#define MQTT_BUFFER_SIZE    2048     // Bộ đệm gói tin MQTT 2048 byte

#endif