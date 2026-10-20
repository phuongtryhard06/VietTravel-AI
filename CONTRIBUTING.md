# Contributing to Vietnam Travel Assistant API

Cảm ơn bạn đã quan tâm đến việc đóng góp cho dự án Vietnam Travel Assistant API! Dự án này được phát triển như một đồ án tốt nghiệp và chúng tôi rất hoan nghênh mọi đóng góp từ cộng đồng.

## Cách thức đóng góp

### 1. Báo cáo lỗi (Bug Reports)

Nếu bạn phát hiện lỗi trong hệ thống, vui lòng:

1. Kiểm tra xem lỗi đã được báo cáo chưa trong [Issues](../../issues)
2. Tạo issue mới với tiêu đề mô tả rõ ràng vấn đề
3. Cung cấp thông tin chi tiết:
   - Mô tả lỗi
   - Các bước để tái hiện lỗi
   - Kết quả mong đợi
   - Môi trường thực thi (OS, Python version, etc.)
   - Screenshots (nếu có)

### 2. Đề xuất tính năng (Feature Requests)

Nếu bạn có ý tưởng cải thiện hệ thống:

1. Kiểm tra xem tính năng đã được đề xuất chưa
2. Tạo issue với label "enhancement"
3. Mô tả chi tiết tính năng và lý do tại sao nó hữu ích
4. Đề xuất cách triển khai (nếu có thể)

### 3. Đóng góp code

#### Chuẩn bị môi trường

1. Fork repository này
2. Clone fork về máy local:
   ```bash
   git clone https://github.com/phuongtryhard06/VietTravel-AI.git
   cd VietTravel-AI
   ```

3. Tạo branch mới cho feature/fix:
   ```bash
   git checkout -b feature/your-feature-name
   # hoặc
   git checkout -b fix/your-bug-fix
   ```

4. Cài đặt dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/Mac
   venv\Scripts\activate     # Windows
   pip install -r requirements.txt
   ```

#### Quy tắc code

1. **Coding Style**: Tuân thủ PEP 8 cho Python
2. **Documentation**: Thêm docstring cho functions và classes mới
3. **Testing**: Viết test cases cho tính năng mới
4. **Commit Messages**: Sử dụng commit message rõ ràng và mô tả

#### Quy trình Pull Request

1. Thực hiện các thay đổi trong branch của bạn
2. Chạy tests để đảm bảo không có lỗi:
   ```bash
   python -m pytest tests/
   ```
3. Commit các thay đổi:
   ```bash
   git add .
   git commit -m "Add: mô tả thay đổi"
   ```
4. Push branch lên fork:
   ```bash
   git push origin feature/your-feature-name
   ```
5. Tạo Pull Request với:
   - Mô tả chi tiết về thay đổi
   - Link đến issue liên quan (nếu có)
   - Screenshots (nếu thay đổi UI)

### 4. Cải thiện tài liệu

- Cập nhật README.md
- Thêm comments cho code phức tạp
- Viết hướng dẫn sử dụng mới
- Dịch tài liệu sang ngôn ngữ khác

## Cấu trúc dự án

```
ChatbotTravel_server/
├── src/
│   ├── controllers/     # API endpoints
│   ├── models/         # Database models
│   ├── services/       # Business logic
│   ├── config/         # Configuration
│   └── nlp_model/      # AI/ML components
├── migrations/         # Database migrations
├── uploads/           # File uploads
└── tests/             # Test files
```

## Tiêu chuẩn code

### Python
- Sử dụng Python 3.8+
- Tuân thủ PEP 8
- Sử dụng type hints khi có thể
- Tối đa 79 ký tự mỗi dòng

### API Design
- RESTful API principles
- Consistent response format
- Proper HTTP status codes
- Comprehensive error handling

### Database
- Sử dụng SQLAlchemy ORM
- Migration files cho schema changes
- Proper indexing cho performance

## Testing

### Chạy tests
```bash
# Chạy tất cả tests
python -m pytest

# Chạy tests với coverage
python -m pytest --cov=src

# Chạy tests cụ thể
python -m pytest tests/test_auth.py
```

### Viết tests mới
- Tạo file test trong thư mục `tests/`
- Sử dụng pytest framework
- Mock external dependencies
- Test cả success và error cases

## Review Process

1. **Code Review**: Tất cả PR sẽ được review bởi maintainers
2. **Automated Checks**: CI/CD sẽ chạy tests và linting
3. **Approval**: Cần ít nhất 1 approval để merge
4. **Merge**: Maintainer sẽ merge sau khi approve

## Liên hệ

Nếu bạn có câu hỏi hoặc cần hỗ trợ:

- Tạo issue trên GitHub repository
- Tham gia discussion trong repository

## License

Bằng cách đóng góp, bạn đồng ý rằng đóng góp của bạn sẽ được cấp phép theo cùng license với dự án (MIT License).

---

Cảm ơn bạn đã đóng góp cho dự án! 🚀 