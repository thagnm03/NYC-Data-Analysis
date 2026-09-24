# Bước 5 — EDA và problem discovery trên development panel

**Trạng thái:** hoàn thành, chờ duyệt  
**Phạm vi phân tích:** 01/2024–12/2025  
**Final test 01/2026–05/2026:** chưa sử dụng  
**Đơn vị quan sát:** `pickup zone × block 4 giờ`  
**Target:** số lượt đón Yellow Taxi trong mỗi zone-block

## 1. Mục tiêu của bước này

Bước 5 dùng development panel để kiểm tra xem bài toán đã khóa ở Bước 1 có thật sự được dữ liệu hỗ trợ hay không. EDA tập trung vào năm câu hỏi:

1. Nhu cầu có cấu trúc lặp lại theo thời điểm trong tuần không?
2. Lịch sử gần, đặc biệt cùng block tuần trước, có chứa tín hiệu dự báo không?
3. Pickup volume có tập trung vào một nhóm nhỏ taxi zone không?
4. Độ thưa và hành vi target có khác nhau giữa các nhóm zone không?
5. Mức cầu có thay đổi theo thời gian hoặc bị phá vỡ vào các ngày bất thường không?

Mọi kết quả trong báo cáo này đều được tính từ development panel. Pipeline chủ động dừng nếu phát hiện timestamp từ năm 2026 trở đi.

## 2. Dữ liệu đã phân tích

| Thành phần | Giá trị |
|---|---:|
| Số zone | 261 |
| Số block 4 giờ | 4.386 |
| Số dòng panel | 1.144.746 |
| Tổng pickup | 89.642.441 |
| Năm sử dụng | 2024, 2025 |
| Dòng final test được sử dụng | 0 |

Activity tier được xác định từ tổng pickup của riêng năm 2024 rồi chia thành ba nhóm có cùng số zone: Low, Medium và High, mỗi nhóm 87 zone. Cách làm này tránh dùng thông tin năm 2025 để định nghĩa phân khúc ban đầu.

## 3. Kết quả theo giả thuyết

### H1 — Cấu trúc thời gian trong tuần có tính lặp lại

**Kết quả: được dữ liệu hỗ trợ.**

- Tương quan giữa hai time-of-week profile của 2024 và 2025 là **0,9967**.
- Cả hai năm đều cho thấy volume thấp vào các block rạng sáng và cao hơn vào chiều/tối.
- Cell trung bình cao nhất là thứ Năm lúc 16:00 trong cả 2024 và 2025, lần lượt khoảng **128,45** và **148,94** pickup/zone-block.
- Cell trung bình thấp nhất là thứ Ba lúc 00:00, lần lượt khoảng **10,02** và **13,91** pickup/zone-block.

**Diễn giải:** calendar features như thứ trong tuần và giờ bắt đầu block là tín hiệu hợp lệ. Tuy nhiên, cùng một hình dạng thời gian không đồng nghĩa mức volume đứng yên; năm 2025 có level cao hơn.

### H2 — Cùng block tuần trước là baseline mạnh

**Kết quả: được hỗ trợ, nhưng sức mạnh không đồng đều giữa các zone.**

| Lag | Tương quan pooled thô | Tương quan sau khi center theo zone | Median tương quan từng zone |
|---|---:|---:|---:|
| Block trước, 4 giờ | 0,800 | 0,462 | 0,428 |
| Cùng block hôm trước, 24 giờ | 0,942 | 0,844 | 0,583 |
| Cùng block tuần trước, 168 giờ | **0,970** | **0,918** | **0,629** |
| Cùng block 2 tuần trước | 0,962 | 0,897 | 0,610 |
| Cùng block 4 tuần trước | 0,964 | 0,902 | 0,601 |
| Cùng block 52 tuần trước | 0,981 | 0,953 | 0,400 |

Tương quan pooled có thể cao một phần vì các zone lớn luôn lớn hơn các zone nhỏ. Vì vậy, kết quả sau khi center theo zone và phân phối tương quan từng zone mới là bằng chứng quan trọng hơn. Lag một tuần vẫn mạnh nhất trong các lag ngắn, nhưng median chỉ là 0,629.

Lag 52 tuần chưa đủ để kết luận về seasonality hằng năm: nó chỉ có cặp quan sát giữa 2024 và 2025, và median tương quan theo zone thấp hơn đáng kể so với con số pooled.

**Diễn giải:** `same block previous week` phải là baseline chính. Mô hình hồi quy chỉ có giá trị nếu thắng baseline này trên đúng cùng các dòng validation.

### H3 — Pickup volume tập trung mạnh theo không gian

**Kết quả: được dữ liệu hỗ trợ.**

- Top 10 zone chiếm **35,92%** tổng pickup.
- Top 20 zone chiếm **60,01%** tổng pickup.
- Chỉ cần 33/261 zone để đạt 80% tổng pickup.
- Manhattan chiếm **88,73%** pickup năm 2024 và **86,02%** năm 2025.

**Diễn giải:** metric toàn cục sẽ bị các zone lớn chi phối. Vì vậy phải báo cáo lỗi theo activity tier và borough; không được dùng một MAE hoặc R² toàn cục để tuyên bố mô hình hoạt động tốt cho mọi zone.

### H4 — Độ thưa khác biệt rất lớn giữa activity tiers

**Kết quả: được dữ liệu hỗ trợ.**

| Tier | Tỷ trọng pickup 2024 | Zero-cell 2024 | Tỷ trọng pickup 2025 | Zero-cell 2025 |
|---|---:|---:|---:|---:|
| High | 98,50% | 1,95% | 96,23% | 0,58% |
| Medium | 1,28% | 26,04% | 3,23% | 7,02% |
| Low | 0,21% | 72,42% | 0,53% | 51,57% |

**Diễn giải:** bài toán ở high-activity zones khác đáng kể so với low-activity zones. Low tier có nhiều target bằng 0, nên cần đánh giá riêng zero-demand/positive-demand rows và không để lợi ích ở Manhattan hoặc high tier che giấu suy giảm ở nhóm thấp.

### H5 — Có level shift giữa 2024 và 2025

**Kết quả: được dữ liệu hỗ trợ ở mức mô tả.**

- Tổng pickup tăng từ **41.023.097** năm 2024 lên **48.619.344** năm 2025.
- Daily mean tăng **18,84%**, từ 112.085 lên 133.204 pickup/ngày.
- 257/261 zone có volume năm 2025 cao hơn 2024; 4 zone giảm.
- Zero-cell rate toàn panel giảm từ **33,47%** xuống **19,73%**.

**Diễn giải:** mô hình phải được kiểm tra bằng rolling-origin validation để quan sát khả năng thích nghi khi level thay đổi. Đây không phải bằng chứng rằng latent passenger demand tăng 18,84%, cũng không xác định được nguyên nhân của thay đổi.

### H6 — Một số ngày phá vỡ pattern tuần thông thường

**Kết quả: được dữ liệu hỗ trợ ở mức mô tả.**

Các sai khác lớn so với cùng thứ của tuần trước xuất hiện quanh ngày 4/7, Thanksgiving và Giáng sinh. Ví dụ, ngày 25/12/2024 thấp hơn ngày 18/12 khoảng **61,10%**, còn ngày 5/12/2024 cao hơn Thanksgiving tuần trước khoảng **92,14%**.

**Diễn giải:** holiday indicator đã biết trước thời điểm dự báo là candidate feature hợp lệ. Cần phân tích lỗi riêng cho holiday/special-date periods. EDA không chứng minh ngày lễ là nguyên nhân duy nhất của các thay đổi này.

## 4. Problem discovery: từ quan sát đến bài toán dự báo

| Tầng lập luận | Kết luận |
|---|---|
| Quan sát dữ liệu | Volume có pattern theo zone và time-of-week rất rõ, nhưng tập trung mạnh, độ thưa khác nhau và level thay đổi giữa hai năm. |
| Ý nghĩa vận hành | Nhóm vận hành có thể dùng dự báo để ưu tiên những zone-block cần xem xét trước khi block bắt đầu; một cảnh báo chung cho toàn thành phố là chưa đủ. |
| Business problem | Xác định và xếp hạng các `zone × block 4 giờ` cần demand review/positioning review. |
| Predictive problem | Dùng calendar và pickup history đã hoàn tất trước `block_start` để dự báo realized pickup count của block 4 giờ kế tiếp. |
| Baseline bắt buộc | Pickup count của cùng zone, cùng block, một tuần trước. |
| Cách đánh giá bắt buộc | Rolling temporal folds và metric theo activity tier, borough, peak/off-peak, weekday/weekend, zero/positive target. |

EDA vì vậy **xác nhận bài toán đã khóa ở Bước 1 là có cơ sở**, đồng thời bổ sung hai yêu cầu quan trọng: đánh giá phân khúc để không che giấu low-activity zones và xử lý drift/ngày bất thường trong validation.

## 5. Candidate features chuyển sang Bước 6

Danh sách này mới là candidate từ EDA, chưa phải feature set cuối:

- zone identity;
- hour/block-of-day, day-of-week, weekend;
- known-holiday indicator;
- target lags 1, 6, 42, 84, 168 và 2.184 block;
- shifted rolling mean/median và variability features chỉ dùng dữ liệu trước target block;
- trend/index feature nếu validation cho thấy cần thiết.

Bước 6 phải kiểm tra availability tại prediction time và leakage của từng feature. Mọi rolling feature bắt buộc phải `shift` trước khi tính cửa sổ.

## 6. Những điều chưa được phép kết luận

EDA này không cho phép kết luận về:

- latent passenger demand;
- thiếu hoặc thừa tài xế;
- nguyên nhân nhân quả của tăng trưởng 2025;
- tác động của thời tiết, sự kiện hay chính sách;
- doanh thu, lợi nhuận hoặc phương án dispatch tối ưu.

Target chỉ là **realized Yellow Taxi pickup volume** được ghi nhận trong dữ liệu TLC.

## 7. Artefact có thể tái tạo

- Cấu hình: `config/eda_config.json`
- Pipeline: `scripts/run_eda.py`
- Verifier: `scripts/verify_eda.py`
- 10 bảng kết quả: `outputs/eda/*.csv`
- 8 biểu đồ: `outputs/eda/figures/*.png`
- Tóm tắt machine-readable: `outputs/eda/eda_summary.json`
- Kết quả kiểm tra: `outputs/eda/eda_verification.json`

Verifier đã xác nhận đủ artefact, target reconciliation đúng, activity tiers đúng cấu hình, không có correlation bị thiếu và không sử dụng final test.

## 8. Quyết định cuối Bước 5

**Giữ nguyên target, grain và business framing đã khóa.** Dữ liệu development cung cấp đủ tín hiệu để tiếp tục bài toán dự báo, nhưng ngưỡng khó thực tế là phải thắng một weekly seasonal baseline mạnh và không được hy sinh hiệu năng ở low-activity zones.

Prompt tiếp theo sau khi duyệt:

`Làm bước 6: khóa feature set, validation design và leakage audit.`
