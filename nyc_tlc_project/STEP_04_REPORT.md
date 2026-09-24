# Bước 4 — Modeling Panel và Integrity Checks

**Trạng thái:** hoàn thành, chờ người dùng kiểm tra  
**Panel grain:** một `pickup zone × block 4 giờ`  
**Development period:** 01/01/2024 00:00 đến 31/12/2025 20:00  
**Final test 01–05/2026:** không được sử dụng

## 1. Transformation đã thực hiện

Với từng monthly Parquet trong development period:

1. bỏ các dòng trùng hoàn toàn trên toàn bộ fields của file;
2. giữ pickup timestamp thuộc đúng nominal file month;
3. giữ `PULocationID` từ 1 đến 263;
4. gán pickup vào block 4 giờ bắt đầu tại 00:00, 04:00, 08:00, 12:00, 16:00 hoặc 20:00;
5. đếm pickup theo `zone_id × block_start`;
6. ghép với full time grid để các zone-block không có trip nhận target bằng 0.

Không sử dụng fare, distance, duration, dropoff hoặc passenger fields để quyết định target, đúng cleaning log Bước 3.

## 2. Zone-universe rule

Zone universe được khóa bằng initial training year 2024:

> Eligible zone = zone ID 1–263 có ít nhất một clean pickup trong 01/2024–12/2024.

Kết quả:

- 261 eligible zones;
- zone 103 và 104 không eligible;
- cả hai zone không có pickup trong toàn bộ development period;
- không có zone mới xuất hiện lần đầu trong 2025;
- số target rows bị mất do training-only zone freeze: 0.

Quy tắc này ngăn validation year 2025 quyết định entity universe nhưng không loại bất kỳ pickup nào trong dữ liệu hiện tại.

## 3. Panel dimensions

| Thành phần | Giá trị |
|---|---:|
| Eligible zones | 261 |
| Calendar days | 731 |
| Blocks mỗi ngày | 6 |
| Distinct time blocks | 4,386 |
| Expected grid rows | 1,144,746 |
| Actual panel rows | 1,144,746 |
| Target sum | 89,642,441 |
| Zero-demand cells | 304,592 |
| Zero-demand share | 26.61% |
| Panel Parquet size | 1,661,453 bytes |

Panel nhỏ hơn raw data rất nhiều vì 89.6 triệu trip records đã được chuyển thành một balanced time-zone grid.

## 4. Reconciliation với Bước 3

| Kiểm tra | Rows |
|---|---:|
| Expected clean rows theo audit Bước 3 | 89,642,441 |
| Bị loại bởi training-only zone universe | 0 |
| Expected panel target sum | 89,642,441 |
| Actual panel target sum | 89,642,441 |
| Chênh lệch | 0 |

Mỗi clean trip đóng góp đúng một đơn vị vào một zone-block.

## 5. Integrity checks

Tất cả kiểm tra đều pass:

- [x] `(zone_id, block_start)` là khóa duy nhất;
- [x] row count bằng `261 × 4,386`;
- [x] đúng 4,386 time blocks;
- [x] không null ở zone, timestamp hoặc target;
- [x] target là số nguyên không âm;
- [x] block timestamp căn đúng bội số 4 giờ;
- [x] mỗi zone có đúng 6 block mỗi ngày;
- [x] không có gap 4 giờ trong từng zone;
- [x] tổng target khớp clean audit;
- [x] không có row thuộc final-test period;
- [x] build metadata không chứa file 2026.

## 6. Điều chưa làm ở bước này

- chưa tạo lag hoặc rolling features;
- chưa chia activity tiers;
- chưa thực hiện EDA về temporal/spatial patterns;
- chưa chia rolling validation folds;
- chưa train model;
- chưa mở dữ liệu 2026.

Zero-cell rate và monthly panel totals chỉ được ghi để kiểm tra cấu trúc. Việc diễn giải pattern thuộc Bước 5.

## 7. Artifacts

- Panel config: `config/panel_config.json`
- Builder: `scripts/build_panel.py`
- Verifier: `scripts/verify_panel.py`
- Modeling panel: `data/processed/zone_4h_panel_development.parquet`
- Integrity report: `outputs/panel/panel_integrity_report.json`
- Build summary: `outputs/panel/panel_build_summary.json`
- Monthly reconciliation: `outputs/panel/monthly_clean_demand.csv`
- Monthly panel summary: `outputs/panel/panel_monthly_summary.csv`
- Zone eligibility: `outputs/panel/zone_eligibility.csv`
- Zone panel summary: `outputs/panel/panel_zone_summary.csv`
- Ineligible-zone demand: `outputs/panel/ineligible_zone_demand.csv`

## 8. Gate Bước 4

**Pass.** Balanced development panel đã sẵn sàng cho Bước 5 — EDA và problem discovery. EDA phải dùng panel này thay vì quay lại lọc raw data theo rule mới.
