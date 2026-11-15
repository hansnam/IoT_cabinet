# TV3 — Kế hoạch Django cho Gemini, học và review theo từng patch

Ngày kiểm kê: **05/11/2026**. Baseline đã đọc: commit **fe7b6a2**. Người thực hiện: TV3. Nhóm đã thuyết trình, đang chuẩn bị demo; không tự đặt lại hạn còn 7 ngày kể từ ngày đọc tài liệu này.

**Cách làm đã thống nhất:** Gemini đọc và đề xuất trong chat; **không tự sửa code**. Mỗi lần chỉ một patch nhỏ, xuất **toàn bộ nội dung từng file**, đánh dấu file mới/file sửa, chú thích và giải thích bằng tiếng Việt cho người chưa thạo Django. TV3 tự đọc, tạo/sửa file và chạy lệnh. Sau đó dừng để TV3 và người review kiểm tra trước khi làm patch tiếp.

Tài liệu này là kế hoạch, **chưa triển khai Django**. Không xem các trạng thái dự kiến là đã hoàn thành.

Nguồn yêu cầu: [đặc tả V1.4](IOT_TU_THIET_BI_DAC_TA_CHUNG_V1_3.md), [đối chiếu đề bài/demo](DOI_CHIEU_DE_BAI_VA_CHUAN_BI_DEMO.md), [đánh giá chuyển đổi](DANH_GIA_VA_CHUYEN_DOI_DJANGO.md). File đặc tả còn tên V1_3 nhưng nội dung hiện hành là V1.4. Bản Flask trong Description là lịch sử.

## 1. Kiểm kê TV3: đã có gì, thiếu gì

### 1.1 Cách hiểu trạng thái

- **CÓ MÃ FLASK:** đã đọc thấy implementation và/hoặc tests, chưa chạy xác nhận trong lượt rà soát này.
- **CHƯA CÓ IMPLEMENTATION:** mới package mô tả hoặc chưa thấy file/route/model tương ứng.
- **CẦN PORT:** nghiệp vụ còn dùng nhưng phải viết adapter Django/ORM, không copy nguyên Flask.
- **KHÔNG PORT P0:** chức năng/cơ chế cũ được V1.4 thay thế hoặc hoãn.
- **CHỜ TÍCH HỢP:** cần phần TV1/TV2/TV4 hoặc môi trường thật, không chứng minh bằng mock.

Không có báo cáo PASS từ việc đếm file hoặc đọc tên test. Repo hiện backend/ vẫn chứa Flask; chưa có manage.py/project Django hoặc bộ test Django. Sau kiểm kê đã chuẩn bị môi trường Python/Django ở mục1.4; mới kiểm tra import/dependencies, **chưa chạy suite ứng dụng Flask/Django hoặc xác nhận DB/broker/phần cứng**.

### 1.2 Bảng kiểm kê cụ thể

| Phần TV3 | Bằng chứng đang có | Đánh giá và việc tiếp theo |
|---|---|---|
| Foundation, cấu hình, lỗi, request ID, headers | backend/app/security/; app factory; tests/tv3/test_foundation.py | CÓ MÃ FLASK; port envelope/request ID/config validation sang Django; readiness hiện chủ yếu kiểm DB, worker health là phần tích hợp |
| Argon2id và password validation | backend/app/auth/passwords.py; test_crypto.py | CÓ MÃ FLASK; giữ ca test/ý nghĩa, dùng Django set_password/check_password/Argon2PasswordHasher thay hasher thô |
| Phát và kiểm JWT | backend/app/auth/tokens.py; security JWT callbacks | CÓ MÃ FLASK nhưng KHÔNG PORT P0; không thêm SimpleJWT/Bearer/refresh để khớp tests cũ |
| Session registry/revoke | backend/app/auth/sessions.py; test_session_guard.py | CÓ MÃ FLASK; viết registry dùng cùng Django session, không giữ JWT ver/exp làm nguồn auth mới |
| Login/logout/me | backend/app/auth/routes.py, service.py; test_login.py | CÓ MÃ FLASK; port thành cookie session, CSRF kể cả anonymous login, mã lỗi/session hết hạn đúng V1.4 |
| Rate limit bền vững | auth/rate_limit.py, LoginAttempt; login tests | CÓ MÃ FLASK; có HMAC, purpose, advisory locks, Retry-After; port ORM và dùng cho cả API/Admin |
| Hồ sơ và tự đổi password | auth service/schemas/routes; test_profile_password.py | CÓ MÃ FLASK; port; giữ old_password check, rollback, revoke all, must_change_password |
| RBAC và scope thiết bị | permissions/service.py, decorators.py; test_permissions.py | CÓ MÃ FLASK; port policy/queryset/permission classes; không tái tạo bảng role riêng cạnh Groups/Permissions |
| Quyền worker dispatch | PermissionService.authorize_dispatch trong permissions/service.py | CÓ MÃ FLASK; đây là logic đã có, không đánh dấu chưa làm; port khi TV2 có models Command/Config/Device Django |
| Tạo/khóa/archive user, đổi role, admin reset password | backend/app/users/__init__.py mới mô tả; không có users service/routes | CHƯA CÓ IMPLEMENTATION hoàn chỉnh; cần shared UserService và Admin forms/actions, last-admin guard/revoke/audit |
| Gán DeviceAccess qua giao diện/service quản trị | DeviceAccess model/seed và truy vấn scope đã có | Có dữ liệu nền/read policy, chưa thấy workflow quản trị grant hoàn chỉnh; làm service/Admin cùng TV2 |
| Audit + nhật ký chức năng | audit/service.py, user_activity/service.py, catalog, models/logs.py; test_log_writers.py | CÓ MÃ FLASK ghi hai bảng; port thành một AuditLog theo V1.4, giữ actor/result/redaction/atomic |
| Trang đọc log bảo mật/nhật ký | Không có route đọc log/Admin Django; app factory chỉ đăng ký auth/devices/telemetry/commands | CHƯA CÓ UI hoàn chỉnh; Admin read-only/filter là bàn giao P0, không bắt xây API logs riêng |
| Seed user/role/quyền | backend/seed.py; test_schema_seed.py | CÓ MÃ FLASK, 31 mã gồm quyền dự phòng; seed Django chỉ quyền P0 triển khai, không copy nguyên role tables/EXT |
| Broker Dynamic Security adapter | backend/app/broker_operations/__init__.py chỉ mô tả | CHƯA CÓ IMPLEMENTATION; viết adapter payload/transport/timeout, test broker thật cùng TV2 |
| ACL/client binding/disconnect/reconnect denial | mosquitto.conf có khai báo plugin | Chỉ khai báo plugin không chứng minh bootstrap/ACL/revoke chạy; CHỜ triển khai và test thật |
| Auth frontend | App.jsx dùng fetch chưa thấy login/session helper; Vite chưa có proxy | CHƯA CÓ tích hợp auth V1.4; helper/contract bàn giao TV4, không tự làm lại dashboard |
| TLS/deployment | Compose DB/broker/Adminer, broker config có listener1883; chưa thấy listener TLS đầy đủ | CHỜ TÍCH HỢP TV1/TV2/TV4; expose8883 không chứng minh MQTTS |
| Django accounts/admin/ORM/migrations/tests | Chưa có project Django trong backend/ | CHƯA CÓ IMPLEMENTATION; bắt đầu foundation chung một lần, custom User trước migration đầu |

Tài sản tests Flask: **8 file test_*.py, 165 hàm test cấp module** theo quét tĩnh; parametrization có thể tạo nhiều case hơn. Đây không phải số tests đã chạy hay PASS.

### 1.3 Những gì không cần làm lại

- Giữ logic và ví dụ nghiệp vụ của scope/revoke/worker authorization, redaction, password, rate-limit; viết lại phần framework/ORM phù hợp.
- Không sửa Flask chỉ để tests JWT xanh trước khi làm Django. Mã cũ đọc tham khảo, không gọi từ backend Django.
- Không tiếp tục tạo refresh tokens, session UI, React Users CRUD, self activity API, re-auth/MFA, nhóm/location nhiều tầng, P1/AI/OTA.
- Không chuyển raw password hash vào field password Django bằng gán chuỗi tùy tiện; không sửa migration Alembic đã chạy hoặc xóa DB cũ.

### 1.4 Môi trường đã chuẩn bị ngày 05/11/2026

- Đã tạo .venv/ tại gốc repo, dùng Python3.14.10; .gitignore đã bỏ qua thư mục này.
- Đã cài Django5.2.19, djangorestframework3.18.3, psycopg/psycopg-binary3.3.8, argon2-cffi25.1.0, python-dotenv1.2.4.
- Đã kiểm Django version, import các thư viện và pip check: đạt, không dependency conflict được pip phát hiện. Đây chưa là kiểm thử ứng dụng hay chứng minh kết nối PostgreSQL.
- Chưa lưu Flask vào back/, chưa tạo Django project, chưa migrate/seed. Gemini phải nhận biết phần môi trường đã có, không tạo lại/xóa venv.

Để TV3 tự tạo khung project, chạy lệnh ở **thư mục gốc IoT_cabinet**:

1. Dùng Explorer/VS Code tạo back/, chuyển nguyên thư mục backend hiện tại vào đó thành back/backend. Nếu nơi đích đã tồn tại thì dừng kiểm, không gộp/ghi đè. Tests/SQL cũ lưu theo mục4.3 khi thực hiện bước archive đầy đủ.
2. Sau khi đã xác nhận bản cũ lưu đúng, tạo backend mới và khung config bằng các lệnh bên dưới. Người dùng tự chạy, Gemini không thực hiện.

```powershell
if (Test-Path -LiteralPath .\backend) {
    throw 'backend vẫn tồn tại. Kiểm tra việc lưu Flask vào back/backend trước.'
}
New-Item -ItemType Directory -Path .\backend
.\.venv\Scripts\python.exe -m django startproject config backend
.\.venv\Scripts\python.exe -m django --version
```

Kết quả mong đợi: backend/manage.py và backend/config/{settings,urls,wsgi,asgi}.py. config/asgi.py do startproject tạo có thể giữ nguyên; không phải một project thứ hai.

3. Trong VS Code chọn Python interpreter .venv/Scripts/python.exe. Dùng đường dẫn interpreter đầy đủ như trên thì không bắt buộc activate hoặc đổi ExecutionPolicy.
4. Tiếp tục chặng01 để Gemini trình bày custom User/app accounts/settings PostgreSQL/env đúng cấu trúc, bạn tự copy. **Chưa chạy migrate hoặc createsuperuser** trước khi AUTH_USER_MODEL và migration đầu đã review.

backend/requirements.txt hiện vẫn thuộc Flask trước archive; không dùng file đó để cài môi trường Django. Sau khi tạo backend mới, Gemini đề xuất requirements Django đã pin dựa version thực ở trên; không ghi đè file của bản Flask.

## 2. Phạm vi TV3 và hợp đồng với các thành viên

| TV3 sở hữu | Cần phối hợp | TV3 không tự triển khai thay |
|---|---|---|
| User/Groups/password, session registry/CSRF/login limiter, auth API | TV2 scaffold/settings/User dependencies | Backend telemetry/commands/outbox/alerts toàn bộ |
| UserService, last-admin guard, Users Admin | TV2/TV4 biết role/grant/auth contract | Model Device giả hoặc một Django project thứ hai |
| PermissionService, DeviceAccess policy/grants Admin | TV2 sở hữu Device/Config/Command, tích hợp policy vào API/worker | Đổi MQTT schema/timing/safety firmware |
| AuditLog service/Admin read-only | TV2/TV4 gọi cùng logger/transaction | Viết tracking mọi poll hoặc log SUCCESS cho HTTP202 |
| Broker adapter/ACL policy | TV2 lifecycle/DeviceOperation; TV1 credential trên ESP32 | Tự nhận lifecycle/outbox/reconcile thành công khi broker chưa xác nhận |
| Auth helper frontend và checklist TLS | TV4 UI/proxy/Compose; TV1 MQTTS/CA | Ghi đè App.jsx/dashboard hoặc cấu hình deployment chung thiếu phối hợp |

**Điểm chốt trước code, patch 00:** nếu TV2 đã có scaffold mới ở nhánh khác, lấy cấu trúc/settings thực tế đó làm nền. Không chạy startproject/migrate lần hai. Nếu chưa có, TV3 trình bày scaffold chung để người dùng đưa vào, bàn giao TV2 dùng cùng project backend.

Các chữ ký dưới đây là contract đích, có thể tinh chỉnh tên ở patch00 nhưng phải thống nhất trước tích hợp:

- accounts/services/sessions.py: verify_session(user_id, sid, now=None), revoke_session(sid, reason), revoke_all_user_sessions(user_id, reason); helper không tự commit.
- accounts/services/auth.py: authenticate_credentials(...), establish_session(request, user,...), logout_session(request,...); login API và Admin dùng chung các bước.
- accounts/services/users.py: create_user(...), update_profile(...), change_password(...), reset_password(...), change_role(...), change_status(...); xác định rõ ai là actor và ai là target.
- accounts/policies/roles.py: require_permission(user, action) cho quyền global/self.
- accounts/policies/devices.py: visible_devices(user, now=None), require_device_permission(user, action, device_id,...). Các hàm HTTP/worker đều gọi được, không đọc request toàn cục bên trong.
- accounts/policies/dispatch.py: authorize_dispatch(command_id, now=None) trả allowed/reason; chỉ đọc DB, không publish/commit.
- audit/services.py: record_event(actor, function_key, action, outcome, target,...); một nguồn log, actor server xác minh, caller quyết định transaction.
- mqtt/broker_security.py: provision_client(...), enable_client(...), disable_client(...), inspect_client(...); transport/response có timeout và correlation, không nhận actor USER giả cho worker.

Django app labels accounts/devices/audit/... độc lập với Flask backend.app; không import backend.app trong project mới. Flask sẽ được lưu dưới back/backend/; đường dẫn bằng chứng ở mục1 ghi theo trạng thái trước di chuyển.

Quy ước để tránh nhầm khi học: package Python là apps.accounts, AppConfig.name là apps.accounts, app label là accounts; AUTH_USER_MODEL dùng accounts.User. BASE_DIR của Django là backend, env riêng nạp từ đó nếu dùng file .env; không tự nạp .env gốc đang chứa cấu hình Flask để rồi dùng nhầm DB/secret.

## 3. Nội quy bắt buộc cho Gemini

### 3.1 Chỉ đề xuất, không thao tác thay người dùng

1. **Không gọi công cụ ghi/sửa/xóa file, apply_patch, shell ghi file, formatter autofix hoặc tạo migration.** Không tự sửa ngay cả khi đã biết lỗi.
2. Không tự chạy install, migrate, makemigrations, seed, test có DB write, start service, Docker up, git add/commit/push. Lệnh cần chạy phải đưa trong chat, người dùng đọc rồi tự chạy.
3. Chỉ đọc file/trạng thái nếu môi trường cho phép; không đọc hoặc in .env/credential/private key. Thiếu thông tin chỉ hỏi tên biến/cấu trúc/log đã che secret.
4. Không tự gọi agent khác để triển khai hoặc gửi tin cho team. Có thể viết nội dung bàn giao trong chat để TV3 tự gửi.
5. Người dùng copy thủ công không có nghĩa Gemini được sửa các file liên quan còn lại. Mọi thay đổi ngoài patch phải tách patch mới và giải thích.

### 3.2 Một phản hồi = một phần vừa đủ của chặng hiện tại

- Một phần có một mục tiêu có thể hiểu/kiểm tra; thường 1–3 file code chính và test liên quan. Boilerplate scaffold có thể nhiều file rất ngắn, phải liệt kê hết.
- Mỗi file mới/sửa xuất **toàn bộ nội dung cuối cùng**, không diff, không “giữ phần cũ”, không dấu ba chấm, không TODO/pass/return True cho nghiệp vụ chưa làm. File rỗng __init__.py phải ghi rõ “file rỗng”, không nhét chú thích ngoài code vào file.
- File sửa phải dựa trên **nội dung mới nhất người dùng đã copy/TV2 đã sửa**. Chưa đọc được file hiện tại thì xin người dùng cung cấp file, không viết đè bằng bản tưởng tượng.
- Nếu file/phản hồi quá dài để cung cấp trọn vẹn, dừng trước code, đề xuất phần nhỏ như 04a/04b hoặc module theo trách nhiệm, chờ xác nhận; không tự ghép nhiều patch hoặc cắt mất cuối file.
- Không viết code patch sau dù còn dư thời gian. Không coi “ok”, không có lỗi báo về, hoặc ảnh một màn hình thành PASS toàn bộ.
- Sau mỗi patch ghi: **“DỪNG TẠI PHẦN TV3-DJ-XX(a). Chưa sang phần tiếp. Chờ bạn copy, chạy kiểm tra và gửi kết quả/review.”**
- Chỉ tiếp tục khi người dùng nêu rõ phần hiện tại đã review/chấp nhận và yêu cầu phần tiếp; nếu còn lỗi thì chỉ đề xuất bản sửa của patch hiện tại.

### 3.3 Chú thích và giải thích cho người mới học

- Mở đầu mỗi file: vai trò file, được Django/call site nào nạp, phụ thuộc ai, tác động DB/session hay chỉ tính toán.
- Mỗi hàm/lớp tự viết có docstring: nhiệm vụ, tham số, dữ liệu trả về, caller, thay đổi DB/cookie/session, transaction và ngoại lệ quan trọng.
- Hằng số/cấu hình/field có chú thích ý nghĩa, đơn vị, giá trị mặc định và lý do chọn. Biến cục bộ mới phải giải thích ở gần chỗ xuất hiện hoặc bảng biến sau code; không để tên viết tắt khó hiểu.
- Giải thích import, decorator, self, request, serializer, queryset, FK/on_delete, timezone, atomic/select_for_update và Django hook khi lần đầu gặp.
- Tách code thư viện cung cấp và code dự án tự viết. Nêu rõ Django gọi hàm nào tự động; không làm người học tưởng save() luôn tự gọi full_clean().
- Với mỗi patch có ví dụ đầu vào → từng bước hàm → DB/session thay đổi → HTTP/output; có một nhánh lỗi đối chiếu.
- Chú thích đúng hành vi, không dùng “mã hóa JWT” thay “ký token”, không gọi hashing password là mã hóa có thể giải mã.

### 3.4 Khung phản hồi chuẩn cho từng patch

1. Chặng ID/phần; mục tiêu, prerequisites đã xác nhận, phần chưa làm.
2. Danh sách file theo thứ tự copy: NEW/MODIFY/GENERATED-BY-USER và lý do.
3. Luồng gọi ngắn và kiến thức Django cần hiểu.
4. Toàn bộ code từng file, đường dẫn ở ngoài code fence; chú thích tiếng Việt trong code.
5. Bảng hàm/biến/field, giải thích dữ liệu vào/ra và tác dụng phụ.
6. Lệnh PowerShell **để người dùng chạy**, working directory, expected output và trường hợp thất bại; không giả log PASS.
7. Acceptance checklist và câu hỏi tự kiểm tra.
8. Gói gửi reviewer: files/commands/output/commit hoặc diff đã che secret.
9. Câu dừng bắt buộc; không viết tiếp patch.

## 4. Cấu trúc theo app và theo lớp bên trong app

Bản trước có nhiều file ngang hàng trong accounts. Bản này dùng cách **chia theo nghiệp vụ trước, rồi chia theo trách nhiệm**. Nhìn vào accounts sẽ tìm được model, kiểm tra đầu vào, xử lý request và nghiệp vụ ở các thư mục riêng.

| Cách gọi bạn có thể quen | Tên thường dùng trong Django/DRF | Trách nhiệm |
|---|---|---|
| Request/DTO validation | serializers/ | Kiểm JSON đầu vào, chuẩn hóa và định dạng dữ liệu trả về |
| Controller | views/ | Nhận HTTP request, gọi serializer/service và trả response |
| Service | services/ | Nghiệp vụ và transaction, không phụ thuộc frontend |
| Entity/model | models/ | Dữ liệu ORM, quan hệ và constraints |
| Authorization/policy | policies/ | Role/permission/scope và quyền dispatch |
| Form | forms/ | Form HTML/Admin; không phải serializer JSON |
| Router | urls.py | Ánh xạ URL tới view |

Không cần đặt đúng tên requests/controllers để có cách tổ chức quen thuộc. Giữ tên serializers/views giúp đọc tài liệu Django/DRF dễ hơn. HTTP request là đối tượng được truyền vào view; serializer là lớp kiểm dữ liệu, không phải đối tượng request đó.

### 4.1 Cây đích

**Đây là cấu trúc dự kiến, chưa tạo mã.** Chỉ tạo module khi chặng thực sự dùng; không tạo toàn bộ file rỗng trước. Với audit có ít file, giữ models.py/services.py là đủ; không bắt mỗi lớp phải có một folder riêng.

```text
back/                                   # Bản Flask lưu riêng, chưa di chuyển thực tế
  backend/                              # Nguyên backend cũ: app/migrations/workers/seed/requirements
  tests/                                # Tests Flask tv2/tv3 đi cùng bản cũ
  database/                             # Schema SQL cũ cần giữ để tham khảo
  README.md                             # Cách đọc/chạy bản cũ, không entrypoint Django
backend/                                # Backend chính, Django đặt thẳng tại đây
  manage.py
  requirements.txt
  .env.example                        # Placeholder riêng Django
  README_TV3.md
  config/
    __init__.py
    settings.py
    test_settings.py
    urls.py
    wsgi.py
  apps/
    __init__.py
    core/                             # Hạ tầng chung, ít file nên giữ gọn
      __init__.py
      apps.py
      errors.py                       # Envelope, error codes, CSRF failure
      middleware.py                   # Request ID/headers
      test_runner.py                  # Guard trước setup/flush DB test
      views.py                        # Health, phối hợp TV2 worker readiness
      tests/
        __init__.py
        test_foundation.py
        test_database_guard.py
    accounts/
      __init__.py
      apps.py
      urls.py
      admin.py                        # Entry Django autodiscover, UserAdmin
      admin_site.py                   # Một AdminSite chung
      managers.py
      models/
        __init__.py                   # Export User/AuthSession/LoginAttempt
        user.py
        auth_session.py
        login_attempt.py
      serializers/
        __init__.py
        auth.py                       # Login/password payloads
        profile.py                    # Me/profile payloads
      views/
        __init__.py
        auth.py                       # CSRF/login/logout
        profile.py                    # Me/update/change-password
        admin_auth.py                 # Admin login/logout shared policy
      services/
        __init__.py
        auth.py                       # Authenticate/establish/logout
        sessions.py                   # Verify/revoke registry
        users.py                      # Profile/password/create/role/status/reset
      policies/
        __init__.py
        catalog.py                    # Business code -> Django permission
        roles.py                      # Global/self permissions
        devices.py                    # Scope queryset/action
        dispatch.py                   # Worker policy, cần model thật TV2
      security/
        __init__.py
        authentication.py             # DRF session + registry
        middleware.py                 # Admin registry/must-change guard
        rate_limit.py
        password_validators.py
      forms/
        __init__.py
        auth.py                       # Admin authentication form
        users.py                      # UserAdmin forms
      migrations/
        __init__.py
        0001_initial.py               # User từ migration đầu
        ...                           # User tự sinh migrations thực về sau
      management/
        __init__.py
        commands/
          __init__.py
          seed_roles.py
          bootstrap_admin.py
      tests/
        __init__.py
        helpers.py
        test_models_roles.py
        test_auth.py                  # Session/CSRF/limiter/login/logout
        test_users_admin.py           # Profile/password/user service/Admin
        test_scope_dispatch.py
        test_integration.py           # Race/failure/cookie replay
    audit/
      __init__.py
      apps.py
      models.py
      services.py                     # Writer + sanitize helpers, một nguồn log
      admin.py
      migrations/
      tests/
        __init__.py
        test_audit.py
    devices/                          # TV2 sở hữu Device/lifecycle
      models.py                       # DeviceAccess cùng TV3, một định nghĩa
      access_services.py              # TV3 grant/revoke
      access_admin.py                 # TV3 DeviceAccessAdmin
      admin.py                        # DeviceAdmin TV2, không register trùng
      migrations/
    commands/                         # TV2
    telemetry/                        # TV2
    alerts/                           # TV4 + TV2 hỗ trợ
    mqtt/                             # Chung với TV2
      broker_security.py              # TV3 ACL/controller adapter
      broker_transport.py             # TV3 correlation/timeout/TLS
      tests/
        __init__.py
        test_broker.py
frontend/
  src/api/auth.js                        # TV3 helper bàn giao TV4
  vite.config.js                         # Proxy phối hợp TV4
deployment/                              # TLS/proxy/Compose phối hợp
docs/
  TV3_KE_HOACH_DJANGO_CHO_GEMINI.md
  TV3_KET_QUA_KIEM_THU.md                 # Chỉ tạo khi có kết quả thật
tests/                                  # Kịch bản tích hợp chung; tests Flask nằm trong back/tests
```

### 4.2 Các quy tắc để việc tách folder vẫn chạy đúng Django

- models/ thay cho models.py: **không có đồng thời cả hai** trong cùng app. models/__init__.py import/export model classes để Django phát hiện; chỉ để file user.py mà không import không đủ.
- Django vẫn tìm apps.py, models, admin.py và management/commands theo cơ chế framework. Giữ entrypoints này; admin.py có thể gọi forms/services trong folder khác.
- urls.py import view tường minh, ví dụ từ apps.accounts.views.auth; không cần dồn mọi view vào __init__.py hoặc dùng import sao.
- Serializer không ghi nghiệp vụ thay service; view giữ mỏng; model không phụ thuộc view. Services/policies không import HTTP request toàn cục.
- Đừng tách mỗi hàm thành một file hoặc tạo base controller/repository/interface chỉ để giống framework khác. Phần ít code giữ một file, tách khi có trách nhiệm rõ.
- Cây là đích để tìm đường; Gemini chưa được phép tự tạo folder/file nào.

### 4.3 Cất bản cũ trước khi dùng lại backend/

- Đây là **cấu trúc đích trong kế hoạch**; hiện code Flask vẫn ở backend/. Gemini chỉ trình bày danh sách/lệnh, người dùng quyết định và tự di chuyển.
- Sau khi chặng00 chốt, lưu nguyên thư mục backend hiện tại thành back/backend trước khi tạo backend Django mới. Giữ cả app/, migrations/, workers/, seed.py, mqtt_worker.py và requirements.txt cũ; không ghi requirements Django đè requirements Flask.
- Lưu tests/tv2 và tests/tv3 cũ cùng bản Flask ở back/tests; giữ schema SQL cũ cần dùng ở back/database. Không di chuyển toàn bộ tài liệu/database/deployment đang dùng chung nếu chưa phân loại và phối hợp team.
- Ghi back/README.md về snapshot và đường dẫn chạy. Giữ back/backend giúp bảo toàn cấu trúc import backend.app của code cũ; khi kiểm thử Flask phải dùng đúng working directory của bản lưu và DB Flask cũ, không chạy tests cũ vào backend Django. Cần chạy kiểm tra trước khi gọi bản lưu là rollback đã xác minh.
- Chỉ lưu source/tài liệu cần thiết, không đưa .env/private key/credential/venv vào bản archive được commit. Không đổi/xóa DB cũ hoặc Git history.
- Nếu back/backend đã tồn tại, phải kiểm tra và hỏi cách lưu snapshot trước, không overwrite/gộp âm thầm. Không mở cả worker Flask và Django xử lý cùng topic.
- Django dùng backend/manage.py, backend/config, backend/apps, backend/requirements.txt và env backend/.env. Mọi lệnh, test, Docker working directory và README mới phải trỏ các đường dẫn này. **Không tạo lại thư mục backend/django/.**

## 5. Các nguyên tắc kỹ thuật không được làm sai

1. Custom User/UUID/AUTH_USER_MODEL trước migrate đầu. Trong scaffold đầu có thể chưa chạy check/migrate; không chạy migration auth mặc định rồi mới đổi User.
2. DB Django iot_cabinet_django; test iot_cabinet_django_test, tuyệt đối khác iot_cabinet/iot_cabinet_test cũ. Test runner guard chạy trước create/flush/drop; PostgreSQL cho race/row lock, không dùng SQLite thay bằng chứng.
3. Role dùng Group/Permission; một role/user do service/form enforce, không user.role thứ hai. is_staff không là quyền nghiệp vụ; ADMIN thường không is_superuser; chặn bypass bằng editor Group/UserPermissions.
4. Session DB Django + AuthSession UUID registry. Sid UUID có thể log/reference; **raw session cookie/key là secret**, không log. Registry không FK bắt buộc tới django_session bị clearsessions xóa.
5. Expiry tuyệt đối 15 phút dùng datetime; request/poll không sliding. Reload còn hạn vẫn login. Lock/reset/archive revoke registry, unlock không hồi sinh cookie cũ.
6. Django authenticate/login/logout dùng đúng cơ chế; không tự encode cookie. SessionAuthentication mặc định 403, phân biệt NOT_AUTHENTICATED/CSRF_FAILED/FORBIDDEN.
7. Login anonymous vẫn csrf_protect, CSRF rotation/token mới sau login; protected mutation không csrf_exempt. API JSON và form Admin đều kiểm CSRF.
8. Đổi password/role/status và user creation đi shared service; form Admin không bypass qua save_model/save_related/user_change_password hoặc m2m groups.
9. Business+AuditLog atomic. Writer chỉ insert, không tự commit. Failed/denied log sau rollback phải thực sự commit được; không raise trong atomic rồi giả failure log vẫn lưu.
10. Login/session registry/audit/session backend thất bại không được để lại cookie authenticate được. Persist Django session trong luồng đã kiểm lỗi; tránh chỉ thử exception ở login() trong khi SessionMiddleware mới save ở response. Có test lỗi session save/commit/registry.
11. Last-admin race: chỉ lock hai User riêng biệt không đủ chống đồng thời hạ hai admin cuối. Dùng lock chung ổn định (ví dụ transaction advisory lock PostgreSQL), sau đó user locks theo thứ tự; mọi đường mutation phải theo cùng thứ tự.
12. Scope ở SQL/queryset trước list/count/pagination/export. ADMIN global scope vẫn kiểm permission/state/safety. Device ngoài scope404, đã VIEW nhưng thiếu action403; Viewer OPERATE không control.
13. Django User permission cache không giữ giữa jobs; worker reload DB. Dispatch MANUAL kiểm session/actor/grant hiện tại; APPLY_CONFIG bền vững theo V1.4, không đổi USER MANUAL thành SYSTEM.
14. Không tạo Device/Command production giả để TV3 tests xanh. Chưa có models TV2 thì patch scope/dispatch chờ dependency; chỉ pure policy tests không được ghi là integration PASS.
15. AuditLog một nguồn, redaction/allowlist/size/finite; không poll log, không secret. Admin read-only chưa đủ append-only DB: runtime DB role cấm UPDATE/DELETE, role retention/migration riêng và có cách verify.
16. Mosquitto default-deny với exact topics và client ID binding; worker không có controller-admin quyền. Broker success response phải match request, không dựa PUBACK. Disable/reconnect denial phải test client đang nối thật.
17. Provision secret chỉ RAM/trả một lần; không DB/outbox/log/CLI argument. Crash mất secret -> NEEDS_REPROVISION do lifecycle TV2; broker adapter không tự biến metadata retry thành khôi phục plaintext.
18. HTTPS/MQTTS CA/hostname đúng; Secure chỉ false ở HTTP local dev; không setInsecure, CORS wildcard hoặc tin forwarded-IP tùy ý để vượt lỗi.

## 6. Lộ trình 12 chặng và phần code mỗi lần

**Thay bản 30 patch định sẵn bằng 12 chặng 00–11.** Mỗi chặng là một kết quả dễ nhận biết. Không bắt học riêng một vòng chỉ để tạo một file helper hoặc một test file.

Điều này giảm phân mảnh và file không cần thiết, không làm biến mất công việc bảo mật. **Một chặng không bắt buộc xuất hết trong một phản hồi.** Nếu quá dài để bạn đọc/copy, Gemini đề xuất một phần như 04a, chờ bạn chọn rồi chỉ xuất phần đó; xong vẫn dừng review. Không tự tạo sẵn thêm 30 patch con.

Mỗi lượt ưu tiên 1–3 file có logic chính và test cần thiết. Boilerplate scaffold có thể có nhiều file rất ngắn, phải liệt kê đầy đủ. Tests thêm vào lúc tính năng được làm, không tách thành một chặng chỉ để kiểm hằng số.

### TV3-DJ-00 — Chốt hiện trạng và cấu trúc, chưa viết code

- Đọc file này, đặc tả mục5/6/7/10, repo và scaffold/nhánh Django của TV2 nếu có.
- Trả bảng có/chưa có; tên package/app label, project paths, model ownership và API/auth/error contracts. Chỉ hỏi phần thiếu thật sự.
- Chốt một project đặt trực tiếp backend/, bản Flask lưu back/backend/, DB mới/test riêng, custom User trước migration, session/CSRF và ba role; không JWT P0.
- Nếu TV2 đã có scaffold thì dùng bản đó, không tạo project thứ hai.
- **Đạt:** TV3 hiểu flow và phần sở hữu; scaffold chung được thống nhất. Dừng, không xuất code01.
- **Tự kiểm:** models/ khác services/ thế nào? Vì sao không dùng lại Flask User bằng import?

### TV3-DJ-01 — Nền Django, custom User và database an toàn

- **File chính:** requirements/.env.example/config/manage.py/AppConfig; accounts/models/{user,auth_session,login_attempt}.py, models/__init__.py, managers.py; core/test_runner.py, config/test_settings.py và tests model/DB guard.
- Tạo ba model accounts một lần để tránh liên tục sửa một models.py lớn. User UUID/AbstractUser, canonical username3–64, status/is_active/must-change, password validators/Argon2, FK lịch sử; Group/Permission kế thừa.
- Settings AUTH_USER_MODEL từ trước migrate; PostgreSQL mới/test riêng, USE_TZ, session DB; root env Flask không tự nạp vào Django.
- **TV3 tự thực hiện:** sau khi chốt00, lưu bản Flask vào back/ theo mục4.3 → kiểm interpreter/venv → tạo Django trực tiếp backend/ → cài dependencies → check target DB → makemigrations accounts → đọc0001 có User → migrate DB mới → tests. Không tạo default auth tables trước rồi mới đổi User.
- Guard test DB chạy trước create/flush/drop, không dùng DB cũ/demo làm test. Password dùng set/check_password, validator có caller rõ, save không mặc định full_clean.
- **Đạt:** project import đúng, migration/User/DB target đúng, hash hoạt động, test guard thật; không xóa/sửa Flask DB/migrations.
- Chặng nền có thể chia môi trường / models / settings-migration khi cần; mỗi phần chỉ làm sau khi bạn chọn, không đưa hàng trăm dòng scaffold lẫn nghiệp vụ trong một lượt.

### TV3-DJ-02 — Hạ tầng lỗi và một nguồn AuditLog

- **File chính:** core/errors.py, middleware.py, views.py; audit/models.py, services.py, admin.py để sau06; tests foundation/audit; settings/urls chỉ sửa phần dùng ngay.
- Request ID/envelope/no-store, CSRF failure JSON cho API, error400/403/404/500/503 không secret; health live/DB readiness không giả worker đã sẵn.
- Audit actor/session/target/function/action/outcome/request/correlation/time/summary/metadata; sanitize allowlist/size/finite; writer chỉ insert, caller giữ transaction; không activity table thứ hai.
- Auth model đã có01. Device identifier audit có thể là snapshot text64 để auth không chờ TV2; FK thật chỉ thêm khi Device có thật và dependency được review.
- **Đạt:** SUCCESS/ACCEPTED/DENIED/FAILED đúng,202 không SUCCESS; spoof/secret bị reject; mutation rollback kéo SUCCESS rollback; failed log sau rollback commit được.
- **Học:** request lifecycle, transaction owner, lỗi do validation khác lỗi hệ thống. Chưa coi Admin read-only hay DB append-only đã xong.

### TV3-DJ-03 — Roles/permissions và tài khoản bootstrap

- **File chính:** accounts/policies/catalog.py, roles.py; commands/seed_roles.py, bootstrap_admin.py; security/password_validators.py nếu chưa có; test_models_roles.py.
- Business action map Django permission thật, seed ADMIN/OPERATOR/VIEWER idempotent; một role/user theo service/form, không user.role thứ hai.
- Admin thường staff=true/superuser=false; bootstrap dùng env/hidden prompt, chạy lại không đổi password/nâng quyền user đã có. Chưa mở editor arbitrary Group/UserPermissions.
- Accounts/audit permissions tạo từ models sau migrate; device permissions chờ model TV2, không bịa ContentType. Chưa port quyền EXT/location/group.
- **Đạt:** seed đúng quyền, password hash/validation đúng, không secret mặc định; nhận biết staff khác permission và superuser không dùng chứng minh RBAC.
- Bootstrap là setup có giới hạn;06 phải nối các mutation quản trị về shared UserService, không để command thành backdoor reset/assign.

### TV3-DJ-04 — Login/logout, session registry, CSRF và limiter

- **File chính theo flow:** security/rate_limit.py → services/sessions.py và auth.py → serializers/auth.py → views/auth.py/urls.py → security/authentication.py/middleware.py; test_auth.py.
- Port HMAC/purpose/advisory-lock limiter bền vững:5 username/20IP/900s, Retry-After; blocked attempts không kéo dài vô hạn window. API và Admin06 dùng cùng helper.
- Django authenticate/login/logout; session key rotate, sid UUID registry, expiry tuyệt đối15phút; verify/revoke hiện tại/all; no sliding, reload còn hạn tiếp tục.
- GET csrf, POST login/logout; anonymous login csrf_protect, rotate token sau login, no-store; lỗi403 phân biệt NOT_AUTHENTICATED/CSRF_FAILED/FORBIDDEN, credential sai401/limit429.
- Registry belongs user/ACTIVE/role/expiry; cookie cũ revoked deny, DB down503; must-change guard có đường đổi password dùng được, không redirect loop.
- **Đạt:** CSRF enforcement thật, dummy verification/thông báo chung, session/audit/registry save failure không trả cookie login hợp lệ; không JWT/Bearer/Web Storage.
- **Cách chia nếu dài:** một lượt session/limiter, một lượt auth service, một lượt HTTP/guard, do bạn chọn và review từng lượt. Không xuất cả chặng nếu không thể cung cấp full files vừa đọc.
- **Học:** authenticate khác login, session cookie khác registry sid, middleware order và SessionMiddleware save lúc response.

### TV3-DJ-05 — Profile và đổi password

- **File chính:** services/users.py, serializers/profile.py và auth.py cần sửa; views/profile.py/urls.py; test_users_admin.py.
- GET/PATCH me: role/permissions/expiry và chỉ full_name được sửa; reject field lạ/đặc quyền, GET không log.
- Đổi password xác minh old, validate12–128/no trim/Django validators, new khác old, limiter purpose riêng, lock/recheck sau chờ.
- set_password, clear must-change, revoke all kể cả hiện tại, login lại; không update_session_auth_hash để giữ phiên cũ trái policy.
- **Đạt:** old sai không đổi hash, replay cookie sau đổi bị deny, temporary-password journey chạy; writer/commit failure rollback, failed attempts/log giữ đúng transaction.
- **Học:** serializer validation không tự thay service validation; actor bản thân khác target user của quản trị06.

### TV3-DJ-06 — Quản trị người dùng và nhật ký trên Django Admin

- **File chính:** services/users.py; forms/auth.py, users.py; views/admin_auth.py, admin_site.py/admin.py; audit/admin.py; test_users_admin.py.
- User create/role/status/lock/archive/unlock/reset password đều cùng service và audit/revoke; role/status đồng bộ staff/active theo policy. Last ADMIN ACTIVE được bảo vệ bằng lock chung ổn định, không chỉ lock từng user.
- Một AdminSite, login/logout cùng auth service/limiter/registry15phút/CSRF; is_staff nhưng role sai deny; must-change không vào các chức năng quản trị.
- Form add/change/reset không bypass service ở save_model/save_related/m2m/user_change_password; không chỉnh raw groups/is_superuser/hash; không bulk delete. Đọc/lock state DB thật, không tin form.instance đã mang dữ liệu submitted.
- Audit list/filter read-only, không add/change/delete/log-read recursion; app read-only chưa thay DB privilege10.
- **Đạt:** test POST form Admin thật, login API/Admin parity, user CRUD/last-admin/reset/old-cookie guard đúng. Race thật kiểm11.
- **Học:** form vs serializer; Admin có sẵn UI, service giữ nghiệp vụ; không viết React Users riêng.

### TV3-DJ-07 — Scope thiết bị và giao diện cấp quyền

- **Gate:** Device/DeviceAccess/migrations thật từ TV2; chưa có thì dừng hỏi fields/chữ ký, không tạo model production giả.
- **File chính:** policies/devices.py, catalog.py/roles.py; devices/models.py chung nếu thiếu DeviceAccess, access_services.py/access_admin.py; test_scope_dispatch.py.
- Unique(user,device), VIEW/OPERATE/expiry/actor; ADMIN grant/revoke service+audit; owner/location/group không cấp ngầm.
- visible_devices queryset SQL; scope trước list/count/export, kiểm create/action; ngoài VIEW404, thiếu action403; Viewer OPERATE vẫn không control.
- Form cấp quyền gọi service, không register DeviceAdmin lần hai; role/grant hết hạn/thu hồi ảnh hưởng request/dispatch sau.
- **Đạt:** model/FK thật không vòng migration, sim-unassigned không lộ, tests policy và endpoint thật phân biệt. TV2/TV4 tự nối query/caller theo bàn giao.
- **Học:** object permission không tự lọc list; permission cache Django không giữ giữa request/jobs.

### TV3-DJ-08 — Quyền gửi lệnh cho worker

- **Gate:** Command/Config/Device thật và dispatcher contract TV2.
- **File chính:** policies/dispatch.py, test_scope_dispatch.py; tài liệu call site, không tự viết lại dispatcher.
- MANUAL USER kiểm actor/session/ACTIVE/control/OPERATE/state/expiry từ DB mới nhất, command đã persist; cross-user sid hoặc revoked grant deny.
- APPLY_CONFIG kiểm snapshot và tác giả trước apply đầu; config đã applied re-sync theo V1.4, không bỏ qua LOCKED/DECOMMISSIONED; không đổi USER MANUAL thành SYSTEM.
- **Đạt:** helper chỉ trả decision/reason, không commit/publish; missing config/unsupported source deny; tests dùng commands DB thật.
- **Học:** background worker không có HTTP request, phải nhận context tường minh; lệnh đã truyền có giới hạn thu hồi.

### TV3-DJ-09 — Broker adapter, ACL và revoke thật

- **File chính:** mqtt/broker_security.py, broker_transport.py, tests/test_broker.py. Lifecycle/DeviceOperation vẫn TV2, credential trên ESP32 TV1.
- Payload exact topics, default-deny send/subscribe/receive, client-ID binding, disabled lúc provision; controller credential khác worker, không wildcard cho node.
- Transport paho version/callback API đã pin, correlation/timeout/lỗi typed; PUBACK không thay response điều khiển. Secret chỉ RAM/trả một lần, không DB/outbox/log/CLI arguments.
- **Đạt unit:** wrong correlation/timeout/error không success, retry không tạo account vô hạn.
- **Đạt integration riêng:** broker thật plugin/bootstrap hoạt động; disabled deny, đúng username/password/clientID mới nối, cross-topic deny, disable client đang nối disconnect + deny reconnect.
- Broker chưa có ghi NOT_RUN; mock không chứng minh ACL/revoke. Crash mất secret cần NEEDS_REPROVISION do lifecycle TV2, không tự lưu plaintext.
- **Học:** role Django khác broker role; request thất lạc response có thể đã được broker thực hiện.

### TV3-DJ-10 — Tích hợp frontend và cấu hình bảo mật bàn giao

- **File chính theo phần được chọn:** frontend/src/api/auth.js và Vite proxy cùng TV4; settings/env/static/proxy/Compose/TLS files thực cùng team; DB privilege/retention hướng dẫn.
- Helper relative /api/v1, credentials same-origin, CSRF memory/rotate, lỗi auth phân biệt; không tự viết lại App.jsx hay auto retry control.
- DEBUG=False/host/trusted origins/Secure/HttpOnly/SameSite, Admin static; MQTTS CA/SAN verify; không setInsecure/wildcard CORS/forwarded-IP trust tùy ý.
- Runtime DB role không UPDATE/DELETE AuditLog, roles retention/migration riêng; root env mẫu chỉ placeholder, không đọc/in secret thật.
- **Đạt:** browser flow có UI TV4, HTTPS/Admin/MQTTS thật và kiểm DB privilege; port8883 hoặc mock chưa đủ. Chưa có môi trường thì tách kết quả helper với integration NOT_RUN.
- **Học:** session cookie tự gửi khác CSRF token; Admin read-only khác DB append-only.
- Tách frontend/web/TLS/DB thành các lượt review khi cần, không gộp cả deployment vào một khối code lớn.

### TV3-DJ-11 — Security integration và hồ sơ demo TV3

- **File chính:** tests integration hiện có, README_TV3.md, docs/TV3_KET_QUA_KIEM_THU.md; code lỗi nếu có thành bản sửa review riêng.
- Client/APIClient(enforce_csrf_checks=True); PostgreSQL TransactionTestCase/connections riêng kiểm hai admin cuối, login đồng thời lock/reset, grant revoke trước dispatch; timeout hữu hạn.
- Cookie replay/expiry bằng clock injection, Admin parity, mass assignment, lỗi save session/registry/audit/commit, scope endpoint thật; log failed không mất do rollback.
- **Đạt:** chức năng đã làm có evidence thực, compile/import/migrations đúng; race không làm mất hết admin; không ghi PASS từ mô tả hoặc unit mock.
- Bàn giao setup/migrations/seed/auth/Admin/policy signatures, versions và test matrix actual; CHỜ_TV2/CHỜ_TV4/NOT_RUN/FAIL ghi đúng.
- **Học/demo:** giải thích request đi qua URL → view → serializer → service/policy → model/DB → response/audit; một lệnh pending bị revoke phải bị chặn.
- Không tự commit/push/release. TV3 quyết định sau review; không làm lại buổi thuyết trình đã xong.

## 7. Vòng lặp đọc, copy và review

1. Gemini trình bày chặng hiện tại và đúng phần sắp làm. Nếu quá dài, đề xuất phạm vi một lượt trước khi viết code.
2. Gemini xuất full files trong chat, chú thích hàm/biến và luồng; không chạm repo hoặc chạy lệnh mutate.
3. TV3 đọc, tự copy; file sửa phải là bản mới nhất, không đè code TV2/TV4.
4. TV3 tự chạy lệnh đã giải thích, gửi log che secret và code cho reviewer.
5. Reviewer kết luận CHẤP NHẬN / CẦN SỬA / CHƯA ĐỦ BẰNG CHỨNG, phân biệt review tĩnh với integration.
6. Còn lỗi chỉ sửa phần hiện tại; mỗi lần vẫn full files. Chỉ đi tiếp khi TV3 xác nhận review và yêu cầu rõ phần/chặng tiếp.

Một chặng nhiều lượt vẫn phải dừng sau mỗi lượt. Không tự phát sinh danh sách patch con dài ngay ngày đầu.

### 7.1 Gói gửi reviewer

```text
Review: TV3-DJ-XX hoặc XXa — <mục tiêu phần đã làm>
Baseline: <commit/trạng thái>
Đã copy: <file mới/file sửa, migration tự sinh>
File chung TV2/TV4 cần chú ý: <nếu có>
Tôi tự chạy: <command + working directory + output đã che secret>
Tôi hiểu flow: <3–5 câu>
Tôi chưa hiểu: <hàm/biến/dòng cụ thể>
Nhờ đọc và review; không tự sửa source code.
```

Không gửi password/raw session key/cookie/.env/private key. Sid UUID registry/request ID có thể dùng correlation; phân biệt với cookie secret.

### 7.2 Theo dõi 12 chặng

Trạng thái: NOT_STARTED → CODE_PROPOSED → COPIED_BY_TV3 → CHECKED_BY_TV3 → REVIEW_ACCEPTED; lỗi NEEDS_FIX, thiếu đầu vào WAITING_DEPENDENCY. REVIEW_ACCEPTED tĩnh chưa đồng nghĩa integration PASS. Nếu một phần của chặng hoàn thành, ghi phần đó, không đánh dấu toàn chặng đạt.

| Chặng | Phụ thuộc chính | Trạng thái ban đầu |
|---|---|---|
| 00 — Chốt cấu trúc | Repo/TV2 scaffold | NOT_STARTED |
| 01 — Nền Django/User/DB | 00 + môi trường PostgreSQL | NOT_STARTED |
| 02 — Lỗi và AuditLog | Models accounts01 | NOT_STARTED |
| 03 — Roles/bootstrap | 01/02, permissions sau migrate | NOT_STARTED |
| 04 — Session/CSRF/login | 01–03 | NOT_STARTED |
| 05 — Profile/password | 04 | NOT_STARTED |
| 06 — User Admin/logs | 02–05 | NOT_STARTED |
| 07 — Scope/grants | 03/04/06 + models Device thật TV2 | NOT_STARTED |
| 08 — Dispatch policy | 07 + Command/Config thật TV2 | NOT_STARTED |
| 09 — Broker ACL/revoke | MQTT contract, broker TV2/bo TV1 | NOT_STARTED |
| 10 — Frontend/TLS/DB privilege | 04–06, môi trường TV1/TV2/TV4 | NOT_STARTED |
| 11 — Kiểm chứng/bàn giao | Các phần thực hiện có code/evidence | NOT_STARTED |

Gemini chỉ đề xuất cập nhật tiến độ trong chat, không tự sửa bảng hay giả bạn đã copy.

## 8. Mốc bàn giao và cách giữ kế hoạch vừa sức

- **Sớm:**01–03 bàn giao User/schema/role contract cho TV2;04–05 bàn giao auth/cookie/CSRF cho TV4. Không chờ Admin hoàn thiện mới nối dữ liệu thật.
- **Quản trị:**06 có UI quản lý đúng service;07–08 đợi model thật và tích hợp quyền endpoint/worker.
- **Broker:**09 độc lập tương đối, có thể ưu tiên khi đang chờ TV2 models, nhưng phải do TV3 yêu cầu rõ; Gemini không tự nhảy.
- **Trước demo:**10–11 kiểm môi trường và bằng chứng thật; ghi rõ phần chưa kiểm. Không dành tuần này làm lại slide đã thuyết trình.
- **Không hứa theo số chặng:** 12 chặng không phải 12 ngày hoặc 12 lần trả lời chắc chắn. Người mới cần thời gian đọc; tiến độ tính theo tính năng đã hiểu/chạy được.
- **Giảm công thực sự:** dùng Django Admin và native auth, hợp audit/activity, gom tests theo feature, không tạo repository/base controller/EXT hay file cho mỗi hàm.
- **Không giảm bằng cách bỏ điều kiện:** không bỏ revoke/scope/CSRF/safety hoặc ghi PASS khi chưa chạy để rút số patch.

Bản đầu định sẵn 30 patch quá chi li cho việc theo dõi. Bản này giữ 12 mốc nghiệp vụ, còn kích thước mỗi lượt dựa trên code thật và tốc độ đọc của bạn; việc chia nhỏ phải có lý do, không tự động áp đủ 30 lượt cũ.

## 9. Prompt khởi động gửi Gemini

Gửi file này cùng đặc tả V1.4 và repo/file cần đọc. Copy đoạn sau:

```text
Bạn hướng dẫn tôi phần TV3 bằng Django5.2/DRF. Tôi mới học Django.
Đọc TV3_KE_HOACH_DJANGO_CHO_GEMINI.md và đặc tả V1.4 trước.
Kế hoạch hiện có 12 chặng 00–11, không dùng bản 30 patch cũ.

TỔ CHỨC:
- Project Django đặt thẳng backend/, mã Flask lưu riêng back/backend/.
- Không tạo tầng django/ bên trong backend/.
- Theo app nghiệp vụ, trong accounts tách models/serializers/views/services/
  policies/security/forms như tài liệu. Giữ entrypoints Django đúng quy ước.
- serializers tương ứng validation request/DTO; views tương ứng controller.
- Không tự thêm repository/base class hoặc một file cho từng hàm.

CÁCH LÀM:
- Chỉ đọc và trả lời chat. Không tự sửa/tạo/xóa file hoặc chạy lệnh mutate.
- Tôi tự đọc/copy/chạy install/makemigrations/migrate/test/git.
- Mỗi lần chỉ phần vừa đủ đọc của chặng hiện tại, full nội dung mỗi file.
- File sửa phải đọc bản mới nhất; thiếu thì hỏi, không đoán hoặc ghi đè.
- Chú thích tiếng Việt rõ file, hàm/lớp, tham số, biến/field, input/output,
  DB/session/transaction và caller. Giải thích cơ chế Django khi mới gặp.
- Có flow đúng/lỗi, lệnh để tôi tự chạy, expected output và checklist.
- Nếu cả chặng quá dài, đề xuất một phần nhỏ trước, chờ tôi chọn;
  không tự xuất cả chặng hoặc tạo sẵn thêm30 patch con.
- Không cắt code bằng dấu ba chấm, không pass/TODO/allow-all cho business thiếu.
- DỪNG sau mỗi lượt để tôi copy, chạy và gửi reviewer.
- Chỉ tiếp tục khi tôi xác nhận review và yêu cầu rõ phần/chặng tiếp.
- Lỗi hiện tại sửa hiện tại; không tự sửa repo/contract để vượt test.
- Không đọc/in secrets, không coi chưa báo lỗi là PASS.

BẮT ĐẦU TV3-DJ-00, CHƯA VIẾT CODE:
Kiểm kê scaffold/model TV2 thật, trình bày structure/contracts cần chốt,
chỉ hỏi phần còn thiếu rồi DỪNG để tôi xác nhận.
```

Mẫu tiếp tục:

```text
Phần TV3-DJ-XX(a) tôi đã copy và tự kiểm <lệnh/kết quả>.
Review: <kết luận thực>; chưa chạy: <liệt kê>.
Hãy đề xuất duy nhất phần tiếp theo <phạm vi>.
Full files + giải thích, không sửa repo, xong dừng.
```

Mẫu sửa:

```text
Chưa chuyển phần/chặng. Sửa TV3-DJ-XX(a) theo nhận xét <...>.
Đây là file hiện tại và output đã che secret: <...>.
Giải thích nguyên nhân, full files cần sửa, checklist kiểm lại rồi dừng.
```

## 10. Checklist reviewer không được bỏ qua

- [ ] Đúng patch ID, dùng file hiện tại; Gemini không thực hiện ghi repo/lệnh mutate.
- [ ] Code từng file đầy đủ, không thiếu import/callable/hook hoặc phụ thuộc patch tương lai.
- [ ] TV3 hiểu hàm/biến/luồng, biết ai gọi và dữ liệu thay đổi ở đâu.
- [ ] Không Django/Flask imports lẫn nhau, không dự án/accounts/user model thứ hai.
- [ ] User trong migration đầu; DB test guard trước create/flush; migration dependency thật.
- [ ] Login API/Admin đều CSRF/limiter/registry/expiry/audit; không default Admin bypass.
- [ ] Cookie session secret không log; sid UUID registry không bị nhầm với secret cookie.
- [ ] Lock/reset/archive và role/grant revoke ảnh hưởng request/dispatch; unlock không hồi sinh.
- [ ] Last-admin được serialize bằng lock chung, race test PostgreSQL thực.
- [ ] Password validators/hash/must-change policy đúng; không auto giữ current session sai policy.
- [ ] Log atomic/redaction/actor/outcome đúng; failed log không rollback nhầm; append-only app/DB có bằng chứng riêng.
- [ ] Scope lọc SQL và kiểm detail/create/action; viewer OPERATE vẫn deny control; count/export không lộ.
- [ ] Worker dùng fresh DB/context tường minh, config re-sync không bypass locked state.
- [ ] Broker payload/transport có correlation/timeout, credential riêng, ACL default-deny/exact topics.
- [ ] Mock/unit vs integration/broker/TLS thực được ghi rõ; chưa chạy ghi NOT_RUN.
- [ ] Không sửa schema/API/MQTT/đồ thị UI TV2/TV4 để làm test của TV3 xanh.

## 11. Tài liệu chính thức để Gemini đối chiếu

Tài liệu đã được tham khảo khi lập kế hoạch; Gemini phải đọc đúng version đang cài khi viết từng patch. Các quyết định DeviceAccess/registry/outbox/patch là thiết kế dự án, không mặc định có sẵn trong Django.

- [Custom User và auth customization Django5.2](https://docs.djangoproject.com/en/5.2/topics/auth/customizing/).
- [Django auth, password và Groups/Permissions](https://docs.djangoproject.com/en/5.2/topics/auth/default/).
- [Sessions](https://docs.djangoproject.com/en/5.2/topics/http/sessions/), [CSRF/login CSRF](https://docs.djangoproject.com/en/5.2/ref/csrf/).
- [Django Admin hooks](https://docs.djangoproject.com/en/5.2/ref/contrib/admin/).
- [Transactions](https://docs.djangoproject.com/en/5.2/topics/db/transactions/), [advanced testing](https://docs.djangoproject.com/en/5.2/topics/testing/advanced/).
- [DRF authentication](https://www.django-rest-framework.org/api-guide/authentication/), [permissions và giới hạn object/list](https://www.django-rest-framework.org/api-guide/permissions/).
- [Mosquitto Dynamic Security](https://mosquitto.org/documentation/dynamic-security/).

**Điểm bắt đầu thực tế:** chưa code Django nào được đề xuất/copy/test theo kế hoạch này. Việc hoàn thành tài liệu không thay trạng thái triển khai. Gửi Gemini tài liệu và yêu cầu chặng00; sau đó mỗi phần bạn tự copy và gửi lại để review.
