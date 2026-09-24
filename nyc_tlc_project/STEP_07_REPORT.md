# Bước 7 — Baseline và regression development trên rolling validation

**Trạng thái:** hoàn thành, chờ duyệt  
**Phạm vi:** bốn rolling validation folds trong năm 2025  
**Số validation rows:** 571.590  
**Modeling config SHA-256:** `9a70f5987d653abef71ede2a20ce31a3801339b8c0dab8ab74a3096865130fe7`  
**Final test 01/2026–05/2026:** chưa sử dụng

## 1. Những phương pháp đã chạy

### Baselines

1. Previous block.
2. Previous day.
3. Previous week — primary baseline đã khóa.
4. Four-week seasonal mean.

### Regression candidates

1. Simple Linear Regression với `lag_42`.
2. Multiple Linear Regression với zone, calendar và historical features.
3. Ridge Regression với cùng feature set; thử alpha `0.01, 0.1, 1, 10, 100, 1000`.
4. Polynomial Regression degree 2 chỉ được chạy nếu residual-curvature gate đạt.

Tất cả phương pháp được chấm trên đúng cùng validation rows. Prediction âm được clip về 0 trước khi tính metric.

## 2. Kết quả trung bình qua bốn fold

| Phương pháp | Mean MAE | Mean RMSE | Mean WAPE | Mean R² | Uplift MAE so với previous week |
|---|---:|---:|---:|---:|---:|
| Four-week seasonal mean | **14,88** | 51,41 | 17,54% | 0,951 | **+9,21%** |
| Ridge Regression, α=1000 | 14,97 | **45,72** | 17,68% | **0,961** | +8,66% |
| Previous week | 16,39 | 56,77 | 19,25% | 0,940 | 0,00% |
| Multiple Linear Regression | 16,55 | 47,90 | 19,67% | 0,957 | −0,95% |
| Simple Linear Regression | 16,99 | 56,48 | 19,97% | 0,941 | −3,68% |
| Previous day | 21,90 | 81,21 | 25,81% | 0,878 | −33,62% |
| Previous block | 47,60 | 148,63 | 56,05% | 0,595 | −190,41% |

**Kết quả thực nghiệm tốt nhất về mean MAE là four-week seasonal mean**, không phải regression. Ridge đứng thứ hai về MAE nhưng tốt nhất về RMSE và R², cho thấy nó giảm được nhiều lỗi lớn ở high-volume observations.

So với previous-week baseline, Ridge giảm mean MAE 8,66% và giảm mean RMSE khoảng 19,46%. Tuy nhiên lợi ích này không phân bố đều giữa các activity tiers.

## 3. Độ ổn định theo fold

| Phương pháp | Fold 1 MAE | Fold 2 MAE | Fold 3 MAE | Fold 4 MAE |
|---|---:|---:|---:|---:|
| Previous week | 14,64 | 16,00 | 15,67 | 19,25 |
| Four-week seasonal mean | **14,31** | **13,57** | 14,96 | 16,68 |
| Ridge | 15,50 | 13,93 | 14,86 | 15,59 |
| Multiple Linear Regression | 22,06 | 13,91 | **14,68** | **15,54** |
| Simple Linear Regression | 15,42 | 16,60 | 16,22 | 19,74 |

Four-week seasonal mean thắng previous week trong cả 4/4 folds. Ridge thắng previous week trong 3/4 folds. MLR cũng thắng trong 3/4 folds nhưng lỗi rất lớn ở Fold 1 khiến mean MAE chung cao hơn baseline.

Kết quả Fold 1 cho thấy mô hình nhiều biến nhạy với khác biệt giữa training year 2024 và đầu năm 2025. Ridge regularization làm giảm đáng kể sự bất ổn này, nhưng không xóa hoàn toàn.

## 4. Ridge tuning

Alpha được chọn đúng theo mean MAE qua bốn development folds:

| Alpha | Mean MAE |
|---:|---:|
| 0,01 | 17,36 |
| 0,1 | 17,36 |
| 1 | 17,31 |
| 10 | 16,92 |
| 100 | 15,76 |
| 1000 | **14,97** |

Optimum nằm ở biên trên của grid. Điều này cho thấy regularization mạnh có ích, nhưng cũng là một limitation: grid có thể chưa bao phủ điểm tối ưu thật. Grid không được mở rộng sau khi nhìn kết quả vì alpha set đã khóa ở Bước 6. Đây có thể là đề xuất cho một research iteration mới, không phải thay đổi trong vòng đánh giá hiện tại.

## 5. Vì sao Ridge không được chọn

Model-selection rule yêu cầu candidate phải:

1. có mean MAE thấp hơn previous week;
2. thắng ít nhất 3/4 folds;
3. mean Low-tier MAE không vượt 1,05 lần baseline;
4. có complexity giải thích được.

Ridge đạt điều kiện 1, 2 và 4, nhưng không đạt điều kiện 3:

| Activity tier | Previous-week MAE | Ridge MAE | Kết quả |
|---|---:|---:|---|
| Low | **1,04** | 1,82 | Ridge cao hơn 74,45% |
| Medium | **3,88** | 4,65 | Ridge cao hơn 19,92% |
| High | 44,25 | **38,44** | Ridge thấp hơn 13,13% |

Ridge thắng metric toàn cục chủ yếu nhờ cải thiện high-activity zones, vốn chiếm phần lớn pickup volume. Ở zero-target rows, MAE của Ridge là 1,16 so với 0,58 của previous week. Đây chính là loại kết quả mà segment gate ở Bước 6 được thiết kế để phát hiện.

MLR không đạt mean-MAE rule và low-tier rule. SLR không đạt cả mean-MAE, fold-win và low-tier rules.

## 6. Polynomial gate

Best non-polynomial regression là Ridge. Residual diagnostic tạo 10 prediction deciles:

- bốn decile vượt ngưỡng absolute mean residual;
- cả bốn đều có residual trung bình dương;
- không có qualifying decile với residual âm.

Gate yêu cầu ít nhất ba qualifying deciles và phải có cả hai dấu để chỉ ra curvature thay vì một chiều bias. Điều kiện dấu không đạt, vì vậy polynomial regression **không được chạy**. Đây là thực thi đúng quy tắc, không phải lỗi pipeline.

## 7. Formal selection và empirical winner

### Formal selection theo rule đã khóa

Không regression candidate nào đạt toàn bộ replacement rules. Theo quy tắc Bước 1/Bước 6, lựa chọn chính thức là:

> **Previous-week baseline — dự báo pickup của zone-block bằng pickup của cùng zone-block một tuần trước.**

### Phương pháp có development MAE thấp nhất

Four-week seasonal mean có mean MAE thấp nhất và thắng previous week ở 4/4 folds. Tuy nhiên protocol đã khóa chỉ định previous week là fallback khi không regression nào đủ điều kiện; nó không định nghĩa việc thay fallback bằng một baseline khác.

Vì vậy, four-week seasonal mean được báo cáo là **empirical development winner**, nhưng không được đổi thành formal selection sau khi nhìn kết quả. Đây là một limitation của selection protocol cần nói thẳng khi thuyết trình, không phải lý do để sửa rule hậu nghiệm.

Ở Bước 8, previous week phải được xem là formal frozen selection. Four-week seasonal mean có thể được báo cáo như một secondary pre-specified benchmark, nhưng final test không được dùng để thay đổi quyết định đã khóa.

## 8. Ý nghĩa cho đồ án

Kết quả này hỗ trợ một câu chuyện predictive analysis mạnh:

- R² cao không đồng nghĩa model tạo thêm giá trị ổn định so với baseline.
- Regularization giúp giảm lỗi lớn và ổn định MLR, nhưng aggregate gain có thể che giấu thiệt hại ở sparse zones.
- Một trung bình mùa vụ đơn giản có thể vượt các mô hình hồi quy phức tạp về MAE.
- Quy tắc selection được định nghĩa trước giúp tránh chọn model chỉ vì kết quả tổng thể đẹp.
- Việc baseline được chọn là kết quả hợp lệ, không phải thất bại của đồ án.

Không kết quả nào ở đây chứng minh latent demand, thiếu tài xế, lợi nhuận hoặc tác động nhân quả.

## 9. Verification

Independent verifier đạt **24/24 checks**:

- 571.590 validation rows khớp common modeling sample;
- bốn baseline khớp chính xác với lag/seasonal columns;
- toàn bộ fold metrics được tính lại từ prediction và khớp output;
- Ridge alpha và final selection tuân theo locked rules;
- prediction đầy đủ, không âm và key duy nhất;
- polynomial execution khớp gate;
- không có dữ liệu 2026.

## 10. Artefact

- Training config: `config/training_config.json`
- Modeling runner: `scripts/run_models.py`
- Independent verifier: `scripts/verify_models.py`
- Fold metrics: `outputs/modeling/fold_metrics.csv`
- Mean metrics: `outputs/modeling/mean_fold_metrics.csv`
- Segment metrics: `outputs/modeling/mean_segment_metrics.csv`
- Ridge tuning: `outputs/modeling/ridge_alpha_summary.csv`
- Polynomial gate: `outputs/modeling/polynomial_gate.json`
- Model-selection audit: `outputs/modeling/model_selection_candidates.csv`
- Frozen decision: `outputs/modeling/model_selection.json`
- Out-of-fold predictions: `outputs/modeling/validation_predictions.parquet`
- Verification: `outputs/modeling/modeling_verification.json`
- Figures: `outputs/modeling/figures/`

## 11. Quyết định cuối Bước 7

Giữ **previous-week baseline** làm formal selected method cho final evaluation. Giữ **four-week seasonal mean** làm secondary pre-specified benchmark và ghi nhận nó là phương pháp có development MAE thấp nhất. Không mở rộng Ridge alpha grid, không chạy polynomial và không thay selection rule sau khi nhìn kết quả.

Prompt tiếp theo sau khi duyệt:

`Làm bước 8: mở final test 01/2026–05/2026, chạy frozen evaluation và thực hiện error analysis.`
