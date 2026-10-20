# Cấu hình Database PostgreSQL

## 📋 Thông tin cần thiết

Để kết nối với PostgreSQL, bạn cần các thông tin sau:

| Thông tin | Mô tả | Ví dụ |
|-----------|-------|-------|
| `DB_HOST` | Địa chỉ máy chủ PostgreSQL | `localhost` hoặc `127.0.0.1` |
| `DB_NAME` | Tên database | `scapedata_db` |
| `DB_USER` | Tên người dùng database | `postgres` |
| `DB_PASSWORD` | Mật khẩu database | `your_password` |
| `DB_PORT` | Cổng PostgreSQL | `5432` (mặc định) |

## 🔧 Cách thiết lập

### 1. Tạo file `.env` trong thư mục gốc

Tạo file `.env` với nội dung sau:

```env
# Database Configuration
DB_HOST=localhost
DB_NAME=scapedata_db
DB_USER=postgres
DB_PASSWORD=your_password
DB_PORT=5432

# Alternative: Full PostgreSQL URL
DATABASE_POSTGRESQL_URL=postgresql://postgres:your_password@localhost:5432/scapedata_db

# JWT Secret Key
SECRET_KEY=your_secret_key_here

# Email Configuration
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=your_email@gmail.com
MAIL_PASSWORD=your_email_password
MAIL_DEFAULT_SENDER=your_email@gmail.com

# Frontend URL
FRONTEND_URL=http://localhost:3000
```

### 2. Các trường hợp DB_HOST khác nhau

#### Localhost (máy tính của bạn)
```env
DB_HOST=localhost
# hoặc
DB_HOST=127.0.0.1
```

#### PostgreSQL trên Docker
```env
DB_HOST=localhost
DB_PORT=5432
```

#### PostgreSQL trên server khác
```env
DB_HOST=your_server_ip
DB_PORT=5432
```

#### PostgreSQL trên cloud (AWS RDS, Google Cloud SQL, etc.)
```env
DB_HOST=your-db-instance.region.rds.amazonaws.com
DB_PORT=5432
```

## 🚀 Cách kiểm tra kết nối

### 1. Kiểm tra PostgreSQL có đang chạy không

```bash
# Windows
net start postgresql

# macOS/Linux
sudo systemctl status postgresql
# hoặc
brew services list | grep postgresql
```

### 2. Kết nối bằng psql

```bash
psql -h localhost -U postgres -d scapedata_db
```

### 3. Kiểm tra bằng Python

Tạo file `test_db_connection.py`:

```python
import psycopg2
from src.config.config import Config

def test_connection():
    try:
        conn = psycopg2.connect(
            host=Config.DB_HOST,
            database=Config.DB_NAME,
            user=Config.DB_USER,
            password=Config.DB_PASSWORD,
            port=Config.DB_PORT
        )
        print("✅ Kết nối database thành công!")
        conn.close()
    except Exception as e:
        print(f"❌ Lỗi kết nối: {e}")

if __name__ == "__main__":
    test_connection()
```

## 📝 Các bước thiết lập hoàn chỉnh

### 1. Cài đặt PostgreSQL

#### Windows
- Tải từ: https://www.postgresql.org/download/windows/
- Cài đặt với port mặc định 5432

#### macOS
```bash
brew install postgresql
brew services start postgresql
```

#### Ubuntu/Debian
```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

### 2. Tạo database và user

```sql
-- Kết nối với PostgreSQL
sudo -u postgres psql

-- Tạo database
CREATE DATABASE scapedata_db;

-- Tạo user
CREATE USER scapedata_user WITH PASSWORD 'your_password';

-- Cấp quyền
GRANT ALL PRIVILEGES ON DATABASE scapedata_db TO scapedata_user;

-- Thoát
\q
```

### 3. Cập nhật file .env

```env
DB_HOST=localhost
DB_NAME=scapedata_db
DB_USER=scapedata_user
DB_PASSWORD=your_password
DB_PORT=5432
```

### 4. Chạy migration

```bash
python migrate_notifications.py
```

## 🔍 Troubleshooting

### Lỗi thường gặp

1. **Connection refused**
   - Kiểm tra PostgreSQL có đang chạy không
   - Kiểm tra port có đúng không

2. **Authentication failed**
   - Kiểm tra username/password
   - Kiểm tra file pg_hba.conf

3. **Database does not exist**
   - Tạo database trước khi chạy migration

4. **Permission denied**
   - Kiểm tra quyền của user database

### Kiểm tra cấu hình

```bash
# Kiểm tra PostgreSQL status
sudo systemctl status postgresql

# Kiểm tra port
netstat -an | grep 5432

# Kiểm tra logs
sudo tail -f /var/log/postgresql/postgresql-*.log
```

## 📞 Hỗ trợ

Nếu gặp vấn đề, hãy kiểm tra:
1. PostgreSQL có đang chạy không
2. Thông tin kết nối trong file .env
3. Quyền của user database
4. Firewall settings 