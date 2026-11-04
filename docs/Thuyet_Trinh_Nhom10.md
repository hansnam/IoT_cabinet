# KỊCH BẢN THUYẾT TRÌNH CHIA 4 NGƯỜI & CẨM NANG VẤN ĐÁP – NHÓM 10

- **Đề tài:** Hệ thống IoT giám sát nhiệt độ, độ ẩm và điều khiển thông gió tủ thiết bị mạng
- **Học phần:** Bài tập lớn IoT (`INT14149-09`) — **GVHD:** Thầy Kim Ngọc Bách
- **Tổng thời lượng nói Slide:** ~9 phút 30 giây – 10 phút (mỗi thành viên nói **1 cụm slide liền mạch ~2 – 2,5 phút**, không đổi mic qua lại gây đứt mạch).

---

## BẢNG CHIA LƯỢT THUYẾT TRÌNH CHO 4 THÀNH VIÊN

| Lượt nói | Người trình bày | Cụm Slide phụ trách | Thời lượng | Lý do ghép cụm |
| :-: | :--- | :--- | :-: | :--- |
| **Lượt 1** | **TV1** – Phần cứng & Thiết bị nhúng<br>*(Nguyễn Hoàng Long / Đàm Tiến Thành)* | **Mở đầu (Bìa & Bài toán)**<br>→ **Slide 1:** Yêu cầu và chức năng<br>→ **Slide 2:** Kiến trúc tổng thể<br>→ **Slide 3:** Thiết bị và phần cứng | **~2 phút 35 giây**<br>*(0:00 – 2:35)* | Dẫn từ bài toán thực tế của tủ mạng, giới thiệu bức tranh kiến trúc chung rồi chốt thẳng vào sở trường mạch ESP32, DHT22, Relay và thuật toán `AUTO/FAULT`. |
| **Lượt 2** | **TV2** – Backend, CSDL & MQTT Worker<br>**(Trần Hán Nam – `B23DCCN590`)** | **Slide 4:** Giao thức và luồng dữ liệu<br>→ **Slide 5:** Backend và lựa chọn công nghệ<br>→ **Slide 6:** Quản lý vòng đời thiết bị | **~2 phút 40 giây**<br>*(2:35 – 5:15)* | Trùng khớp 100% phần TV2 code và viết Chương 4: hợp đồng MQTT/QoS/Retry/ACK, Flask API + Outbox Worker + PostgreSQL và 6 trạng thái vòng đời thiết bị. |
| **Lượt 3** | **TV3** – Xác thực, Phân quyền & Bảo mật<br>*(Đàm Tiến Thành / Nguyễn Hoàng Long)* | **Slide 7:** Xác thực và phân quyền<br>→ **Slide 8:** Kiểm thử và kịch bản demo | **~2 phút 05 giây**<br>*(5:15 – 7:20)* | Trình bày sâu sở trường Argon2id, JWT gắn phiên, RBAC + phạm vi thiết bị, Mosquitto ACL và nối sang kịch bản kiểm thử độ tin cậy, phân quyền và chỉ tiêu `p95`. |
| **Lượt 4** | **TV4** – Web Dashboard, Cảnh báo & Tích hợp<br>**(Nguyễn Ngọc Vinh – `B23DCCN935`)** | **Slide 9:** Chức năng nâng cao<br>→ **Slide 10:** Kết luận và kế hoạch<br>→ **Dẫn mở màn vào Demo Web trực tiếp** | **~2 phút 10 giây**<br>*(7:20 – 9:30)* | Khép lại bằng các hướng mở rộng (`RPM`, `AI`, `OTA`), tổng kết nguyên lý *"Điều khiển tại chỗ – Theo dõi tập trung"* và chuyển mượt sang thao tác Demo trên giao diện Web. |

---

## PHẦN 1: KỊCH BẢN LỜI NÓI CHI TIẾT CHO TỪNG NGƯỜI (CÓ CÂU CHUYỂN MIC)

---

### 👤 LƯỢT 1: TV1 TRÌNH BÀY *(0:00 – 2:35 | Mở đầu + Slide 1, 2, 3)*

#### [Mở đầu – Slide Bìa & Bài toán] *(~35 giây)*
> *"Em chào thầy và các bạn. Hôm nay Nhóm 10 xin trình bày đề tài: Hệ thống IoT giám sát nhiệt độ, độ ẩm và điều khiển thông gió tủ thiết bị mạng.*
>
> *Xuất phát từ thực tế các thiết bị mạng hoạt động 24/7 trong tủ kín rất dễ tích nhiệt gây treo mạch, nhóm xây dựng hệ thống giải quyết đồng thời hai yêu cầu: Một là giám sát, cảnh báo và điều khiển tập trung từ xa cho ba nhóm người dùng Admin, Operator và Viewer; hai là đảm bảo tính tự chủ tại chỗ — tức ngay cả khi mất Wi-Fi hay gián đoạn máy chủ, quạt thông gió tại tủ vẫn phải tự hoạt động an toàn."*

#### [Slide 1: YÊU CẦU VÀ CHỨC NĂNG] *(~35 giây)*
> *"Hệ thống tập trung vào ba nhóm chức năng trên màn hình:*
> *– **Giám sát**: Theo dõi nhiệt độ, độ ẩm hiện tại, biểu đồ lịch sử, cảnh báo vượt ngưỡng và trạng thái online/offline.*
> *– **Điều khiển**: Vòng lặp tự động **AUTO** chạy trực tiếp trên ESP32; chế độ thủ công **MANUAL** có thời hạn tự hủy để chống bỏ quên; và mọi lệnh từ xa chỉ được ghi nhận khi có phản hồi **ACK** từ mạch.*
> *– **Quản lý**: Phân quyền tài khoản, quản lý vòng đời thiết bị, khóa cách ly từng tủ và lưu nhật ký thao tác."*

#### [Slide 2: KIẾN TRÚC TỔNG THỂ] *(~40 giây)*
> *"Về kiến trúc tổng thể từ trái sang phải: Tại tủ mạng, cảm biến **DHT22** (`GPIO23`) gửi số đo vào **ESP32** để điều khiển **Relay và quạt** (`GPIO18`) với cấu hình lưu trong bộ nhớ **NVS**. ESP32 giao tiếp hai chiều qua **Mosquitto Broker** tới khối **Backend** gồm Flask API, các tiến trình nền và cơ sở dữ liệu **PostgreSQL**. Giao diện **React Web** chỉ truy cập qua **REST API**, không nối thẳng vào MQTT hay Database để đảm bảo kiểm soát phân quyền tập trung."*

#### [Slide 3: THIẾT BỊ VÀ PHẦN CỨNG] *(~45 giây)*
> *"Đi sâu vào phần cứng tại tủ: Nhóm chọn **ESP32** vì tích hợp sẵn Wi-Fi, bộ nhớ NVS và đủ chân GPIO; một module **DHT22** đo đồng thời cả nhiệt độ và độ ẩm; cùng **Relay và quạt DC 5V** tách biệt an toàn mạch điều khiển với nguồn tải.*
>
> *Ở thuật toán **AUTO**, nhóm dùng khoảng trễ nhiệt **hysteresis**: tự động bật quạt từ `30 °C`, tắt ở `28 °C` và giữ nguyên trạng thái trong khoảng `28–30 °C` để chống đóng ngắt relay liên tục. Lệnh **MANUAL** chỉ có hiệu lực `60 giây`, hết hạn hoặc đứt mạng sẽ tự về **AUTO**. Nếu DHT22 lỗi **3 lần liên tiếp**, thiết bị chuyển sang **FAULT** và chủ động bật quạt làm mát.*
>
> ***(Câu chuyển mic sang TV2):*** *Tiếp theo, bạn **Hán Nam** sẽ trình bày chi tiết về thiết kế giao thức truyền thông, kiến trúc Backend và vòng đời thiết bị ạ."*

---

### 👤 LƯỢT 2: TV2 – TRẦN HÁN NAM TRÌNH BÀY *(2:35 – 5:15 | Slide 4, 5, 6)*

#### [Slide 4: GIAO THỨC VÀ LUỒNG DỮ LIỆU] *(~60 giây)*
> *"Em xin tiếp tục với phần giao thức và luồng dữ liệu. Hệ thống dùng **MQTT pub/sub** để tách rời thiết bị phần cứng khỏi dịch vụ xử lý, và dùng **REST API** cho quản trị trên Web. Toàn bộ topic được chuẩn hóa theo mẫu `cabinet/v3/devices/{device_id}/{channel}` để phân quyền riêng cho từng tủ.*
>
> *– Ở luồng **Telemetry**: ESP32 gửi nhiệt độ, độ ẩm kèm định danh và thời gian mỗi `5 giây`, gửi trạng thái mỗi `15 giây` ở mức **QoS 0** để phù hợp thư viện nhúng và không làm trễ vòng điều khiển quạt. Riêng bản tin trạng thái bật **retained**, kết hợp di chúc **LWT** hoặc ngưỡng quá `45 giây` vắng tin để phát hiện offline chính xác.*
>
> *– Ở luồng **Lệnh và ACK**: Lệnh gửi xuống dùng **QoS 1** và **không bật retain** nhằm ngăn thiết bị tự chạy lại lệnh thủ công cũ sau khi khởi động lại. Lệnh hết hạn sau `15 giây`, chờ ACK tối đa `10 giây` và tự động gửi lại ở mốc `3 giây`, `6 giây` với **cùng một ID lệnh** để chống thực thi lặp. Với lệnh cấu hình, hệ thống chỉ xác nhận áp dụng khi cả phiên bản (`version`) và mã băm (`hash`) thiết bị báo về khớp tuyệt đối."*

#### [Slide 5: BACKEND VÀ LỰA CHỌN CÔNG NGHỆ] *(~45 giây)*
> *"Ở phía máy chủ, Backend được tổ chức thành ba khối rõ ràng:*
>
> *– Thứ nhất là **Flask API**: Xử lý xác thực, kiểm tra quyền và cung cấp API dữ liệu, cấu hình; việc chia module độc lập giúp 4 thành viên phối hợp song song thuận lợi.*
>
> *– Thứ hai là tách riêng các **Tiến trình nền (Workers)**: Thay vì để Web API gọi thẳng MQTT dễ gây treo request hoặc nhân bản luồng nhận tin, nhóm tách riêng Worker để nhận telemetry, đối chiếu ACK, đánh giá cảnh báo và gửi lệnh qua bảng hàng đợi **Outbox**.*
>
> *– Thứ ba là **PostgreSQL**: Với tần suất gửi mẫu 5 giây một lần, một hệ quản trị PostgreSQL duy nhất vừa đáp ứng tốt lưu trữ lịch sử chuỗi thời gian, vừa dùng giao dịch ACID giữ nhất quán toàn bộ quan hệ thiết bị, lệnh và cảnh báo."*

#### [Slide 6: QUẢN LÝ VÒNG ĐỜI THIẾT BỊ] *(~55 giây)*
> *"Trên nền Backend đó, mỗi thiết bị được kiểm soát chặt chẽ qua 6 trạng thái vòng đời:*
>
> *– Thiết bị mới đi từ **REGISTERED** khi đăng ký, sang **PROVISIONED** khi được cấp khóa truy cập — nhưng lúc này tài khoản trên Broker vẫn khóa chờ, chỉ khi quản trị viên bấm kích hoạt sang **ACTIVE** mới chính thức vận hành.*
>
> *– Khi bảo dưỡng tại tủ, thiết bị chuyển sang **MAINTENANCE**: vẫn nhận dữ liệu giám sát để kiểm tra mạch nhưng dừng mọi lệnh điều khiển tay từ xa để bảo vệ kỹ thuật viên; hoàn tất sẽ về lại **ACTIVE**.*
>
> *– Khi nghi lộ khóa hoặc có bất thường, thiết bị chuyển sang **LOCKED** để thu hồi ngay quyền kết nối trên Broker; mở khóa sẽ quay về **ACTIVE** hoặc **PROVISIONED** tùy trạng thái trước đó. Khi ngừng sử dụng hẳn sẽ sang **DECOMMISSIONED** — chặn kết nối vĩnh viễn nhưng giữ nguyên lịch sử để kiểm toán. Hai trạng thái `ONLINE/OFFLINE` và `OK/FAULT` luôn được theo dõi độc lập với vòng đời này.*
>
> ***(Câu chuyển mic sang TV3):*** *Để làm rõ cơ chế bảo vệ các luồng dữ liệu và trạng thái vừa nêu, em xin mời bạn **[Tên TV3]** trình bày phần xác thực, phân quyền và kế hoạch kiểm thử ạ."*

---

### 👤 LƯỢT 3: TV3 TRÌNH BÀY *(5:15 – 7:20 | Slide 7, 8)*

#### [Slide 7: XÁC THỰC VÀ PHÂN QUYỀN] *(~60 giây)*
> *"Em xin tiếp nối với cơ chế xác thực, phân quyền và bảo mật của hệ thống qua ba lớp:*
>
> *– Thứ nhất về **Xác thực**: Mật khẩu người dùng được băm bằng thuật toán **Argon2id**. Mỗi mã **JWT** đều gắn trực tiếp với phiên đăng nhập trong cơ sở dữ liệu nên hệ thống có thể thu hồi phiên ngay lập tức khi người dùng đăng xuất hoặc bị khóa tài khoản, đồng thời tuyệt đối không công khai khóa bí mật trong mã nguồn hay nhật ký.*
>
> *– Thứ hai về **Phân quyền**: Hệ thống chia rõ ba vai trò **ADMIN** quản trị toàn cục, **OPERATOR** điều khiển theo quyền được giao và **VIEWER** chỉ xem dữ liệu. Điểm cốt lõi là Backend kiểm tra quyền chi tiết tới **từng thiết bị**, người dùng không được cấp phạm vi trên tủ nào sẽ không thể xem hay gửi lệnh tới tủ đó.*
>
> *– Thứ ba về **Bảo mật thiết bị và đường truyền**: Mỗi node ESP32 dùng khóa truy cập riêng, áp dụng **ACL** trên Mosquitto để giới hạn chỉ giao tiếp trên đúng topic của chính mình; khi thu hồi khóa sẽ ngắt và chặn kết nối lại ngay trên Broker, hướng tới triển khai mã hóa **HTTPS** và **MQTTS**."*

#### [Slide 8: KIỂM THỬ VÀ KỊCH BẢN DEMO] *(~65 giây)*
> *"Để kiểm chứng toàn bộ các thiết kế trên, nhóm thực hiện kiểm thử theo ba nhóm tiêu chí:*
>
> *– Về **Điều khiển**: Thử nghiệm qua ngưỡng nhiệt độ để quạt tự bật từ `30 °C`, tắt ở `28 °C`; gửi lệnh thủ công trên Web nhận đúng **ACK** và tự trở về **AUTO** sau `60 giây`; đồng thời ngắt Wi-Fi/MQTT giữa chừng để xác nhận ESP32 vẫn tự điều khiển tại tủ.*
>
> *– Về **Độ tin cậy và phân quyền**: Giả lập DHT22 lỗi 3 lần liên tiếp để kiểm tra chuyển **FAULT** bật quạt an toàn; dùng tài khoản **VIEWER** gửi lệnh để xác minh Backend từ chối; và thu hồi khóa MQTT để kiểm chứng thiết bị cũ không thể kết nối lại.*
>
> *– Về **Chỉ tiêu định lượng**: Nhóm đo tối thiểu `30 mẫu` cho luồng từ cảm biến đến giao diện với mục tiêu phân vị thứ 95 (`p95`) đạt `≤ 8 giây`; đo tối thiểu `20 lần` từ lúc gửi lệnh tới khi nhận ACK đạt `p95 ≤ 3 giây`, kết hợp chạy liên tục trong `2 giờ` để đánh giá độ ổn định.*
>
> ***(Câu chuyển mic sang TV4):*** *Cuối cùng, bạn **Ngọc Vinh** sẽ trình bày định hướng phát triển nâng cao, tổng kết đề tài và điều phối phần Demo trực tiếp của nhóm ạ."*

---

### 👤 LƯỢT 4: TV4 – NGUYỄN NGỌC VINH TRÌNH BÀY *(7:20 – 9:30 | Slide 9, 10 & Dẫn vào Demo)*

#### [Slide 9: CHỨC NĂNG NÂNG CAO] *(~55 giây)*
> *"Em xin trình bày các hướng mở rộng nâng cao sau khi hệ thống cơ bản đã hoạt động ổn định:*
>
> *– Hướng thứ nhất là **Đo tốc độ quạt (RPM)**: Do quạt DC 2 dây hiện tại mới xác nhận được trạng thái đóng relay chứ chưa đo trực tiếp chuyển động cơ khí của cánh quạt, việc bổ sung cảm biến đọc xung RPM sẽ giúp kiểm chứng quạt thực sự đang quay và phát cảnh báo ngay nếu quạt bị kẹt hoặc mất nguồn tải.*
>
> *– Hướng thứ hai là **Phân tích bằng AI**: Khai thác chuỗi lịch sử nhiệt độ, độ ẩm và vòng quay thực tế để phát hiện sớm dấu hiệu suy giảm hiệu suất quạt — với nguyên tắc bắt buộc phải có dữ liệu vận hành thật và đánh giá độ chính xác trước khi tích hợp.*
>
> *– Hướng thứ ba là **Cập nhật OTA**: Hỗ trợ cập nhật firmware từ xa cho các tủ lắp cố định, có bước kiểm tra tính toàn vẹn mã băm, phiên bản tương thích và xác nhận kết quả khởi động an toàn."*

#### [Slide 10: KẾT LUẬN VÀ KẾ HOẠCH] *(~55 giây)*
> *"Tổng kết lại, giải pháp của Nhóm 10 thực hiện trọn vẹn nguyên lý **Điều khiển tại chỗ – Theo dõi tập trung**:*
>
> *– **Tại tủ mạng**, thiết bị đo nhiệt ẩm ổn định, điều khiển quạt tự động theo khoảng trễ nhiệt và vẫn tiếp tục vận hành bảo vệ tủ khi gián đoạn kết nối mạng.*
>
> *– **Trên giao diện Web**, người dùng theo dõi trực quan dữ liệu hiện tại cùng biểu đồ lịch sử, điều khiển có xác nhận ACK theo đúng phạm vi phân quyền và quản lý tập trung thiết bị, cảnh báo.*
>
> *– Bốn thành viên trong nhóm đã hoàn tất lắp ráp mạch, tích hợp đồng bộ Web – Backend – Broker trên lược đồ 27 bảng PostgreSQL và kiểm chứng các kịch bản vận hành.*
>
> *Phần trình bày trên slide của Nhóm 10 đến đây là kết thúc. Em xin cảm ơn thầy và các bạn đã lắng nghe, ngay sau đây nhóm xin phép chuyển sang màn hình thực tế để Demo hệ thống và trả lời câu hỏi ạ!"*

---

## PHẦN 2: CHIA TRÁCH NHIỆM TRẢ LỜI CÂU HỎI KHI VẤN ĐÁP

Khi thầy đặt câu hỏi phản biện, người phụ trách mảng nào chủ động nhận trả lời chính (các bạn khác bổ sung nếu cần):

1. **TV1 nhận câu hỏi về:** Đấu nối GPIO (`GPIO23`, `GPIO18`, `GPIO19`, `GPIO25`), tiếp điểm `COM–NO` của Relay, tách nguồn tải quạt 5V, chu kỳ đọc `DHT22` (`2,1s`), thuật toán trễ `hysteresis` (`28–30 °C`), lỗi cảm biến `FAULT` và lưu cấu hình trong `NVS`.
2. **TV2 (Trần Hán Nam) nhận câu hỏi về:** Cấu trúc topic MQTT v3, `QoS 0` vs `QoS 1`, `retained`, `LWT` & timeout `45s`, hàng đợi `Transactional Outbox` & `mqtt_worker`, retry lệnh (`0s, +3s, +6s`, timeout `10s`, hết hạn `15s`), đồng bộ cấu hình `desired/applied` + `SHA-256`, CSDL PostgreSQL 27 bảng, thống kê/xuất CSV và 6 trạng thái vòng đời thiết bị.
3. **TV3 nhận câu hỏi về:** Băm mật khẩu `Argon2id`, `Access JWT 15 phút` đối chiếu `auth_sessions`, chống đoán mật khẩu (Rate Limit), phân quyền kép `RBAC + device_access` (`403` vs `404`), `audit_logs` / `user_activity_logs` và phân quyền `ACL` / thu hồi khóa trên Mosquitto Dynamic Security.
4. **TV4 nhận câu hỏi về:** Giao diện `React + Vite`, biểu đồ `Recharts`, cơ chế polling `2s`, đánh giá cảnh báo trong `alert_worker` (`OPEN → ACKNOWLEDGED → RESOLVED`), đóng gói `Docker Compose` và tổng hợp số liệu đo kiểm `p95`.

---

## PHẦN 3: CẨM NANG VẤN ĐÁP NHANH DÀNH RIÊNG CHO TV2 (TRẦN HÁN NAM)

- **Ý nghĩa `p95 ≤ 8s` & `p95 ≤ 3s`:** Ít nhất 95% số mẫu đo có độ trễ không quá ngưỡng. Cảm biến gửi mỗi `5s` + xử lý Broker/DB `~0,3s` + Web polling mỗi `2s` → lệch nhịp tối đa `~7,3s ≤ 8s`. Lệnh đi qua Outbox (`0,5s`) + MQTT `QoS 1` + ACK trả về + Web cập nhật → hoàn tất trong `0,5 – 2,5s ≤ 3s`.
- **Vì sao tách `mqtt_worker` và dùng `outbox_messages`:** Tránh nhân bản luồng nhận telemetry khi mở rộng nhiều tiến trình Web, đồng thời ghi nguyên tử `commands + outbox_messages + audit_logs` trong 1 transaction PostgreSQL (`HTTP 202 Accepted`) để không treo HTTP hoặc mất lệnh khi mạng MQTT chập chờn.
- **Không báo thành công giả:** `202 Accepted` chỉ là `PENDING`; Broker `PUBACK` mới là `SENT` (mỗi thiết bị tối đa 1 lệnh `SENT` cùng lúc); chỉ khi nhận gói `/acks` từ ESP32 khớp `command_id, seq, boot_id, connection_id` mới chuyển sang `APPLIED`. Lệnh `SET_FAN` bắt buộc `SET_MODE(MANUAL)` đạt `APPLIED` trước và dùng giá trị đích `on: true/false` (không dùng `toggle`).
- **Xử lý `NULL`, gói trễ & Khóa thiết bị:** Lỗi cảm biến lưu `NULL` (không lưu `0` làm sai `AVG`); gói trễ (`seq ≤ latest_seq`) lưu lịch sử nhưng không ghi đè lùi `device_shadows`; khóa/ngừng dùng thiết bị chặn ngay ở DB + ngắt TCP trên Mosquitto + xóa gói `Retained /status` cũ + giữ toàn bộ lịch sử bằng `ON DELETE RESTRICT`.

---

## PHẦN 4: CHECKLIST 5 BƯỚC TRƯỚC KHI BẤM DEMO TRÊN BỤC GIẢNG

1. Kiểm tra đủ các tiến trình đang chạy: PostgreSQL, Mosquitto, `Flask API`, **`mqtt_worker.py`**, `alert_worker.py` và `Frontend`.
2. Kiểm tra tủ `cabinet-001` đang `ACTIVE`, `ONLINE`, `sensor_ok = true`, `stale = false`, `clock_synced = true`.
3. Demo điều khiển tay đúng trình tự: Bấm chuyển **`MANUAL`** → chờ hiện **`APPLIED`** → mới bấm **`Bật / Tắt quạt`**.
4. Demo phân quyền: Đăng nhập tài khoản **`VIEWER`** thử bấm điều khiển để cho thấy bị chặn `403 Forbidden`.
5. Demo tự chủ tại biên: Đang `MANUAL` tắt quạt → ngắt Wi-Fi/Broker + gia nhiệt `DHT22 ≥ 30 °C` → ESP32 tự về `AUTO` và đóng Relay bật quạt!
