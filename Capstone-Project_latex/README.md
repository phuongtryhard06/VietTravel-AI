# Báo cáo đồ án VietTravel AI

Thư mục này chứa báo cáo LaTeX và hai bản PDF riêng biệt:

- VietTravel_AI_Report_EN.pdf — báo cáo tiếng Anh.
- VietTravel_AI_Report_VI.pdf — báo cáo tiếng Việt.

Hãy dùng hai tên tệp trên làm bản phát hành hiện tại. Các tệp `main.pdf` và `main_vi.pdf` cũ có thể còn trong thư mục từ lần biên dịch trước và không được watcher hiện tại cập nhật.

## Biên dịch cả hai bản PDF

Mở PowerShell tại thư mục này và chạy:

~~~powershell
.\build_pdfs.ps1
~~~

Nếu PowerShell chặn tập lệnh, chạy:

~~~powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\build_pdfs.ps1
~~~

build_pdfs.ps1 biên dịch main.tex và main_vi.tex, rồi cập nhật hai tệp PDF ở thư mục này. Bộ biên dịch Tectonic 0.17.0 được cài trong vùng dữ liệu người dùng. Nếu cần cài lại, chạy install_tectonic.ps1. Lần biên dịch đầu cần Internet để Tectonic tải các gói LaTeX còn thiếu.

## Tự cập nhật PDF tiếng Anh

Chạy tập lệnh sau trong một cửa sổ PowerShell để biên dịch lại PDF tiếng Anh mỗi khi tệp LaTeX, tài liệu tham khảo, lớp báo cáo, kiểu trình bày, hình ảnh hoặc mã nguồn Python/SQL/JSON/YAML/HTML/CSS/JavaScript của ứng dụng thay đổi:

~~~powershell
.\start_english_pdf_watch.ps1
~~~

Watcher chạy ẩn nền và tự biên dịch lại VietTravel_AI_Report_EN.pdf khi tệp báo cáo hoặc mã nguồn trong src, scripts, migrations hay tests thay đổi. Dừng watcher bằng stop_english_pdf_watch.ps1. Có thể chạy trực tiếp watch_english_pdf.ps1 trong cửa sổ PowerShell nếu muốn xem log; nhấn Ctrl+C để dừng. Việc biên dịch lại không tự viết lại nội dung thuyết minh: sau khi thay đổi hành vi hoặc kết quả của hệ thống, cần cập nhật tệp .tex tương ứng để báo cáo mô tả đúng thay đổi đó.

## Tệp nguồn

- main.tex — tài liệu tiếng Anh, gồm trang bìa, tóm tắt, phần đầu, năm chương, tài liệu tham khảo và phụ lục.
- main_vi.tex — tài liệu tiếng Việt có cùng cấu trúc.
- chapter01.tex đến chapter05.tex — năm chương tiếng Anh.
- chapter01_vi.tex đến chapter05_vi.tex — bản dịch tiếng Việt của năm chương.
- appendices.tex, appendices_vi.tex — phụ lục hai ngôn ngữ.
- metadata.tex, metadata_vi.tex — thông tin trang bìa.
- sicaireport.cls — lớp báo cáo theo mẫu Samsung Innovation Campus; tùy chọn vietnamese bật phông chữ hỗ trợ tiếng Việt.
- preamble.tex và references.bib — gói dùng chung và tài liệu tham khảo.
- build_pdfs.ps1, start_english_pdf_watch.ps1, watch_english_pdf.ps1, stop_english_pdf_watch.ps1, install_tectonic.ps1 — tập lệnh biên dịch, theo dõi và cài bộ biên dịch.

## Phạm vi và bằng chứng

Báo cáo có đủ năm phần được yêu cầu: Introduction; Related Work; Dataset and Pre-processing; Model Configuration; Experimental Results and Discussion. Bản tiếng Anh phân biệt bằng chứng trong mã nguồn, số liệu ghi trong tệp kê khai và phép đo chưa được thực hiện. Dự án hiện có một bản tổng hợp dữ liệu cùng chín ca kiểm thử được khai báo, nhưng không có log kiểm thử đã chạy hoặc benchmark có thể tái lập; các giá trị chất lượng mô hình không có bằng chứng đã bị loại khỏi báo cáo.

Các chương chapter06.tex, chapter07.tex và chapter08.tex là bản nháp cũ, không được main.tex đưa vào biên dịch.
