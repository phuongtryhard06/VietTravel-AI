# VietTravel AI — Vietnam Smart Travel Booking & Multi-Agent Assistant API

<div align="center">

![Python](https://img.shields.io/badge/Python-3.10+-3776AB.svg?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0.2-000000.svg?logo=flask&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-4169E1.svg?logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg?logo=docker&logoColor=white)
![Google Gemini](https://img.shields.io/badge/Google_Gemini-Flash_2.5-4285F4.svg?logo=google&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

**Hệ sinh thái API & Web Trợ lý Du lịch Thông minh Việt Nam với Hệ thống AI Đa Tác Tử (Multi-Agent System)**

[Tổng quan](#-tổng-quan) • [Kiến trúc Đa Tác Tử](#-kiến-trúc-hệ-thống-ai-đa-tác-tử-multi-agent) • [Tính năng](#-tính-năng-chính) • [Hướng dẫn Cài đặt & Chạy](#-hướng-dẫn-cài-đặt--khởi-chạy-dự-án) • [Tài liệu API](#-tài-liệu-api) • [Cấu trúc Thư mục](#-cấu-trúc-thư-mục-dự-án)

</div>

---

## 📋 Tổng quan

**VietTravel AI (ChatbotTravel Server)** là hệ sinh thái máy chủ thông minh phục vụ ngành du lịch Việt Nam, được phát triển như đồ án tốt nghiệp chuyên ngành Công nghệ Thông tin tại Trường Đại học Giao Thông Vận Tải.

Dự án chuyển đổi từ mô hình chatbot hỏi đáp truyền thống sang **Hệ thống AI Đa Tác Tử (Multi-Agent System)** chuyên sâu về **Tư vấn Điểm đến, Chỉ đường & Dự trù Chi phí, Lập Lịch trình & Đặt Chuyến đi**:
* **Dữ liệu địa lý thực tế**: Thay thế hoàn toàn vector search nội bộ bằng **PostgreSQL POI Engine** chứa **hơn 30.800+ địa điểm thực tế trên khắp 63 tỉnh thành Việt Nam** (được chuẩn hóa từ OpenStreetMap), loại bỏ 100% tình trạng nhầm lẫn vùng miền (hallucination).
* **Tra cứu Web thời gian thực (Google Search Grounding)**: Tự động cập nhật thời tiết, giá vé, sự kiện và xu hướng du lịch mới nhất.
* **Giao diện Web cao cấp tích hợp sẵn**: Thiết kế sang trọng (Deep Obsidian Navy & Royal Electric Blue), hiển thị trực quan thẻ địa điểm, bản đồ số và công cụ lập lịch trình.

---

## 🧠 Kiến trúc Hệ thống AI Đa Tác Tử (Multi-Agent)

Hệ thống xử lý thông tin qua chuỗi phối hợp giữa các tác tử chuyên biệt:

```mermaid
flowchart TD
    User([Khách du lịch / Người dùng]) --> UI[Web Client / Swagger API]
    UI --> Controller[Travel Chatbot Controller]
    
    subgraph MultiAgentPipeline [Hệ Thống Đa Tác Tử VietTravel AI]
        Agent1[Tác tử 1: Bóc tách Ý định & Thực thể\nGemini Intent & Extraction Agent]
        Agent2[Tác tử 2: Truy vấn Địa điểm Chuẩn xác\nPostgreSQL POI Engine - 30.800+ POIs]
        Agent3[Tác tử 3: Tra cứu Web Thời gian thực\nGoogle Search Grounding Agent]
        Agent4[Tác tử 4: Chuyên gia Hướng dẫn & Đặt tour\nTravel Tour Guide & Booking Agent]
        
        Agent1 -->|Khu vực, danh mục, thời gian, ngân sách| Agent2
        Agent1 -.->|Khi cần tin tức mới / Địa bàn ngoài DB| Agent3
        Agent2 -->|Danh sách POI kèm tọa độ GPS| Agent4
        Agent3 -->|Dữ liệu giá vé, đường đi thực tế| Agent4
    end

    Controller --> MultiAgentPipeline
    Agent4 --> Response([Phản hồi Chuẩn cấu trúc: Điểm đến - Đường đi - Chi phí - Mẹo đặt tour])
    Response --> UI
```

1. **Tác tử 1 — Intent & Extraction Agent**: Phân tích câu hỏi tự nhiên của khách hàng (đa ngôn ngữ), bóc tách: Tỉnh/thành phố, quận huyện, loại hình (cà phê, di tích, khách sạn, ẩm thực...), ngân sách, thời gian dự kiến.
2. **Tác tử 2 — PostgreSQL POI Engine**: 
   - Tìm kiếm theo bán kính không gian tọa độ (Haversine Spatial Formula) và bộ lọc thuộc tính trong bảng `osm_attractions_staging`.
   - Nếu địa điểm nằm ngoài vùng phủ sóng (ví dụ: các huyện vùng sâu hoặc chưa có POI): trả về danh sách rỗng, **tuyệt đối không hiển thị sai địa điểm**.
3. **Tác tử 3 — Real-Time Web Grounding Agent**: Tra cứu dữ liệu Internet qua Google Search để lấy thông tin tức thời về giá vé, sự kiện lễ hội, thông tin liên hệ và tình trạng mở cửa.
4. **Tác tử 4 — Travel Tour Guide & Booking Agent**: Tổng hợp toàn bộ dữ liệu thành câu trả lời chuyên nghiệp gồm:
   - 📍 **Điểm đến nổi bật & Trải nghiệm chính**: Tên địa điểm, tọa độ GPS, đặc trưng nổi bật.
   - 🚗 **Đường đi & Di chuyển**: Tuyến đường (cao tốc/quốc lộ), phương tiện (ô tô, xe máy, xe khách/limousine kèm giá vé và thời gian chạy).
   - 💰 **Chi phí dự kiến & Giá vé**: Bảng dự trù chi phí chi tiết theo ngày.
   - ⏰ **Thời gian lý tưởng**: Mùa đẹp nhất trong năm hoặc khung giờ đẹp trong ngày.
   - 💡 **Mẹo thực tế & Hướng dẫn đặt tour/đặt xe**: Lưu ý an toàn, mẹo đặt dịch vụ.
5. **Hệ thống Lập lịch trình Đa Tác Tử (Tab Lịch trình - Multi-Agent Planner)**:
   - **Orchestrator**: Điều phối quy trình lập lịch.
   - **POI Search Tool**: Thu thập các điểm tham quan phù hợp sở thích.
   - **Planner Agent**: Tối ưu cung đường di chuyển (km), phân bổ thời gian ăn chơi từng chặng.
   - **Constraint Validator**: Kiểm tra giờ mở cửa, tính hợp lý và ngăn ngừa trùng lặp.

---

## ✨ Tính năng Chính

### 1. 🤖 AI Chatbot Tư vấn Du lịch & Đặt lịch
- Tự động nhận diện ngôn ngữ (Tiếng Việt, Anh, Trung, Hàn, Nhật).
- Trả lời thông minh theo đúng nghiệp vụ lữ hành, không trả lời lan man.
- Hiển thị các thẻ địa điểm tương tác trực tiếp (`Chi tiết`, `+ Thêm vào lịch`).
- Nút bật/tắt **Web Real-time** linh hoạt ngay trên khung chat.

### 2. 📅 Lập Lịch trình Tự động (AI Multi-Agent Itinerary)
- Nhập điểm khởi hành, điểm đến, số ngày du lịch (1 ngày, 2N1Đ, 3N2Đ...), ngân sách và sở thích.
- AI tự động tính toán khoảng cách di chuyển giữa các chặng (km), thứ tự tham quan tối ưu.
- Lưu trữ lịch trình vào tài khoản người dùng, xuất bản đồ hành trình trực quan.

### 3. 🗺️ Quản lý Kho Dữ liệu Địa điểm Toàn quốc
- Quản lý hơn **30.800+ địa điểm** thực tế được chuẩn hóa trên PostgreSQL:
  - 8.500+ Nhà hàng, ẩm thực địa phương
  - 6.800+ Quán cà phê, điểm hẹn
  - 4.000+ Khách sạn, nhà nghỉ, homestay
  - 700+ Điểm tham quan du lịch, danh lam thắng cảnh
  - 240+ Di tích lịch sử, bảo tàng, văn hóa

### 4. 👤 Xác thực & Tài khoản Người dùng
- Đăng ký / Đăng nhập an toàn với mã hóa mật khẩu và **JWT Token**.
- Xác thực email kích hoạt tài khoản qua **Mã OTP**.
- Chức năng quên mật khẩu và đặt lại mật khẩu bảo mật qua email SMTP.

### 5. ⏰ Tự động Gửi Email Nhắc nhở Chuyến đi (Scheduler Daemon)
- Background Daemon tự động quét các lịch trình sắp diễn ra (chu kỳ 5 phút).
- Tự động gửi email thông báo nhắc nhở ngày giờ, điểm đến cho khách hàng.

---

## 🛠️ Công nghệ Sử dụng

| Thành phần | Công nghệ / Thư viện | Vai trò |
| :--- | :--- | :--- |
| **Backend Framework** | Python 3.10+, Flask 3.0.2 | Máy chủ RESTful API & điều hướng |
| **Tài liệu API** | Flask-RESTX (Swagger UI) | Sinh tài liệu tương tác tại `/docs` |
| **Cơ sở Dữ liệu** | PostgreSQL 15+ & pgvector | Lưu trữ POI toàn quốc, người dùng, lịch trình |
| **Container Hóa** | Docker, Docker Compose | Khởi chạy môi trường PostgreSQL độc lập |
| **Mô hình AI chính** | Google Gemini (2.5 Flash / 1.5 Flash) | Xử lý ý định, lập lịch và tổng hợp tư vấn |
| **Web Grounding** | Google Search Tool API | Tra cứu thông tin thời gian thực |
| **ORM & Kết nối DB** | SQLAlchemy, Psycopg2-binary | Thao tác dữ liệu quan hệ và spatial SQL |
| **Hệ thống Email** | Flask-Mail, SMTP BKNS | Gửi OTP đăng ký và email nhắc lịch trình |
| **Bản đồ số** | Mapbox GL JS, OpenStreetMap | Hiển thị bản đồ và định vị tọa độ GPS |

---

## 📦 Hướng dẫn Cài đặt & Khởi chạy Dự án

### Yêu cầu Tiên quyết
* **Hệ điều hành**: Windows 10/11, macOS, hoặc Ubuntu Linux.
* **Python**: Phiên bản 3.10 trở lên.
* **Docker Desktop**: Để chạy container PostgreSQL.
* **Git**: Để quản lý mã nguồn.

---

### Bước 1: Clone Repository
```bash
git clone https://github.com/phuongtryhard06/VietTravel-AI.git
cd VietTravel-AI
```

---

### Bước 2: Thiết lập Môi trường Ảo (Virtualenv)
```bash
# Trên Windows (PowerShell):
python -m venv venv
.\venv\Scripts\activate

# Trên Linux / macOS:
python3 -m venv venv
source venv/bin/activate
```

---

### Bước 3: Cài đặt Thư viện Phụ thuộc
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

### Bước 4: Khởi chạy Database PostgreSQL bằng Docker
Dự án đã cấu hình sẵn file `docker-compose.dev.yml` độc lập, tránh xung đột cổng:
```bash
docker compose -f docker-compose.dev.yml up -d
```
> **Lưu ý**: Container PostgreSQL sẽ mở cổng tại `127.0.0.1:5432`, tự động tạo volume `chatbottravel_pgdata` lưu trữ dữ liệu an toàn.

---

### Bước 5: Cấu hình File Biến Môi trường (`.env`)
Tạo hoặc kiểm tra file `.env` tại thư mục gốc với các thông số sau:

```env
# Database Configuration (Khớp với Docker Compose)
DB_HOST=localhost
DB_NAME=scapedata_db
DB_USER=postgres
DB_PASSWORD=postgres
DB_PORT=5432
DATABASE_POSTGRESQL_URL=postgresql://postgres:postgres@localhost:5432/scapedata_db

# Bảo mật & JWT
SECRET_KEY=chatbot_travel_secret_key_2026_tris_eni

# Google Gemini API (Bắt buộc cho hệ thống Đa Tác Tử & Lập Lịch Trình)
GEMINI_API_KEY=your_actual_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash

# Cấu hình Gửi Email (SMTP)
MAIL_SERVER=smtp81196.bkns.com.vn
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=support_it@quanit206.id.vn
MAIL_PASSWORD=your_email_password
MAIL_DEFAULT_SENDER=support_it@quanit206.id.vn

# Bản đồ số (Mapbox)
MAPBOX_ACCESS_TOKEN=your_mapbox_token_here
```

---

### Bước 6: Khởi tạo Bảng Dữ liệu & Nạp Địa điểm Toàn quốc
Chạy các tập lệnh nạp dữ liệu địa lý thực tế vào cơ sở dữ liệu:

```bash
# 1. Chạy migration tạo bảng notifications và cập nhật schema
python migrate_notifications.py

# 2. Nhập dữ liệu địa điểm OpenStreetMap (hơn 30.800 POI) vào bảng osm_attractions_staging
python scripts/import_all_osm_poi.py --apply --batch-size 1000
```

---

### Bước 7: Khởi chạy Máy chủ Flask
```bash
python main.py
```

Server sẽ tự động:
1. Kết nối cơ sở dữ liệu PostgreSQL.
2. Khởi động vòng lặp kiểm tra nhắc nhở lịch trình (**Itinerary Reminder Scheduler**).
3. Lắng nghe tại: **`http://127.0.0.1:5000`**.

---

### Bước 8: Trải nghiệm Ứng dụng
* **Giao diện Web Trực quan**: Truy cập `http://localhost:5000/` trên trình duyệt để trải nghiệm toàn bộ giao diện tư vấn đa tác tử và lập lịch trình.
* **Tài liệu Tương tác Swagger API**: Truy cập `http://localhost:5000/docs` để kiểm thử toàn bộ API endpoints.

---

## 📚 Tài liệu API (API Endpoints)

Base URL: `http://localhost:5000/api`

### 1. Phân hệ Tư vấn Đa Tác Tử (`/api/travel-chatbot`)
| Phương thức | Đường dẫn | Chức năng |
| :--- | :--- | :--- |
| `POST` | `/api/travel-chatbot/search` | Gửi câu hỏi du lịch đến hệ thống 4 tác tử AI (hỗ trợ bật/tắt Web Real-time) |
| `GET` | `/api/travel-chatbot/metadata` | Lấy danh sách địa điểm phân trang trực tiếp từ PostgreSQL |
| `GET` | `/api/travel-chatbot/embeddings` | Trả về thông báo deprecation (ChromaDB đã được gỡ bỏ) |

**Ví dụ Request gửi câu hỏi:**
```bash
curl -X POST http://localhost:5000/api/travel-chatbot/search \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Gợi ý cho tôi các quán cà phê đẹp ở Quận 1 để làm việc",
    "enable_web_search": true
  }'
```

---

### 2. Phân hệ Lập Lịch trình Đa Tác Tử (`/api/itinerary`)
| Phương thức | Đường dẫn | Chức năng |
| :--- | :--- | :--- |
| `POST` | `/api/itinerary/multi-agent/plan-draft` | Tự động tạo bản thảo lịch trình tối ưu bằng AI Multi-Agent |
| `POST` | `/api/itinerary/create` | Tạo mới lịch trình và lưu vào CSDL |
| `GET` | `/api/itinerary/list` | Lấy danh sách lịch trình của người dùng |
| `PUT` | `/api/itinerary/update` | Cập nhật thông tin/thời gian lịch trình |
| `DELETE`| `/api/itinerary/delete` | Xóa lịch trình |

**Ví dụ Request lập lịch tự động:**
```bash
curl -X POST http://localhost:5000/api/itinerary/multi-agent/plan-draft \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Chuyến khám phá ẩm thực Quận 1",
    "city": "Thành phố Hồ Chí Minh",
    "destination": "Quận 1",
    "days": 1,
    "budget": "Trung bình",
    "preferences": ["Cà phê", "Ẩm thực đường phố"]
  }'
```

---

### 3. Phân hệ Xác thực Tài khoản (`/api/auth`)
| Phương thức | Đường dẫn | Chức năng |
| :--- | :--- | :--- |
| `POST` | `/api/auth/register` | Đăng ký tài khoản và gửi mã OTP qua email |
| `POST` | `/api/auth/verify-otp` | Xác thực mã OTP để kích hoạt tài khoản |
| `POST` | `/api/auth/login` | Đăng nhập nhận JWT Token |
| `POST` | `/api/auth/forgot-password`| Gửi mã OTP khôi phục mật khẩu |
| `POST` | `/api/auth/reset-password` | Đặt lại mật khẩu mới bằng OTP |

---

### 4. Phân hệ Quản lý Tin nhắn & Cuộc trò chuyện (`/api/chatting`)
| Phương thức | Đường dẫn | Chức năng |
| :--- | :--- | :--- |
| `POST` | `/api/chatting/conversations` | Tạo phiên trò chuyện mới cho người dùng |
| `GET` | `/api/chatting/conversations/list` | Lấy danh sách lịch sử hội thoại |
| `POST` | `/api/chatting/messages` | Gửi tin nhắn và nhận phản hồi từ AI |
| `GET` | `/api/chatting/conversations/messages` | Lấy danh sách tin nhắn theo phiên |

---

### 5. Phân hệ Thông báo & Nhắc nhở (`/api/notification`)
| Phương thức | Đường dẫn | Chức năng |
| :--- | :--- | :--- |
| `GET` | `/api/notification/list` | Lấy danh sách thông báo lịch trình |
| `PUT` | `/api/notification/<id>/read` | Đánh dấu thông báo đã đọc |
| `DELETE`| `/api/notification/<id>` | Xóa thông báo |

---

## 📁 Cấu trúc Thư mục Dự án

```
ChatbotTravel_server/
├── docker-compose.dev.yml            # Cấu hình container PostgreSQL 15 & pgvector
├── main.py                           # Điểm khởi chạy ứng dụng Flask & Scheduler
├── requirements.txt                  # Danh mục thư viện Python
├── .env                              # Biến môi trường (DB, AI API, SMTP)
├── README.md                         # Tài liệu hướng dẫn dự án
├── src/
│   ├── __init__.py                   # Khởi tạo Flask app, database & đăng ký API namespaces
│   ├── controllers/                  # Tầng tiếp nhận & điều hướng yêu cầu API
│   │   ├── auth_controller.py        # Controller xác thực & OTP
│   │   ├── travel_chatbot_controller.py # Controller Chatbot Đa Tác Tử (PostgreSQL + Gemini)
│   │   ├── itinerary_controller.py   # Controller Lập lịch trình & Multi-Agent Planning
│   │   ├── chatting_controller.py    # Controller quản lý lịch sử hội thoại
│   │   └── notification_controller.py# Controller thông báo nhắc lịch
│   ├── models/                       # Tầng định nghĩa thực thể SQLAlchemy
│   │   ├── user.py                   # Model Người dùng
│   │   ├── attraction.py             # Model Địa điểm chính thức
│   │   ├── itinerary.py              # Model Lịch trình & Chặng di chuyển
│   │   ├── conversation.py           # Model Phiên hội thoại
│   │   ├── message.py                # Model Tin nhắn
│   │   └── notification.py           # Model Thông báo
│   ├── services/                     # Tầng xử lý nghiệp vụ & logic AI
│   │   ├── travel_chatbot_service.py # PostgreSQL POI Engine + Gemini Grounding
│   │   ├── itinerary_service.py      # Nghiệp vụ quản lý & tính toán lịch trình
│   │   ├── chatting_service.py       # Nghiệp vụ hội thoại
│   │   ├── auth_service.py           # Nghiệp vụ xác thực & băm mật khẩu
│   │   └── ai/                       # Phân hệ AI Đa Tác Tử chuyên sâu
│   │       ├── orchestrator.py       # Điều phối viên luồng lập lịch trình
│   │       ├── agents/               # Các tác tử AI độc lập
│   │       │   ├── planner_agent.py  # Tác tử lập kế hoạch lộ trình di chuyển
│   │       │   └── validator_agent.py# Tác tử kiểm định ràng buộc thời gian/không gian
│   │       └── tools/                # Công cụ hỗ trợ của tác tử
│   │           └── poi_search_tool.py# Công cụ truy vấn bảng osm_attractions_staging
│   ├── templates/
│   │   └── index.html                # Giao diện web người dùng VietTravel AI
│   └── static/
│       ├── css/
│       │   └── style.css             # Giao diện Luxury Modern Travel (Obsidian & Blue)
│       └── js/
│           └── app.js                # Xử lý tương tác AJAX, bản đồ số & render markdown
└── scripts/
    ├── import_all_osm_poi.py         # Script nạp 30.800+ địa điểm vào PostgreSQL
    └── pilot_import_osm.py           # Script nạp thử nghiệm dữ liệu mẫu
```

---

## 👥 Thông tin Thực hiện Dự án

* **Sinh viên thực hiện**: Nguyễn Minh Phương, Thân Phú Cường
* **Giảng viên hướng dẫn**: TS. Trần Quang Quý
* **Trường**: Trường Đại học Công nghệ Thông tin và Truyền thông (ICTU)
* **Khoa**: Công nghệ Thông tin
* **Năm thực hiện**: 2026
* **Mã nguồn GitHub**: [https://github.com/phuongtryhard06/VietTravel-AI](https://github.com/phuongtryhard06/VietTravel-AI)

---

<div align="center">

**⭐ VietTravel AI — Trợ lý Du lịch Thông minh Đa Tác Tử đồng hành cùng mọi chuyến đi! ⭐**

</div>
