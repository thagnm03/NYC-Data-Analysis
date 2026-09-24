# Bước 3 — Data Understanding và Data-Quality Audit

**Trạng thái:** hoàn thành, chờ người dùng kiểm tra  
**Audit scope:** development data 01/2024–12/2025  
**Final test 01/2026–05/2026:** không được đọc ở mức nội dung  
**Nguồn:** [NYC TLC Trip Record Data](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page) và [Yellow Taxi Data Dictionary](https://www.nyc.gov/assets/tlc/downloads/pdf/data_dictionary_trip_records_yellow.pdf)

## 1. Dataset thực sự chứa gì?

- Raw grain: một completed Yellow Taxi trip record trên mỗi dòng.
- Development data: 24 monthly Parquet files, 89,892,322 rows.
- 2024: 41,169,720 rows.
- 2025: 48,722,602 rows.
- Taxi-zone lookup: 265 unique `LocationID`, liên tục từ 1 đến 265.
- Hai trường đủ để tạo pickup-count target là `tpep_pickup_datetime` và `PULocationID`; cả hai không có null trong development data.

TLC cho biết dữ liệu được các technology providers cung cấp và TLC không bảo đảm tuyệt đối độ chính xác. Files được công bố theo tháng, thường trễ khoảng hai tháng.

## 2. Schema drift

Có hai schema variants nhưng data type của 19 cột chung ổn định:

| Period | Columns | Thay đổi |
|---|---:|---|
| 01/2024–12/2024 | 19 | Base Yellow Taxi schema |
| 01/2025–12/2025 | 20 | Thêm `cbd_congestion_fee` |

Theo TLC, `cbd_congestion_fee` phản ánh Congestion Relief Zone charge bắt đầu ngày 05/01/2025. Cột này không tồn tại trong 2024 là structural absence, không phải missing value cần impute. Pipeline tiếp tục dùng `union_by_name` nhưng cột này không tham gia target hoặc model hiện tại.

## 3. Missingness

Năm trường cùng có đúng 15,703,126 null, tương đương 17.47% raw rows:

- `passenger_count`;
- `RatecodeID`;
- `store_and_fwd_flag`;
- `congestion_surcharge`;
- `Airport_fee`.

Audit đồng xuất hiện cho thấy:

- cả năm trường luôn null cùng nhau;
- không có row chỉ thiếu một đến bốn trường trong nhóm;
- toàn bộ 15,703,126 rows đó có `payment_type = 0`;
- không có `payment_type = 0` row nào nằm ngoài missing bundle.

Data dictionary định nghĩa payment type 0 là Flex Fare. Vì target chỉ cần pickup time và pickup zone, bundle này không làm mất target rows và không được impute hoặc loại bỏ trong project hiện tại.

Các trường còn lại không có null khi hiện diện. `cbd_congestion_fee` có mặt đủ 12 tháng 2025 và không có null trong các tháng đó.

## 4. Duplicate và file-month integrity

- 5 duplicate rows được xác nhận trùng toàn bộ trường, nằm tại 02/2024, 07/2024, 10/2024 và 07/2025.
- Cả 5 đều nằm trong nhóm đủ điều kiện tạo target.
- 634 rows có pickup timestamp nằm ngoài nominal month của file.
- Không có `PULocationID` hoặc `DOLocationID` ngoài khoảng 1–265.
- `PULocationID` không có null.

Cleaning sẽ giữ đúng một bản của duplicate, đồng thời bắt buộc pickup timestamp thuộc nominal file month.

## 5. Zone quality

- 249,244 rows (0.2773%) dùng pickup bucket 264 hoặc 265.
- 694,524 rows (0.7726%) dùng dropoff bucket 264 hoặc 265.
- Zone lookup không có null/duplicate ID.
- Modeling chỉ dùng pickup zones 1–263; destination không được dùng làm feature cho next-block pickup prediction.

Sau khi yêu cầu pickup timestamp đúng file-month và pickup zone 1–263, còn 89,642,446 rows, tức 99.7220% raw development data. Sau khi bỏ 5 duplicate, số rows dùng tạo target là **89,642,441**.

## 6. Các anomaly không được dùng để lọc target

| Check | Rows | Share of raw rows | Quyết định |
|---|---:|---:|---|
| Duration không dương | 559,814 | 0.6228% | Giữ cho pickup target |
| Duration trên 6 giờ | 36,939 | 0.0411% | Giữ cho pickup target |
| Distance không dương | 2,179,263 | 2.4243% | Giữ cho pickup target |
| Distance trên 200 miles | 3,380 | 0.0038% | Giữ cho pickup target |
| Fare âm | 3,579,644 | 3.9821% | Giữ cho pickup target |
| Total amount âm | 1,583,065 | 1.7611% | Giữ cho pickup target |
| Passenger count không dương | 661,416 | 0.7358% | Giữ cho pickup target |
| Passenger count trên 8 | 70 | <0.0001% | Giữ cho pickup target |

Lý do: target là recorded pickup count. Các trường trên không quyết định một record có thể được gán vào pickup zone/time hay không. Loại chúng sẽ thay đổi target theo một bộ quy tắc chất lượng chuyến đi chưa được định nghĩa trong project brief.

Các anomaly vẫn quan trọng về data understanding:

- toàn bộ 536,131 VendorID 7 rows có duration không dương;
- mọi fare/total âm đều thuộc VendorID 2 trong development period;
- missing bundle xuất hiện ở VendorID 1, 2 và 6, trong đó 100% VendorID 6 rows thuộc bundle.

Đây là association trong dữ liệu, không phải bằng chứng về nguyên nhân hoặc lỗi của vendor.

## 7. Volume movement

Raw volume tăng từ 41.17 triệu rows năm 2024 lên 48.72 triệu năm 2025. Month-over-month raw row change dao động từ khoảng -13.1% đến +21.9%.

Ở bước này chỉ ghi nhận movement. Chưa gọi đó là demand growth hoặc seasonality vì thay đổi có thể liên quan đến số ngày trong tháng, mùa vụ, provider mix, hoạt động taxi hoặc submission process. Những giả thuyết này thuộc EDA ở Bước 5.

## 8. Cleaning decision

Cleaning rules đã được khóa trong `CLEANING_LOG.md`:

1. pickup timestamp phải thuộc nominal file month;
2. pickup zone phải từ 1 đến 263;
3. bỏ duplicate hoàn toàn, giữ một dòng;
4. không lọc target bằng dropoff, duration, distance, fare, payment hoặc passenger count.

## 9. Gate Bước 3

- [x] Grain, source, schema và data types đã được ghi nhận.
- [x] Schema drift 2025 được xác định.
- [x] Missingness theo month/column được tính.
- [x] Duplicate được kiểm tra và xác nhận.
- [x] Timestamp, zone, duration, distance, fare và passenger anomalies được định lượng.
- [x] Zone lookup được kiểm tra.
- [x] Target-specific cleaning rules đã được khóa trước panel/modeling.
- [x] Final-test content không được sử dụng.

**Kết luận gate:** dữ liệu đạt yêu cầu để chuyển sang Bước 4 — tạo panel `pickup zone × block 4 giờ`. Các vấn đề lớn ở fare/duration không cản trở pickup-count target, nhưng ngăn việc tái sử dụng dataset này cho fare/duration modeling mà không có cleaning riêng.

## 10. Output tables

Các file được tạo trong `outputs/audit/`:

- `schema_by_month.csv`, `schema_variants.csv`;
- `monthly_audit.csv`, `yearly_counts.csv`;
- `missingness_by_month.csv`, `missingness_overall.csv`;
- `missing_bundle_summary.csv`;
- `categorical_value_counts.csv`, `numeric_summary.csv`;
- `quality_by_vendor.csv`;
- `duplicate_groups_summary.csv`;
- `zone_lookup_audit.json`;
- `audit_summary.json`, `audit_run_metadata.json`, `audit_verification.json`.
