#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <DHTesp.h>
#include <mbedtls/sha256.h>
#include <time.h>

#if __has_include("config.h")
  #include "config.h"
#else
  #include "config.example.h"
#endif

// Khởi tạo đối tượng phần cứng & dịch vụ
DHTesp dht;
WiFiClient espClient;
PubSubClient mqttClient(espClient);
Preferences prefs;

// Cấu trúc cấu hình lưu trong NVS
struct DeviceConfig {
  int config_version = 1;
  String config_hash = "3520a5738ade36fecf0a3f72ecf34e9f53fb2b2fe23e0a20245393e6a57a91cb";
  String fan_binding_id = ""; // null ở P0
  float fan_on_c = 30.0f;
  float fan_off_c = 28.0f;
  float temp_alert_c = 35.0f;
  float humidity_alert_pct = 80.0f;
  float temp_clear_margin_c = 1.0f;
  float humidity_clear_margin_pct = 3.0f;
  uint32_t telemetry_interval_ms = 5000;
  uint32_t manual_timeout_s = 60;
} currentConfig;

// Biến trạng thái thiết bị
String bootId = "";
String connectionId = "";
uint64_t telemetrySeq = 0;
uint64_t statusSeq = 0;
uint64_t highWaterCommandSeq = 0;

bool sensorOk = false;
int consecutiveErrors = 0;
int consecutiveValid = 0;
bool isFault = false;
String faultCode = "";

float lastTempC = NAN;
float lastHumidityPct = NAN;

String currentMode = "AUTO"; // "AUTO" hoặc "MANUAL"
bool fanOutput = false;
bool manualFanTarget = false;
unsigned long manualExpireAtMs = 0;

// Các mốc thời gian 
unsigned long lastDhtSampleMs = 0;
unsigned long lastTelemetryMs = 0;
unsigned long lastStatusMs = 0;
unsigned long lastAliveToggleMs = 0;
unsigned long lastReconnectAttemptMs = 0;
bool alivePinState = false;

// Bộ nhớ đệm 20 ACK gần nhất chống trùng lặp
struct AckCacheEntry {
  String command_id;
  uint64_t command_seq;
  String type;
  String status;
  bool valid = false;
};
AckCacheEntry ackCache[20];
int ackCacheIndex = 0;

// Topic MQTT v3
String topicTelemetry = String("cabinet/v3/devices/") + DEVICE_ID + "/telemetry";
String topicStatus    = String("cabinet/v3/devices/") + DEVICE_ID + "/status";
String topicCommands  = String("cabinet/v3/devices/") + DEVICE_ID + "/commands";
String topicAcks      = String("cabinet/v3/devices/") + DEVICE_ID + "/acks";

// Hàm sinh UUID v4 ngẫu nhiên cho boot_id và connection_id
String generateUUIDv4() {
  char buf[37];
  snprintf(buf, sizeof(buf),
           "%04x%04x-%04x-4%03x-%04x-%04x%04x%04x",
           (uint16_t)esp_random(), (uint16_t)esp_random(),
           (uint16_t)esp_random(),
           (uint16_t)(esp_random() & 0x0fff),
           (uint16_t)((esp_random() & 0x3fff) | 0x8000),
           (uint16_t)esp_random(), (uint16_t)esp_random(), (uint16_t)esp_random());
  return String(buf);
}

// Hàm kiểm tra đồng bộ NTP và lấy chuỗi ISO8601 UTC
bool getIsoTimestamp(String &outIso) {
  time_t now;
  time(&now);
  if (now < 1700000000) { // Chưa đồng bộ NTP
    outIso = "";
    return false;
  }
  struct tm timeinfo;
  gmtime_r(&now, &timeinfo);
  char buf[25];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &timeinfo);
  outIso = String(buf);
  return true;
}

// Hàm tính SHA-256 hex cho chuỗi canonical_json
String sha256Hex(const String &payload) {
  byte hash[32];
  mbedtls_sha256_context ctx;
  mbedtls_sha256_init(&ctx);
  mbedtls_sha256_starts(&ctx, 0);
  mbedtls_sha256_update(&ctx, (const unsigned char*)payload.c_str(), payload.length());
  mbedtls_sha256_finish(&ctx, hash);
  mbedtls_sha256_free(&ctx);

  char hexStr[65];
  for (int i = 0; i < 32; i++) {
    sprintf(hexStr + (i * 2), "%02x", hash[i]);
  }
  hexStr[64] = '\0';
  return String(hexStr);
}

// Đọc cấu hình từ NVS khi khởi động
void loadConfigFromNVS() {
  prefs.begin("iot_cab", true);
  if (prefs.isKey("cfg_ver")) {
    currentConfig.config_version = prefs.getInt("cfg_ver", 1);
    currentConfig.config_hash = prefs.getString("cfg_hash", currentConfig.config_hash);
    currentConfig.fan_binding_id = prefs.getString("binding_id", "");
    currentConfig.fan_on_c = prefs.getFloat("fan_on_c", 30.0f);
    currentConfig.fan_off_c = prefs.getFloat("fan_off_c", 28.0f);
    currentConfig.temp_alert_c = prefs.getFloat("t_alert", 35.0f);
    currentConfig.humidity_alert_pct = prefs.getFloat("h_alert", 80.0f);
    currentConfig.temp_clear_margin_c = prefs.getFloat("t_margin", 1.0f);
    currentConfig.humidity_clear_margin_pct = prefs.getFloat("h_margin", 3.0f);
    currentConfig.telemetry_interval_ms = prefs.getUInt("tele_ms", 5000);
    currentConfig.manual_timeout_s = prefs.getUInt("man_ttl", 60);
    Serial.printf("[NVS] Loaded config v%d (hash: %s)\n",
                  currentConfig.config_version, currentConfig.config_hash.c_str());
  } else {
    Serial.println("[NVS] No saved config, using P0 default v1.");
  }
  prefs.end();
}

// Lưu cấu hình mới vào NVS trước khi gửi ACK
bool saveConfigToNVS(const DeviceConfig &cfg) {
  if (!prefs.begin("iot_cab", false)) return false;
  prefs.putInt("cfg_ver", cfg.config_version);
  prefs.putString("cfg_hash", cfg.config_hash);
  prefs.putString("binding_id", cfg.fan_binding_id);
  prefs.putFloat("fan_on_c", cfg.fan_on_c);
  prefs.putFloat("fan_off_c", cfg.fan_off_c);
  prefs.putFloat("t_alert", cfg.temp_alert_c);
  prefs.putFloat("h_alert", cfg.humidity_alert_pct);
  prefs.putFloat("t_margin", cfg.temp_clear_margin_c);
  prefs.putFloat("h_margin", cfg.humidity_clear_margin_pct);
  prefs.putUInt("tele_ms", cfg.telemetry_interval_ms);
  prefs.putUInt("man_ttl", cfg.manual_timeout_s);
  prefs.end();
  return true;
}

// Áp dụng đầu ra vật lý lên Relay và chân Demand Mirror
void applyRelayOutput(bool turnOn) {
  fanOutput = turnOn;
  digitalWrite(PIN_RELAY, turnOn ? RELAY_ON : RELAY_OFF);
  digitalWrite(PIN_DEMAND_MIRROR, turnOn ? HIGH : LOW);
}

// Vòng lặp đọc cảm biến DHT22 và điều khiển an toàn tại chỗ (Mục 3.4 & 4.1)
void sampleSensorAndControl() {
  TempAndHumidity data = dht.getTempAndHumidity();
  bool validSample = !isnan(data.temperature) && !isnan(data.humidity) &&
                     isfinite(data.temperature) && isfinite(data.humidity) &&
                     (data.temperature >= -40.0f && data.temperature <= 80.0f) &&
                     (data.humidity >= 0.0f && data.humidity <= 100.0f);

  if (!validSample) {
    consecutiveErrors++;
    consecutiveValid = 0;
    sensorOk = false;
    Serial.printf("[DHT22] Read error (%d consecutive)\n", consecutiveErrors);

    // Lỗi 3 lần liên tiếp -> chuyển sang FAULT, hủy MANUAL, ép bật quạt (Bảng 3.7)
    if (consecutiveErrors >= 3 && !isFault) {
      isFault = true;
      faultCode = "DHT_READ_ERROR";
      currentMode = "AUTO";
      digitalWrite(PIN_LED_FAULT, HIGH);
      applyRelayOutput(true);
      Serial.println("[SAFETY] Entered FAULT state -> Forced Fan ON, cancelled MANUAL.");
    }
  } else {
    lastTempC = data.temperature;
    lastHumidityPct = data.humidity;
    sensorOk = true;
    consecutiveErrors = 0;
    consecutiveValid++;

    // Đang FAULT và có 3 mẫu hợp lệ liên tiếp -> phục hồi về AUTO (Bảng 3.7)
    if (isFault && consecutiveValid >= 3) {
      isFault = false;
      faultCode = "";
      currentMode = "AUTO";
      digitalWrite(PIN_LED_FAULT, LOW);
      Serial.println("[SAFETY] Recovered from FAULT -> Returned to AUTO mode.");
    }
  }

  // Nếu đang FAULT, luôn ưu tiên bật quạt an toàn
  if (isFault) {
    applyRelayOutput(true);
    return;
  }

  // Kiểm tra hết hạn MANUAL hoặc mất mạng -> tự quay về AUTO
  bool networkOk = (WiFi.status() == WL_CONNECTED) && mqttClient.connected();
  if (currentMode == "MANUAL") {
    if (!networkOk || (long)(millis() - manualExpireAtMs) >= 0) {
      currentMode = "AUTO";
      Serial.println("[CONTROL] MANUAL expired or network lost -> Reverted to AUTO.");
    }
  }

  // Thực thi điều khiển theo chế độ
  if (currentMode == "MANUAL") {
    applyRelayOutput(manualFanTarget);
  } else {
    // Chế độ AUTO với khoảng trễ hysteresis (fan_off_c .. fan_on_c)
    if (sensorOk) {
      if (lastTempC >= currentConfig.fan_on_c) {
        applyRelayOutput(true);
      } else if (lastTempC <= currentConfig.fan_off_c) {
        applyRelayOutput(false);
      }
      // Trong khoảng (fan_off_c < T < fan_on_c): giữ nguyên trạng thái fanOutput trước đó
    }
  }
}

// Gửi bản tin Status / Heartbeat
void publishStatus() {
  if (!mqttClient.connected()) return;
  statusSeq++;

  JsonDocument doc;
  doc["schema_version"] = 3;
  doc["device_id"] = DEVICE_ID;
  doc["profile"] = DEVICE_PROFILE;
  doc["connection"] = "ONLINE";
  doc["boot_id"] = bootId;
  doc["connection_id"] = connectionId;
  doc["status_seq"] = statusSeq;
  doc["config_version"] = currentConfig.config_version;
  doc["config_hash"] = currentConfig.config_hash;
  doc["firmware_version"] = FIRMWARE_VERSION;
  doc["hardware_version"] = HARDWARE_VERSION;
  doc["uptime_ms"] = (uint64_t)millis();

  String isoNow;
  doc["clock_synced"] = getIsoTimestamp(isoNow);
  doc["health"] = isFault ? "FAULT" : "OK";
  if (isFault) doc["fault_code"] = faultCode;
  else doc["fault_code"] = nullptr;

  String payload;
  serializeJson(doc, payload);
  mqttClient.publish(topicStatus.c_str(), payload.c_str(), true);
}

// Gửi bản tin Telemetry định kỳ
void publishTelemetry() {
  if (!mqttClient.connected()) return;
  telemetrySeq++;

  JsonDocument doc;
  doc["schema_version"] = 3;
  doc["device_id"] = DEVICE_ID;
  doc["profile"] = DEVICE_PROFILE;
  if (currentConfig.fan_binding_id.length() > 0) {
    doc["fan_binding_id"] = currentConfig.fan_binding_id;
  } else {
    doc["fan_binding_id"] = nullptr;
  }
  doc["boot_id"] = bootId;
  doc["connection_id"] = connectionId;
  doc["seq"] = telemetrySeq;

  String isoNow;
  bool synced = getIsoTimestamp(isoNow);
  if (synced) doc["measured_at"] = isoNow;
  else doc["measured_at"] = nullptr;
  doc["clock_synced"] = synced;
  doc["uptime_ms"] = (uint64_t)millis();

  // Khi cảm biến lỗi, gửi null thay vì 0 (Mục 3.4 & 6.4)
  if (!isFault && sensorOk) {
    doc["temperature_c"] = serialized(String(lastTempC, 1));
    doc["humidity_pct"] = serialized(String(lastHumidityPct, 1));
    doc["sensor_ok"] = true;
  } else {
    doc["temperature_c"] = nullptr;
    doc["humidity_pct"] = nullptr;
    doc["sensor_ok"] = false;
  }

  doc["fan_output"] = fanOutput;
  doc["mode"] = currentMode;
  doc["config_version"] = currentConfig.config_version;
  doc["config_hash"] = currentConfig.config_hash;
  if (isFault) doc["fault_code"] = faultCode;
  else doc["fault_code"] = nullptr;
  doc["extra"].to<JsonObject>();

  String payload;
  serializeJson(doc, payload);
  mqttClient.publish(topicTelemetry.c_str(), payload.c_str(), false);
  Serial.printf("[TELEMETRY] seq=%llu | T=%.1fC | H=%.1f%% | Fan=%d | Mode=%s\n",
                telemetrySeq, lastTempC, lastHumidityPct, fanOutput, currentMode.c_str());
}

// Gửi bản tin xác nhận ACK
void publishAck(const String &cmdId, const String &cmdType, uint64_t cmdSeq,
                const String &status, const String &errCode = "") {
  JsonDocument doc;
  doc["schema_version"] = 3;
  doc["device_id"] = DEVICE_ID;
  doc["command_id"] = cmdId;
  doc["status"] = status;
  doc["type"] = cmdType;
  doc["boot_id"] = bootId;
  doc["connection_id"] = connectionId;
  doc["device_command_seq"] = cmdSeq;

  String isoNow;
  if (getIsoTimestamp(isoNow)) doc["executed_at"] = isoNow;
  else doc["executed_at"] = nullptr;

  if (errCode.length() > 0) {
    doc["error_code"] = errCode;
  }

  JsonObject res = doc["result"].to<JsonObject>();
  res["fan_output"] = fanOutput;
  res["mode"] = currentMode;
  res["config_version"] = currentConfig.config_version;
  res["config_hash"] = currentConfig.config_hash;

  String payload;
  serializeJson(doc, payload);
  mqttClient.publish(topicAcks.c_str(), payload.c_str(), false);
  Serial.printf("[ACK] cmd_id=%s | type=%s | status=%s\n",
                cmdId.c_str(), cmdType.c_str(), status.c_str());
}

// Xử lý bản tin lệnh nhận từ topic /commands
void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  String msg;
  msg.reserve(length + 1);
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];

  JsonDocument doc;
  if (deserializeJson(doc, msg) != DeserializationError::Ok) {
    Serial.println("[MQTT] Invalid JSON command.");
    return;
  }

  int schemaVer = doc["schema_version"] | 0;
  String devId  = doc["device_id"] | "";
  String cmdId  = doc["command_id"] | "";
  String type   = doc["type"] | "";
  uint64_t cmdSeq = doc["device_command_seq"] | 0;

  if (schemaVer != 3 || devId != DEVICE_ID || cmdId.length() == 0) return;

  // Kiểm tra trong bộ nhớ đệm 20 ACK để chống thực thi lặp khi retry (Idempotency)
  for (int i = 0; i < 20; i++) {
    if (ackCache[i].valid && ackCache[i].command_id == cmdId && ackCache[i].command_seq == cmdSeq) {
      Serial.println("[MQTT] Duplicate command detected, re-sending cached ACK.");
      publishAck(cmdId, type, cmdSeq, ackCache[i].status);
      return;
    }
  }

  String finalStatus = "REJECTED";
  String errCode = "";

  // Kiểm tra phiên boot_id và connection_id đối với các lệnh điều khiển trực tiếp
  String expBoot = doc["expected_boot_id"] | "";
  String expConn = doc["expected_connection_id"] | "";
  if (type != "APPLY_CONFIG") {
    if ((expBoot.length() > 0 && expBoot != bootId) ||
        (expConn.length() > 0 && expConn != connectionId) ||
        cmdSeq <= highWaterCommandSeq) {
      publishAck(cmdId, type, cmdSeq, "REJECTED", "SESSION_OR_SEQ_MISMATCH");
      return;
    }
  }

  JsonObject params = doc["params"].as<JsonObject>();

  if (type == "SET_MODE") {
    String targetMode = params["mode"] | "";
    uint32_t durationS = params["duration_s"] | currentConfig.manual_timeout_s;
    if (isFault) {
      errCode = "SENSOR_FAULT_ACTIVE";
    } else if (targetMode == "AUTO") {
      currentMode = "AUTO";
      sampleSensorAndControl();
      finalStatus = "APPLIED";
    } else if (targetMode == "MANUAL" && durationS >= 10 && durationS <= 300) {
      currentMode = "MANUAL";
      manualFanTarget = fanOutput;
      manualExpireAtMs = millis() + (durationS * 1000UL);
      finalStatus = "APPLIED";
    } else {
      errCode = "INVALID_MODE_PARAMS";
    }
  }
  else if (type == "SET_FAN") {
    if (isFault) {
      errCode = "SENSOR_FAULT_ACTIVE";
    } else if (currentMode != "MANUAL") {
      errCode = "MANUAL_MODE_REQUIRED";
    } else if (!params["on"].is<bool>()) {
      errCode = "INVALID_FAN_PARAM";
    } else {
      manualFanTarget = params["on"].as<bool>();
      applyRelayOutput(manualFanTarget);
      finalStatus = "APPLIED";
    }
  }
  else if (type == "APPLY_CONFIG") {
    int newVer = params["config_version"] | 0;
    String cfgJson = params["config_json"] | "";
    String contentHash = params["content_hash"] | "";

    // Idempotent nếu cùng version và cùng hash
    if (newVer == currentConfig.config_version && contentHash == currentConfig.config_hash) {
      finalStatus = "APPLIED";
    } else if (newVer < currentConfig.config_version) {
      errCode = "STALE_CONFIG_VERSION";
    } else if (sha256Hex(cfgJson) != contentHash) {
      errCode = "CONFIG_HASH_MISMATCH";
    } else {
      JsonDocument cfgDoc;
      if (deserializeJson(cfgDoc, cfgJson) == DeserializationError::Ok) {
        String prof = cfgDoc["profile"] | "";
        float fOn = cfgDoc["fan_on_c"] | -999.0f;
        float fOff = cfgDoc["fan_off_c"] | -999.0f;
        uint32_t teleMs = cfgDoc["telemetry_interval_ms"] | 0;
        uint32_t manTtl = cfgDoc["manual_timeout_s"] | 0;

        if (prof == "CONTROLLER" && fOff < fOn && fOff >= -40.0f && fOn <= 80.0f &&
            teleMs >= 1000 && teleMs <= 60000 && manTtl >= 10 && manTtl <= 300) {
          DeviceConfig nextCfg = currentConfig;
          nextCfg.config_version = newVer;
          nextCfg.config_hash = contentHash;
          nextCfg.fan_binding_id = cfgDoc["fan_binding_id"].isNull() ? "" : cfgDoc["fan_binding_id"].as<String>();
          nextCfg.fan_on_c = fOn;
          nextCfg.fan_off_c = fOff;
          nextCfg.temp_alert_c = cfgDoc["temp_alert_c"] | 35.0f;
          nextCfg.humidity_alert_pct = cfgDoc["humidity_alert_pct"] | 80.0f;
          nextCfg.temp_clear_margin_c = cfgDoc["temp_clear_margin_c"] | 1.0f;
          nextCfg.humidity_clear_margin_pct = cfgDoc["humidity_clear_margin_pct"] | 3.0f;
          nextCfg.telemetry_interval_ms = teleMs;
          nextCfg.manual_timeout_s = manTtl;

          if (saveConfigToNVS(nextCfg)) {
            currentConfig = nextCfg;
            sampleSensorAndControl();
            finalStatus = "APPLIED";
          } else {
            errCode = "NVS_WRITE_FAILED";
          }
        } else {
          errCode = "INVALID_CONFIG_SCHEMA";
        }
      } else {
        errCode = "INVALID_CONFIG_JSON";
      }
    }
  } else {
    errCode = "UNSUPPORTED_CAPABILITY";
  }

  if (cmdSeq > highWaterCommandSeq) highWaterCommandSeq = cmdSeq;

  // Lưu vào cache ACK
  ackCache[ackCacheIndex] = {cmdId, cmdSeq, type, finalStatus, true};
  ackCacheIndex = (ackCacheIndex + 1) % 20;

  publishAck(cmdId, type, cmdSeq, finalStatus, errCode);
}

// Kết nối lại Wi-Fi và MQTT không chặn (non-blocking) vòng điều khiển
void maintainNetworkConnection() {
  if (WiFi.status() != WL_CONNECTED) {
    digitalWrite(PIN_LED_NET, LOW);
    if (millis() - lastReconnectAttemptMs >= 5000) {
      lastReconnectAttemptMs = millis();
      Serial.println("[WIFI] Reconnecting...");
      WiFi.reconnect();
    }
    return;
  }

  if (!mqttClient.connected()) {
    digitalWrite(PIN_LED_NET, LOW);
    if (millis() - lastReconnectAttemptMs >= 5000) {
      lastReconnectAttemptMs = millis();
      connectionId = generateUUIDv4();
      highWaterCommandSeq = 0;

      // Tạo bản tin LWT (Mục 8.3)
      JsonDocument lwtDoc;
      lwtDoc["schema_version"] = 3;
      lwtDoc["device_id"] = DEVICE_ID;
      lwtDoc["profile"] = DEVICE_PROFILE;
      lwtDoc["connection"] = "OFFLINE";
      lwtDoc["reason"] = "LWT";
      lwtDoc["boot_id"] = bootId;
      lwtDoc["connection_id"] = connectionId;
      String lwtPayload;
      serializeJson(lwtDoc, lwtPayload);

      Serial.printf("[MQTT] Connecting as %s (conn_id=%s)...\n", DEVICE_ID, connectionId.c_str());
      if (mqttClient.connect(DEVICE_ID, MQTT_USERNAME, MQTT_PASSWORD,
                             topicStatus.c_str(), 1, true, lwtPayload.c_str())) {
        Serial.println("[MQTT] Connected!");
        digitalWrite(PIN_LED_NET, HIGH);
        mqttClient.subscribe(topicCommands.c_str(), 1);
        publishStatus();
        publishTelemetry();
      } else {
        Serial.printf("[MQTT] Connect failed, rc=%d\n", mqttClient.state());
      }
    }
  } else {
    mqttClient.loop();
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_RELAY, OUTPUT);
  pinMode(PIN_LED_NET, OUTPUT);
  pinMode(PIN_LED_FAULT, OUTPUT);
  pinMode(PIN_DEMAND_MIRROR, OUTPUT);
  pinMode(PIN_ALIVE_HEARTBEAT, OUTPUT);

  applyRelayOutput(false); // Mặc định tắt quạt khi khởi động
  digitalWrite(PIN_LED_NET, LOW);
  digitalWrite(PIN_LED_FAULT, LOW);

  bootId = generateUUIDv4();
  loadConfigFromNVS();

  dht.setup(PIN_DHT, DHTesp::DHT22);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");

  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
  mqttClient.setBufferSize(MQTT_BUFFER_SIZE);
  mqttClient.setCallback(onMqttMessage);

  Serial.printf("[BOOT] Node %s started (boot_id=%s)\n", DEVICE_ID, bootId.c_str());
}

void loop() {
  unsigned long nowMs = millis();

  // 1. Đảo chân heartbeat vật lý mỗi 500ms chứng minh control loop không bị treo
  if (nowMs - lastAliveToggleMs >= ALIVE_TOGGLE_MS) {
    lastAliveToggleMs = nowMs;
    alivePinState = !alivePinState;
    digitalWrite(PIN_ALIVE_HEARTBEAT, alivePinState ? HIGH : LOW);
  }

  // 2. Đọc cảm biến DHT22 và điều khiển quạt tại chỗ mỗi 2.1 giây
  if (nowMs - lastDhtSampleMs >= DHT_SAMPLE_MS) {
    lastDhtSampleMs = nowMs;
    sampleSensorAndControl();
  }

  // 3. Duy trì kết nối mạng và xử lý bản tin MQTT
  maintainNetworkConnection();

  // 4. Gửi Telemetry định kỳ (mặc định 5 giây)
  if (mqttClient.connected() && (nowMs - lastTelemetryMs >= currentConfig.telemetry_interval_ms)) {
    lastTelemetryMs = nowMs;
    publishTelemetry();
  }

  // 5. Gửi Status Heartbeat định kỳ (15 giây)
  if (mqttClient.connected() && (nowMs - lastStatusMs >= STATUS_INTERVAL_MS)) {
    lastStatusMs = nowMs;
    publishStatus();
  }
}
