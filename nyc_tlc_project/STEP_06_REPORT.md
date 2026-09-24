# Bước 6 — Khóa feature set, validation design và leakage audit

**Trạng thái:** hoàn thành, chờ duyệt  
**Config:** phiên bản 1.0, khóa trước khi fit model  
**Phạm vi được đọc:** development panel 01/2024–12/2025  
**Final test 01/2026–05/2026:** chưa sử dụng

## 1. Prediction contract được giữ nguyên

Mỗi observation là một `pickup zone × block 4 giờ`. Ngay trước `block_start`, hệ thống dự báo số pickup thực tế của block kế tiếp bằng:

- thông tin lịch đã biết trước;
- định danh zone;
- pickup history của chính zone đó đã hoàn tất trước `block_start`.

Backtest giả định stakeholder có internal pickup feed cập nhật sau từng block. Nếu chỉ có public TLC files công bố trễ, các lag gần không khả dụng và use case phải được thiết kế lại.

## 2. Feature set đã khóa

### Entity feature

| Feature | Cách dùng |
|---|---|
| `zone_id` | categorical one-hot; encoder chỉ fit trên training portion của từng fold |

`borough`, `zone_name` và `service_zone` không được dùng để train. Borough chỉ dùng để phân tích lỗi; các trường còn lại là metadata mô tả và trùng thông tin với `zone_id`.

### Calendar features

| Feature | Kiểu | Availability |
|---|---|---|
| `month_of_year` | categorical | biết trước |
| `day_of_week` | categorical | biết trước |
| `block_hour` | categorical | biết trước |
| `is_holiday` | binary | biết trước từ holiday calendar đã khóa |
| `time_index_days` | continuous | biết trước, đo số ngày từ 01/01/2024 |

Holiday calendar dùng ngày lễ liên bang Hoa Kỳ theo ngày thực tế, không tự thêm ngày nghỉ bù. Danh sách ngày 2024–2026 được ghi trực tiếp trong config để kết quả có thể tái tạo và không phụ thuộc package/API bên ngoài.

### Historical target features

| Feature | Định nghĩa | Nguồn gần nhất so với target |
|---|---|---:|
| `lag_1` | block 4 giờ trước | −1 block |
| `lag_6` | cùng block hôm trước | −6 block |
| `lag_42` | cùng block tuần trước | −42 block |
| `lag_84` | cùng block 2 tuần trước | −84 block |
| `lag_168` | cùng block 4 tuần trước | −168 block |
| `seasonal_mean_4w` | mean của lag 42, 84, 126, 168 | −42 block |
| `recent_mean_6` | mean của 6 block đã hoàn tất gần nhất | −1 block |
| `recent_mean_42` | mean của 42 block đã hoàn tất gần nhất | −1 block |
| `recent_std_42` | population standard deviation của 42 block đã hoàn tất gần nhất | −1 block |

Tất cả rolling features đều được `shift(1)` trước khi mở cửa sổ. Không feature nào chứa target block hoặc dữ liệu tương lai.

### Vì sao loại lag 52 tuần

`lag_2184` có correlation pooled cao trong EDA nhưng bị loại khỏi feature set chính. Fold 1 train trên 2024 và validation trên Q1/2025; do panel bắt đầu ngày 01/01/2024, không có training row nào trong năm 2024 đủ lịch sử 52 tuần. Dùng feature này sẽ làm mất toàn bộ training sample của fold đầu hoặc buộc phải impute một feature chưa từng quan sát, nên không phù hợp với validation design đã khóa.

## 3. Common modeling sample

Lookback dài nhất là 168 block = 672 giờ = 28 ngày. Vì vậy:

- development frame vẫn giữ đủ 1.144.746 dòng để audit;
- 43.848 dòng warm-up từ 01/01 đến trước 29/01/2024 được đánh dấu `features_ready = false`;
- 1.100.898 dòng từ 29/01/2024 trở đi có đầy đủ feature;
- mọi baseline và model phải được so sánh trên cùng validation rows.

Không dùng imputation cho historical features. Missing do warm-up được loại khỏi modeling sample một cách xác định trước.

## 4. Baseline và candidate models

### Baselines

1. Previous block: `lag_1`.
2. Previous day: `lag_6`.
3. Previous week: `lag_42` — baseline chính.
4. Four-week seasonal mean: `seasonal_mean_4w`.

### Regression candidates

1. **Simple Linear Regression:** chỉ dùng `lag_42`.
2. **Multiple Linear Regression:** zone, calendar và toàn bộ historical features.
3. **Ridge Regression:** cùng feature set với MLR; alpha grid đã khóa là `0.01, 0.1, 1, 10, 100, 1000`.
4. **Polynomial Regression degree 2:** giữ toàn bộ main effects nhưng chỉ tạo polynomial terms cho `time_index_days`, `lag_1`, `lag_6`, `lag_42`, `seasonal_mean_4w`, `recent_mean_42`. Không tạo polynomial interaction với zone one-hot.

Polynomial chỉ được giữ khi residual-curvature gate vượt qua, mean MAE tốt hơn ít nhất 1% so với regression không-polynomial tốt nhất và thỏa toàn bộ model-selection rules. Mục đích là tránh thêm complexity chỉ để có một mô hình tên “polynomial”.

Mọi prediction âm được clip về 0 trước khi tính metric, phù hợp với định nghĩa output là pickup count không âm.

## 5. Validation design đã khóa

Đây là expanding-window rolling-origin validation. Model parameters được fit một lần cho mỗi fold và giữ cố định trong validation quarter; lag/rolling inputs được cập nhật sau mỗi block khi actual pickup của block trước đã hoàn tất.

| Fold | Effective training rows | Validation period | Validation rows |
|---|---:|---|---:|
| 1 | 529.308 | 01/2025–03/2025 | 140.940 |
| 2 | 670.248 | 04/2025–06/2025 | 142.506 |
| 3 | 812.754 | 07/2025–09/2025 | 144.072 |
| 4 | 956.826 | 10/2025–12/2025 | 144.072 |

Bốn validation windows phủ đúng một lần toàn bộ năm 2025, tổng cộng 571.590 zone-block rows. Không dùng random split hoặc shuffled K-fold.

Trong từng fold:

- one-hot encoder, scaler và model chỉ fit trên training rows;
- activity tier được tính lại từ target sum của training rows;
- 14/42 time-of-week cells có training mean cao nhất được gọi là Peak;
- validation target chỉ được dùng sau prediction để tính metric/segment, không dùng để tạo feature hoặc fit preprocessing.

## 6. Metrics và model-selection rule

**Primary metric:** MAE.  
**Supporting metrics:** MSE, RMSE, WAPE và R².

Fold aggregation là mean không trọng số của bốn fold. Uplift được tính bằng:

`100 × (MAE_previous_week − MAE_candidate) / MAE_previous_week`

Một regression candidate chỉ thay thế previous-week baseline khi:

1. mean MAE qua bốn fold thấp hơn baseline;
2. thắng baseline về MAE ở ít nhất 3/4 fold;
3. mean Low-tier MAE không cao hơn 1,05 lần Low-tier MAE của baseline;
4. complexity tăng thêm giải thích được.

Nếu không model nào đạt, previous-week baseline là lựa chọn cuối. Nếu hai model ngang nhau, chọn model đơn giản hơn; nếu complexity ngang nhau, chọn mean RMSE thấp hơn.

Ridge alpha được chọn bằng mean MAE của bốn development folds; nếu hòa thì chọn alpha lớn hơn. Final test không tham gia chọn alpha hoặc model.

## 7. Leakage audit

### Các nhóm rủi ro đã chặn

| Rủi ro | Kiểm soát |
|---|---|
| Dùng target block | Mọi lag có offset ≥ 1; mọi rolling window shift trước |
| Dùng dữ liệu tương lai | Không dùng centered rolling hoặc negative shift |
| Fit preprocessing trên validation | Encoder/scaler fit riêng trên training portion |
| Entity leakage | Activity tier và Peak/Off-peak được định nghĩa lại từ training rows của từng fold |
| So sánh lệch sample | Tất cả baseline/model dùng cùng complete validation rows |
| Mở final test sớm | Feature builder chỉ đọc development panel và từ chối 2026+ |
| Dùng dữ liệu trip sau prediction | Không dùng dropoff, distance, duration, fare hoặc payment |
| Target encoding từ tương lai | Không dùng validation/final-test target để encode zone/category |

Builder audit đạt **17/17 checks**. Independent verifier tái tính calendar features, toàn bộ lag/rolling features và fold masks từ panel gốc; kết quả đạt **34/34 checks**.

## 8. Artefact được tạo

- Locked config: `config/modeling_config.json`
- Feature builder: `scripts/build_modeling_frame.py`
- Independent verifier: `scripts/verify_modeling_spec.py`
- Modeling frame: `data/processed/modeling_frame_development.parquet`
- Feature availability matrix: `outputs/modeling_spec/feature_availability.csv`
- Excluded-feature register: `outputs/modeling_spec/excluded_features.csv`
- Fold manifest: `outputs/modeling_spec/validation_folds.csv`
- Leakage audit: `outputs/modeling_spec/leakage_audit.json`
- Verification result: `outputs/modeling_spec/modeling_spec_verification.json`

Config SHA-256 được ghi vào build summary và verification output. Bước 7 phải ghi lại cùng checksum; nếu config thay đổi thì feature frame/audit phải được tạo lại và thay đổi đó phải được công bố.

## 9. Quyết định cuối Bước 6

Feature set, common sample, folds, preprocessing rule, metrics và model-selection thresholds đã được khóa trước khi fit bất kỳ candidate model nào. Final test 01/2026–05/2026 vẫn đóng.

Prompt tiếp theo sau khi duyệt:

`Làm bước 7: chạy các baseline và candidate regression models trên bốn rolling validation folds.`
