# Báo cáo Bước 8 — Frozen final evaluation và error analysis

## 1. Phạm vi và nguyên tắc đánh giá

Final test gồm năm tháng từ **01/2026 đến 05/2026**. Tập này chỉ được mở sau khi Bước 6 đã khóa feature/validation design và Bước 7 đã khóa model-selection decision.

Ba checksum được kiểm tra trước khi chạy:

- modeling config: `9a70f5987d653abef71ede2a20ce31a3801339b8c0dab8ab74a3096865130fe7`;
- training config: `3bda0eb733295ba242f196697dadf469d82f80c09ae29714b459351a52fcf181`;
- model-selection result: `10a2a7c394154e2313544c7785a5a386247d9c1295cd97241df82d36941a517f`.

Phương pháp được chọn chính thức trước khi mở final test là **previous week**: số pickup của đúng zone và block 4 giờ đó ở tuần trước. `4-week seasonal mean` là benchmark phụ đã định trước. Ridge với `alpha = 1000` là mô hình hồi quy chẩn đoán. Kết quả final test không được dùng để chọn lại phương pháp.

## 2. Xây dựng final test

Pipeline xử lý lại năm file raw bằng đúng cleaning rules của development data, giữ cố định tập 261 zone đã xác định từ training data và tạo balanced panel.

| Chỉ tiêu | Kết quả |
|---|---:|
| Raw rows | 18,999,282 |
| Exact duplicates bị loại | 0 |
| Chuyến đi hợp lệ dùng làm target | 18,969,497 |
| Chuyến bị loại do frozen zone universe | 0 |
| Số block 4 giờ | 906 |
| Số zone | 261 |
| Số dòng panel | 236,466 |
| Ô có 0 pickup | 47,012 (19.88%) |

Các lag đầu tháng 01/2026 được tính bằng lịch sử development trước đó, nên không làm mất ranh giới tháng và không lấy thông tin tương lai.

## 3. Kết quả tổng thể

| Phương pháp | Vai trò | MAE | RMSE | WAPE | R² |
|---|---|---:|---:|---:|---:|
| Previous week | **Formal selected method** | 16.22 | 55.74 | 20.22% | 0.935 |
| 4-week seasonal mean | Benchmark phụ định trước | 14.64 | 49.71 | 18.25% | 0.948 |
| Ridge regression | Diagnostic regression | **14.17** | **43.83** | **17.67%** | **0.960** |
| Previous day | Baseline tham chiếu | 21.99 | 81.73 | 27.41% | 0.860 |
| Previous block | Baseline tham chiếu | 45.45 | 140.40 | 56.66% | 0.587 |

Kết luận xác nhận: **formal decision không thay đổi**. Previous-week đạt MAE 16.22 và WAPE 20.22% trên dữ liệu chưa từng được dùng trong phát triển. Ridge có MAE thấp nhất trên final test, nhưng đây chỉ là kết quả mô tả; không được dùng để quay lại thay lựa chọn đã khóa.

So với MAE trung bình trên bốn development folds, final MAE của previous-week thấp hơn 1.06%, 4-week mean thấp hơn 1.59%, và Ridge thấp hơn 5.32%. Vì vậy không có dấu hiệu suy giảm tổng thể rõ rệt khi chuyển sang giai đoạn 2026.

## 4. Phân tích sai số theo tháng

| Tháng | Previous week | 4-week mean | Ridge | Thấp nhất trong tháng |
|---|---:|---:|---:|---|
| 2026-01 | 19.58 | 21.89 | **17.87** | Ridge |
| 2026-02 | 17.33 | **13.77** | 14.13 | 4-week mean |
| 2026-03 | 15.34 | **12.89** | 13.81 | 4-week mean |
| 2026-04 | 12.02 | **11.46** | 11.72 | 4-week mean |
| 2026-05 | 16.79 | **13.02** | 13.27 | 4-week mean |

Ridge thắng tổng thể chủ yếu nhờ tháng 01, còn 4-week mean tốt nhất trong bốn tháng còn lại. Điều này cho thấy xếp hạng mô hình không ổn định hoàn toàn theo thời gian; chỉ nhìn một con số tổng hợp có thể che mất khác biệt này.

## 5. Phân tích theo mức hoạt động và trạng thái target

| Nhóm | Previous week MAE | 4-week mean MAE | Ridge MAE |
|---|---:|---:|---:|
| High-activity zones | 43.81 | 39.84 | **36.72** |
| Medium-activity zones | 3.77 | **3.17** | 3.79 |
| Low-activity zones | 1.07 | **0.92** | 2.01 |
| Target > 0 | 20.09 | 18.11 | **17.30** |
| Target = 0 | **0.63** | 0.68 | 1.57 |

Trade-off đã thấy trong development data vẫn còn: Ridge giảm sai số ở nhóm hoạt động cao nhưng kém hơn các baseline lịch sử ở medium/low zones và tại các ô có target bằng 0. Vì vậy MAE tổng thể thấp hơn không đồng nghĩa Ridge tốt hơn cho mọi zone.

WAPE của low-activity zones rất cao do tổng target của nhóm nhỏ; ở đây nên đọc MAE cùng với WAPE, không diễn giải WAPE riêng lẻ.

## 6. Các lỗi lớn và cơ chế lỗi

Ngày có MAE lớn nhất của formal previous-week method là **2026-03-02**, MAE 58.47: tổng actual là 113,243 pickup nhưng tổng dự báo chỉ 26,000. Các ngày sai số lớn tiếp theo gồm 2026-01-08, 2026-02-01, 2026-01-25 và 2026-02-23.

Hai cặp ngày 2026-01-01 → 2026-01-08 và 2026-02-23 → 2026-03-02 minh họa nhược điểm cấu trúc của baseline: một mức bất thường ở tuần trước có thể được sao chép sang tuần sau và tạo sai số theo hướng ngược lại. Đây là mô tả cơ chế lag, **không phải kết luận nguyên nhân sự kiện**. Muốn gắn nguyên nhân cho từng ngày phải bổ sung dữ liệu lịch/sự kiện và kiểm chứng riêng.

Sai số tuyệt đối lớn tập trung ở các zone có lượng pickup cao; các zone-block nổi bật gồm East Village, JFK Airport và Midtown Center. Điều này giải thích vì sao Ridge cải thiện tốt nhất ở high-activity tier nhưng vẫn không giải quyết đồng đều toàn bộ panel.

## 7. Calibration

Ở decile dự báo cao nhất, previous-week dự báo trung bình 614.06 pickup và actual trung bình 609.41, khá gần đường bằng nhau. Tuy nhiên MAE trong decile này vẫn là 99.34. Vì thế calibration tốt ở mức trung bình nhóm không có nghĩa từng zone-block đều chính xác; các spike theo ngày/zone vẫn có thể rất lớn.

## 8. Quyết định sau final test

- Giữ nguyên **previous-week** là formal selected method để bảo toàn quy trình đánh giá đã khóa.
- Trình bày trung thực rằng Ridge là empirical winner trên final test và 4-week mean thắng Ridge ở 4/5 tháng; không “đổi người thắng” sau khi nhìn test.
- Formal method phù hợp làm benchmark đơn giản và tín hiệu hỗ trợ rà soát nhu cầu, chưa đủ để tự động điều phối. WAPE 20.22% và độ nhạy với shock tuần trước là các giới hạn quan trọng.
- Nếu triển khai bằng public TLC data, phải điều chỉnh forecast horizon/lag theo độ trễ công bố. Backtest này giả định stakeholder có lịch sử pickup kịp thời hơn.
- Không diễn giải pickup quan sát được thành latent demand, thiếu tài xế, lợi nhuận hay tác động nhân quả.

Đây là một điểm mạnh để trình bày trước lớp: kết quả cho thấy quy trình predictive analysis đúng nghĩa — baseline mạnh, validation theo thời gian, test được giữ kín, và kết luận không bị sửa theo kết quả đẹp nhất sau cùng.

## 9. Tính tái lập và deliverables

Verifier độc lập vượt qua **29/29 checks**, gồm checksum frozen artifacts, ranh giới dữ liệu, lịch sử lag, segment definitions, baseline recomputation, metric recomputation, decision lock và sự tồn tại của biểu đồ.

Các output chính:

- `outputs/final_evaluation/final_metrics.csv`;
- `outputs/final_evaluation/final_segment_metrics.csv`;
- `outputs/final_evaluation/final_daily_error.csv`;
- `outputs/final_evaluation/final_worst_days.csv`;
- `outputs/final_evaluation/final_worst_zone_blocks.csv`;
- `outputs/final_evaluation/final_calibration_deciles.csv`;
- `outputs/final_evaluation/frozen_decision.json`;
- `outputs/final_evaluation/final_evaluation_verification.json`;
- `outputs/final_evaluation/figures/`.

## Prompt bước tiếp theo

`Làm bước 9: hoàn thiện notebook, README và báo cáo cuối cùng có thể tái tạo.`
