# Project Brief — Dự báo lượt đón NYC Yellow Taxi

**Phiên bản:** 1.0  
**Trạng thái:** scope freeze trước data expansion/modeling  
**Dataset:** NYC TLC Yellow Taxi Trip Records  
**Nguồn chính thức:** <https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page>

## 1. Bối cảnh

Đây là đồ án môn Predictive Analysis bắt đầu từ một public dataset, không bắt đầu từ stakeholder thật. Vì vậy, quy trình phải thể hiện rõ cách data understanding và EDA dẫn đến một vấn đề có thể dự đoán, thay vì chọn sẵn một target chỉ vì nó tồn tại trong dữ liệu.

Pilot 01–06/2024 cho thấy lượt đón taxi có cấu trúc mạnh theo khu vực, block giờ và tuần. Tuy nhiên, pilot chỉ dùng sáu tháng và một test month nên chưa đủ để kết luận về độ ổn định dài hạn. Đồ án cuối sẽ kiểm tra lại giả thuyết này trên 29 tháng.

## 2. Stakeholder và quyết định giả định

**Stakeholder giả định:** nhóm vận hành Yellow Taxi hoặc một đơn vị hỗ trợ tài xế theo dõi nhu cầu khu vực.

**Quyết định được hỗ trợ:** xác định và xếp hạng các `zone × block 4 giờ` cần được xem xét về positioning/resource planning trước khi block bắt đầu.

Model chỉ cung cấp demand forecast và review signal. Model không tự phân xe, không tối ưu dispatch và không chứng minh rằng một khu vực đang thiếu tài xế.

## 3. Business problem

Lượt đón Yellow Taxi phân bố không đồng đều theo khu vực và thời điểm. Nhóm vận hành cần biết trước những zone-block nào có khả năng ghi nhận volume cao hoặc khác đáng kể so với pattern thông thường để ưu tiên demand review.

## 4. Predictive problem

> Dựa trên thông tin lịch và lịch sử lượt đón đã hoàn tất trước thời điểm dự báo, có thể dự đoán số lượt đón Yellow Taxi thực tế tại mỗi taxi zone trong block 4 giờ kế tiếp tốt hơn một seasonal naive baseline hay không?

Đây là bài toán supervised regression trên dữ liệu panel theo thời gian.

## 5. Định nghĩa prediction

| Thành phần | Định nghĩa đã khóa |
|---|---|
| Observation grain | Một `pickup zone × block 4 giờ` |
| Target | Số chuyến Yellow Taxi có pickup trong zone và block đó |
| Prediction time | Ngay trước khi block 4 giờ bắt đầu |
| Forecast horizon | Block 4 giờ kế tiếp |
| Output | Một predicted pickup count không âm cho mỗi eligible zone |
| Information cutoff | Chỉ dùng dữ liệu đã tồn tại trước `block_start` |
| Target meaning | Realized pickup volume, không phải latent passenger demand |

## 6. Phạm vi dữ liệu

| Tập | Giai đoạn | Cách sử dụng |
|---|---|---|
| Development | 01/2024–12/2025 | Data audit, EDA, feature design, rolling validation và model selection |
| Final untouched test | 01/2026–05/2026 | Chỉ mở sau khi cleaning, feature set, model family và hyperparameter rule đã khóa |

Chỉ dùng Yellow Taxi. Pickup zone hợp lệ thuộc ID 1–263; bucket 264/265 không được xem là zone địa lý. Quy tắc eligible-zone chi tiết sẽ được chốt từ development training history tại Bước 3, không nhìn final test.

## 7. Giả định vận hành và độ trễ công bố

Public TLC files thường được công bố sau tháng vận hành. Backtest này giả định stakeholder có internal pickup feed đủ kịp thời để biết các block lịch sử đã hoàn tất.

Do đó:

- kết quả đo khả năng dự đoán từ historical pickup patterns;
- kết quả không chứng minh một hệ thống chỉ dùng public TLC files có thể chạy near-real-time;
- nếu use case bị giới hạn hoàn toàn ở public data, forecast cutoff và lag phải được thiết kế lại theo publication delay.

## 8. Baseline bắt buộc

Các baseline phải được đánh giá trước mô hình hồi quy:

1. previous block;
2. same block previous day;
3. same block previous week;
4. shifted seasonal rolling mean nếu đủ lịch sử.

**Baseline chính để ra quyết định:** same block previous week. Mọi model cạnh tranh phải được so sánh với baseline này trên đúng cùng test rows.

## 9. Model progression đã khóa

1. Simple Linear Regression với previous-week lag;
2. Multiple Linear Regression với calendar, zone và historical lags;
3. Ridge Regression để regularize correlated/one-hot features;
4. Polynomial Regression degree 2 chỉ được giữ nếu EDA/residual và rolling validation cho thấy lợi ích; không tạo toàn bộ polynomial interactions với zone one-hot;
5. model phức tạp hơn chỉ được để trong appendix nếu còn thời gian.

## 10. Validation design

Không dùng random split hoặc shuffled K-fold cho kết luận chính.

| Vòng | Training window | Validation window |
|---|---|---|
| Fold 1 | 01/2024–12/2024 | 01/2025–03/2025 |
| Fold 2 | 01/2024–03/2025 | 04/2025–06/2025 |
| Fold 3 | 01/2024–06/2025 | 07/2025–09/2025 |
| Fold 4 | 01/2024–09/2025 | 10/2025–12/2025 |
| Final test | 01/2024–12/2025 | 01/2026–05/2026 |

Preprocessing, scaling, eligible-zone selection và fitting đều chỉ được học từ training portion tương ứng.

## 11. Metrics và tiêu chí lựa chọn

### Primary metric

**MAE**, đo số lượt pickup sai trung bình trên mỗi zone-block.

### Supporting metrics

- MSE: phù hợp nội dung môn học và nhấn mạnh lỗi lớn;
- RMSE: đưa MSE về đơn vị số chuyến;
- WAPE: diễn giải tổng sai số theo tổng volume;
- R²: mô tả mức biến động được giải thích, không dùng một mình để tuyên bố model tốt;
- uplift so với previous-week baseline.

### Rule chọn model trước final test

Một regression model chỉ thay thế previous-week baseline khi:

1. có mean MAE thấp hơn baseline qua bốn validation folds;
2. thắng baseline về MAE trong ít nhất ba trên bốn folds;
3. không tạo suy giảm nghiêm trọng ở low-activity tier;
4. complexity tăng thêm có thể giải thích được.

Nếu không model nào thỏa rule, previous-week baseline là model được chọn. Đây là kết quả hợp lệ, không phải thất bại của đồ án.

Final 2026 test dùng để ước lượng hiệu năng ngoài thời gian của lựa chọn đã khóa, không dùng để chọn lại model.

## 12. Segment evaluation bắt buộc

Ngoài metric toàn cục, phải báo cáo theo:

- high/medium/low activity zone;
- peak/off-peak block;
- weekday/weekend;
- borough;
- từng rolling fold/tháng;
- zero-demand và positive-demand rows.

Activity tier phải được xác định từ training history của mỗi fold.

## 13. Leakage policy

Được phép dùng:

- calendar fields đã biết trước;
- zone identity;
- target lags hoàn toàn nằm trước block dự báo;
- rolling statistics đã `shift(1)` trước khi tính;
- holiday calendar đã biết trước.

Không được dùng:

- pickup count của target block;
- dropoff, destination, distance, duration, fare hoặc payment phát sinh trong/sau target block;
- centered rolling window hoặc rolling window chứa target;
- preprocessing được fit trên validation/final test;
- feature/hyperparameter được chọn sau khi xem final test.

## 14. Điều được phép và không được phép kết luận

### Có thể kết luận

- historical zone-time patterns dự đoán realized pickup volume tốt đến đâu;
- regression có tạo incremental value so với seasonal baseline hay không;
- mô hình mạnh/yếu ở segment và thời điểm nào;
- forecast có thể hỗ trợ ưu tiên demand review như thế nào.

### Không thể kết luận chỉ từ dataset này

- tổng nhu cầu thật của hành khách;
- khu vực thiếu hay thừa tài xế;
- forecast làm tăng doanh thu/lợi nhuận;
- tác động nhân quả của thời tiết, sự kiện hoặc chính sách;
- phương án điều phối xe tối ưu.

## 15. Definition of Done cho Bước 1

- business problem và predictive question được viết riêng;
- target, grain, prediction time và horizon rõ ràng;
- development/final-test periods được khóa;
- primary metric và baseline chính được khóa;
- model-selection rule được định nghĩa trước khi chạy 29 tháng;
- leakage policy và giới hạn phát biểu được ghi thành văn bản;
- tiến độ 10 bước có checkpoint để người dùng duyệt từng bước.

Tất cả điều kiện trên đã được đáp ứng trong phiên bản 1.0 này.
