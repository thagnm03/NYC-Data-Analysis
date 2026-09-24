# Tiến độ đồ án NYC TLC

File này là checklist điều phối dự án. Mỗi lần chỉ thực hiện một bước; bước tiếp theo chỉ bắt đầu sau khi kết quả bước hiện tại được kiểm tra.

| Bước | Nội dung | Deliverable chính | Trạng thái |
|---:|---|---|---|
| 1 | Khóa phạm vi và tiêu chí thành công | `PROJECT_BRIEF.md` | Hoàn thành |
| 2 | Tạo pipeline cấu hình được và tải 29 tháng | downloader, config, raw manifest | Hoàn thành |
| 3 | Data understanding và quality audit | schema report, audit tables, cleaning log | Hoàn thành |
| 4 | Tạo panel `zone × 4 giờ` | processed panel, integrity checks | Hoàn thành |
| 5 | EDA và problem discovery trên development data | figures, tables, EDA findings | Hoàn thành |
| 6 | Khóa feature set và leakage audit | modeling spec, feature availability matrix | Hoàn thành |
| 7 | Baseline và model development | rolling-fold metrics cho baseline, SLR, MLR, Ridge, polynomial gate | Hoàn thành |
| 8 | Mở final test, error analysis và decision | final metrics, segment errors, recommendation | Hoàn thành |
| 9 | Hoàn thiện notebook, README và báo cáo | reproducible package, report | Hoàn thành |
| 10 | Làm slide, speaker notes và rehearsal | presentation deck, Q&A sheet | **Hoàn thành — chờ kiểm tra** |

## Quy tắc chuyển bước

- Không sử dụng dữ liệu final test 01–05/2026 trước Bước 8.
- Không thay đổi target hoặc primary metric sau Bước 1 chỉ vì kết quả mô hình không đẹp.
- Không thay đổi feature/model sau khi mở final test, trừ khi ghi nhận kết quả đó là một vòng nghiên cứu mới và tạo test period khác.
- Mỗi con số cuối cùng trong báo cáo/slide phải truy ngược được về một output table do pipeline tạo ra.

## Prompt tiếp theo sau khi duyệt Bước 10

`Dẫn tôi rehearsal bài thuyết trình trong 12 phút và mô phỏng phần phản biện.`
