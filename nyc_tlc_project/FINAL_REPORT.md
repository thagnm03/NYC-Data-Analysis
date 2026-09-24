# Dự báo lượt đón NYC Yellow Taxi theo khu vực và block 4 giờ

## Tóm tắt

Đồ án sử dụng NYC TLC Yellow Taxi Trip Records để nghiên cứu khả năng dự báo **số lượt đón taxi thực tế tại từng taxi zone trong block 4 giờ kế tiếp**. Quy trình bắt đầu từ data understanding và EDA, sau đó khóa feature set, thực hiện rolling-origin validation trên năm 2025 và chỉ mở final test 01/2026–05/2026 sau khi quyết định đã được đóng băng.

Development data gồm 89.892.322 trip records trong 24 tháng 2024–2025. Sau cleaning theo target, 89.642.441 pickup được tổng hợp thành balanced panel gồm 261 zone × 4.386 block, tương đương 1.144.746 observations. Final test bổ sung 236.466 observations và 18.969.497 pickup trong năm tháng đầu 2026.

EDA cho thấy pickup có cấu trúc tuần mạnh, nhưng phân bố tập trung và không đồng đều: top 20 zone chiếm 60,01% volume; high-activity zones chiếm phần lớn pickup trong khi low-activity zones có tỷ lệ target bằng 0 cao. Vì vậy, kết quả tổng thể luôn được kiểm tra cùng phân tích theo segment.

Trên bốn rolling validation folds, four-week seasonal mean có development MAE thấp nhất (14,88). Ridge Regression đạt MAE 14,97 và giảm nhiều lỗi lớn, nhưng làm MAE low-activity tier tăng 74,45% so với previous-week baseline. Theo selection rule đã khóa, không regression nào đủ điều kiện thay baseline, nên **previous-week** được giữ làm formal selected method.

Trên final test chưa từng dùng trong phát triển, previous-week đạt MAE 16,22, RMSE 55,74, WAPE 20,22% và R² 0,935. Ridge có MAE thấp nhất là 14,17 và four-week mean đạt 14,64, nhưng final test không được dùng để chọn lại phương pháp. Kết quả cho thấy historical pickup patterns có giá trị dự báo, đồng thời chứng minh rằng R² cao hoặc aggregate MAE tốt không đảm bảo một mô hình tốt hơn cho mọi zone.

## 1. Bối cảnh và câu hỏi nghiên cứu

Nguồn dữ liệu là [NYC TLC Trip Record Data](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page), kèm [Yellow Taxi Data Dictionary](https://www.nyc.gov/assets/tlc/downloads/pdf/data_dictionary_trip_records_yellow.pdf). Grain nguồn là một completed Yellow Taxi trip record.

Stakeholder giả định là nhóm vận hành Yellow Taxi hoặc một đơn vị hỗ trợ tài xế theo dõi nhu cầu khu vực. Quyết định được hỗ trợ là xác định và xếp hạng những `zone × block 4 giờ` cần được xem xét về positioning/resource planning trước khi block bắt đầu.

Câu hỏi dự báo:

> Dựa trên thông tin lịch và lịch sử lượt đón đã hoàn tất trước thời điểm dự báo, có thể dự đoán số lượt đón Yellow Taxi thực tế tại mỗi taxi zone trong block 4 giờ kế tiếp tốt hơn một seasonal naive baseline hay không?

Đây là supervised regression trên panel time series. Target là **realized pickup volume**, không phải tổng nhu cầu tiềm ẩn của hành khách.

## 2. Thiết kế nghiên cứu

| Thành phần | Định nghĩa |
|---|---|
| Observation | Một pickup zone × block 4 giờ |
| Target | Số pickup records hợp lệ trong zone-block |
| Prediction time | Ngay trước khi block bắt đầu |
| Development period | 01/2024–12/2025 |
| Rolling validation | Bốn quarter của năm 2025 |
| Final test | 01/2026–05/2026 |
| Primary metric | MAE |
| Supporting metrics | MSE, RMSE, WAPE, R² và mean bias |
| Primary baseline | Cùng zone-block một tuần trước |

Không sử dụng random split. Bốn validation folds dùng expanding training window: train đến 12/2024, 03/2025, 06/2025 và 09/2025; validation lần lượt là bốn quý của năm 2025. Final test được giữ kín đến khi hoàn thành model selection.

## 3. Data understanding và chất lượng dữ liệu

Development audit đọc 24 monthly Parquet files và ghi nhận hai schema variants: dữ liệu 2025 bổ sung `cbd_congestion_fee`. Sự vắng mặt của cột này trong 2024 được xem là structural absence, không phải missing cần impute.

Năm trường `passenger_count`, `RatecodeID`, `store_and_fwd_flag`, `congestion_surcharge` và `Airport_fee` cùng null trên 15.703.126 rows. Bundle này không ảnh hưởng target vì `tpep_pickup_datetime` và `PULocationID` vẫn đầy đủ.

Các vấn đề chính:

| Vấn đề | Số rows | Quyết định |
|---|---:|---|
| Exact duplicate | 5 | Bỏ bản trùng, giữ một record |
| Pickup timestamp ngoài file-month | 634 | Loại khỏi target |
| Pickup bucket 264/265 | 249.244 | Loại vì không phải geographic modeling zone |
| Duration không dương | 559.814 | Giữ cho pickup target |
| Distance không dương | 2.179.263 | Giữ cho pickup target |
| Fare âm | 3.579.644 | Giữ cho pickup target |
| Passenger count null | 15.703.126 | Giữ cho pickup target |

Cleaning chỉ loại một record khi không thể gán đáng tin cậy vào đúng pickup month/zone hoặc khi record trùng hoàn toàn. Không lọc theo fare, duration, distance, dropoff hay passenger count vì các trường này không cấu thành recorded pickup target.

Kết quả row accounting:

- raw development rows: 89.892.322;
- target-valid rows trước deduplication: 89.642.446;
- exact duplicates bị bỏ: 5;
- clean pickup rows tạo panel: **89.642.441**.

## 4. Xây dựng modeling panel

Mỗi pickup được gán vào một trong sáu block mỗi ngày: 00:00, 04:00, 08:00, 12:00, 16:00 hoặc 20:00. Pipeline tạo full grid để zone-block không có trip nhận target bằng 0.

Zone universe được xác định chỉ bằng initial training year 2024: một zone trong ID 1–263 đủ điều kiện nếu có ít nhất một clean pickup trong năm 2024. Có 261 eligible zones; zone 103 và 104 không có pickup và bị loại. Không pickup nào bị mất do zone freeze.

Development panel có:

- 261 zones;
- 4.386 time blocks;
- 1.144.746 rows;
- target sum 89.642.441;
- 304.592 zero cells, tương đương 26,61%;
- key `(zone_id, block_start)` duy nhất và không có gap 4 giờ.

Panel target sum khớp tuyệt đối với clean audit, chứng minh mỗi clean trip đóng góp đúng một đơn vị vào một zone-block.

## 5. EDA và problem discovery

### 5.1 Cấu trúc thời gian

Correlation giữa hai time-of-week profiles của 2024 và 2025 là 0,9967. Cùng block tuần trước có pooled correlation 0,970; sau khi center trong từng zone vẫn đạt 0,918. Median correlation theo từng zone là 0,629, cho thấy tín hiệu tuần mạnh nhưng không đồng nhất.

### 5.2 Tập trung không gian

Top 10 zone chiếm 35,92% tổng pickup; top 20 chiếm 60,01%; chỉ 33/261 zone đã bao phủ 80% pickup. Manhattan chiếm 88,73% volume năm 2024 và 86,02% năm 2025. Vì vậy aggregate metric dễ bị các zone lớn chi phối.

### 5.3 Độ thưa theo activity tier

Ba activity tiers được định nghĩa từ riêng năm 2024, mỗi tier có 87 zone. High tier chiếm 98,50% pickup năm 2024 và có zero-cell rate 1,95%; low tier chỉ chiếm 0,21% pickup nhưng có zero-cell rate 72,42%. Đây là lý do model-selection rule bắt buộc kiểm tra low tier.

### 5.4 Level shift và ngày bất thường

Tổng pickup tăng từ 41.023.097 năm 2024 lên 48.619.344 năm 2025; daily mean tăng 18,84%. Zero-cell rate giảm từ 33,47% xuống 19,73%. Đây là movement mô tả, không đủ để kết luận latent demand tăng hay xác định nguyên nhân.

Các ngày quanh 4/7, Thanksgiving và Giáng sinh có chênh lệch lớn so với cùng thứ tuần trước. Điều này cho thấy weekly baseline có thể thất bại ở special periods và hỗ trợ việc thêm known-holiday indicator.

## 6. Feature engineering và chống leakage

Feature set được khóa trước khi fit model:

- categorical: `zone_id`, `month_of_year`, `day_of_week`, `block_hour`;
- known calendar: `is_holiday`, `time_index_days`;
- lags: 1, 6, 42, 84 và 168 block;
- historical summaries: `seasonal_mean_4w`, `recent_mean_6`, `recent_mean_42`, `recent_std_42`.

Mọi rolling statistic đều `shift(1)` trước khi mở window. Lookback tối đa là 168 block, tạo warm-up 28 ngày. Development modeling frame có 1.100.898 feature-ready rows; 571.590 rows thuộc bốn validation windows.

Leakage controls gồm:

- không dùng target block hoặc negative shift;
- không dùng dropoff, destination, duration, distance, fare hoặc payment;
- encoder, scaler và model chỉ fit trên training portion của từng fold;
- activity tiers và peak cells được tính lại từ training history;
- mọi phương pháp được so sánh trên cùng validation rows;
- final test không tham gia feature, alpha hoặc model selection.

## 7. Models và selection rule

Các baseline gồm previous block, previous day, previous week và four-week seasonal mean. Candidate regressions gồm Simple Linear Regression, Multiple Linear Regression và Ridge Regression. Polynomial degree 2 chỉ được chạy nếu residual-curvature gate đạt; gate không đạt nên polynomial không được fit.

Một regression chỉ được thay previous-week khi:

1. mean MAE thấp hơn qua bốn folds;
2. thắng MAE ở ít nhất 3/4 folds;
3. low-tier MAE không vượt 1,05 lần baseline;
4. complexity tăng thêm giải thích được.

## 8. Development rolling-validation results

| Phương pháp | Mean MAE | Mean RMSE | Mean WAPE | Mean R² |
|---|---:|---:|---:|---:|
| Four-week seasonal mean | **14,88** | 51,41 | 17,54% | 0,951 |
| Ridge, α=1000 | 14,97 | **45,72** | 17,68% | **0,961** |
| Previous week | 16,39 | 56,77 | 19,25% | 0,940 |
| Multiple Linear Regression | 16,55 | 47,90 | 19,67% | 0,957 |
| Simple Linear Regression | 16,99 | 56,48 | 19,97% | 0,941 |
| Previous day | 21,90 | 81,21 | 25,81% | 0,878 |
| Previous block | 47,60 | 148,63 | 56,05% | 0,595 |

Four-week mean thắng previous-week ở 4/4 folds. Ridge thắng ở 3/4 folds và giảm aggregate MAE 8,66%, nhưng segment result cho thấy:

| Activity tier | Previous-week MAE | Ridge MAE |
|---|---:|---:|
| High | 44,25 | **38,44** |
| Medium | **3,88** | 4,65 |
| Low | **1,04** | 1,82 |

Ridge cải thiện high tier nhưng làm low-tier MAE tăng 74,45%. Vì vậy Ridge không đạt locked replacement rule. MLR và SLR cũng không đạt. Formal selection trước final test là **previous-week**.

Four-week mean là empirical development winner nhưng selection protocol ban đầu không cho một baseline phụ thay fallback. Đây là limitation của protocol và được công bố, không sửa hậu nghiệm.

## 9. Frozen final evaluation

Final panel 01/2026–05/2026 có 236.466 zone-block observations, 18.969.497 pickup và zero-cell rate 19,88%. Mọi feature lag được nối đúng với development history tại ranh giới 12/2025–01/2026.

| Phương pháp | Vai trò | MAE | RMSE | WAPE | R² |
|---|---|---:|---:|---:|---:|
| Previous week | Formal selected | 16,22 | 55,74 | 20,22% | 0,935 |
| Four-week seasonal mean | Pre-specified secondary | 14,64 | 49,71 | 18,25% | 0,948 |
| Ridge | Diagnostic regression | **14,17** | **43,83** | **17,67%** | **0,960** |
| Previous day | Reference baseline | 21,99 | 81,73 | 27,41% | 0,860 |
| Previous block | Reference baseline | 45,45 | 140,40 | 56,66% | 0,587 |

Formal previous-week performance gần development estimate: final MAE thấp hơn development mean 1,06%. Không có bằng chứng suy giảm tổng thể rõ rệt.

Ridge có final MAE thấp nhất, nhưng không được dùng để thay formal selection. Four-week mean tốt nhất trong tháng 02–05; Ridge tốt nhất ở tháng 01. Kết quả này nhấn mạnh rằng xếp hạng có thể thay đổi theo thời gian và một aggregate metric có thể che mất monthly variation.

## 10. Error analysis

Trên final test, MAE theo activity tier là:

| Tier | Previous week | Four-week mean | Ridge |
|---|---:|---:|---:|
| High | 43,81 | 39,84 | **36,72** |
| Medium | 3,77 | **3,17** | 3,79 |
| Low | 1,07 | **0,92** | 2,01 |

Ở target-positive rows, Ridge MAE là 17,30 so với 20,09 của previous-week. Ở target-zero rows, previous-week tốt nhất với MAE 0,63, còn Ridge là 1,57. Trade-off development vì vậy tiếp tục xuất hiện trên final test.

Ngày có formal-method MAE lớn nhất là 02/03/2026: actual 113.243 pickup nhưng aggregate prediction chỉ 26.000, tạo MAE 58,47. Các cặp 01/01 → 08/01 và 23/02 → 02/03 minh họa cơ chế một shock của tuần trước bị sao chép sang tuần sau. Đây là mô tả lỗi của lag, không phải kết luận nguyên nhân sự kiện.

Calibration theo decile khá gần đường bằng nhau ở mức aggregate; decile cao nhất có mean prediction 614,06 và mean actual 609,41. Tuy nhiên MAE của decile này vẫn là 99,34, cho thấy calibration theo nhóm không bảo đảm độ chính xác cho từng zone-block.

## 11. Kết luận và khuyến nghị

Historical zone-time pickup patterns có khả năng dự báo realized Yellow Taxi pickup volume. Tuy nhiên, mô hình phức tạp không tạo lợi ích đồng đều:

- weekly baseline là một benchmark mạnh, dễ giải thích nhưng nhạy với special-period shocks;
- four-week averaging ổn định hơn về MAE trong đa số tháng;
- Ridge giảm lỗi lớn ở high-volume zones nhưng gây suy giảm rõ ở sparse zones;
- aggregate R² cao không thay thế segment evaluation hoặc baseline comparison.

Formal previous-week method có thể dùng làm **review signal** đơn giản. WAPE 20,22% và error spikes cho thấy chưa nên dùng trực tiếp để tự động điều phối xe. Một hệ thống tiếp theo nên cân nhắc phương pháp phân tầng/hurdle cho zero-heavy zones, rolling monitoring theo tháng và nguồn dữ liệu kịp thời hơn.

## 12. Giới hạn

1. Public TLC monthly files có độ trễ công bố; backtest giả định stakeholder có internal pickup feed cập nhật sau block.
2. Target là realized pickups, chịu giới hạn bởi supply và quy trình ghi nhận; không đo unconstrained demand.
3. Không có weather, events, transit disruptions hoặc fleet availability, nên không thể giải thích nguyên nhân của error spikes.
4. Ridge alpha optimum nằm ở biên trên của locked grid; mở rộng grid là nghiên cứu mới, không thuộc evaluation hiện tại.
5. Formal protocol không cho secondary baseline thay fallback; điều này làm formal selection khác empirical winner.
6. Kết quả không chứng minh thiếu/thừa tài xế, tác động nhân quả, lợi nhuận hoặc dispatch policy tối ưu.

## 13. Tái lập

Các thành phần tái lập:

- `config/`: toàn bộ period, cleaning, feature, fold và model settings;
- `scripts/`: downloader, builders, analysis runners và independent verifiers;
- `outputs/`: machine-readable tables, JSON audits và figures;
- `notebooks/NYC_TLC_predictive_analysis.ipynb`: notebook trình bày đọc trực tiếp từ verified outputs;
- `scripts/run_pipeline.ps1`: entry point theo stage;
- `requirements-lock.txt`: exact package versions của môi trường verification;
- `README.md`: hướng dẫn môi trường và lệnh thực thi.

Ba frozen checksums được kiểm tra trước final evaluation:

- modeling config: `9a70f5987d653abef71ede2a20ce31a3801339b8c0dab8ab74a3096865130fe7`;
- training config: `3bda0eb733295ba242f196697dadf469d82f80c09ae29714b459351a52fcf181`;
- model selection: `10a2a7c394154e2313544c7785a5a386247d9c1295cd97241df82d36941a517f`.

Independent verification đã đạt:

- audit: success;
- panel integrity: success;
- EDA: success;
- modeling specification/leakage: success;
- model development: 24/24 checks;
- final evaluation: 29/29 checks.

Mọi số liệu chính trong báo cáo truy ngược về một CSV/JSON nằm trong `outputs/`.
