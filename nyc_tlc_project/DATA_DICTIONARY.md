# Data Dictionary rút gọn — NYC TLC Yellow Taxi

Nguồn định nghĩa chính thức: [NYC TLC Yellow Taxi Trip Records Data Dictionary](https://www.nyc.gov/assets/tlc/downloads/pdf/data_dictionary_trip_records_yellow.pdf).

| Field | Ý nghĩa | Vai trò trong đồ án |
|---|---|---|
| `VendorID` | Mã nhà cung cấp TPEP gửi record | Data understanding/audit |
| `tpep_pickup_datetime` | Thời điểm đồng hồ taxi được bật | Bắt buộc để tạo target block |
| `tpep_dropoff_datetime` | Thời điểm đồng hồ taxi được tắt | Chỉ audit; không dùng dự đoán pickup tương lai |
| `passenger_count` | Số hành khách được báo cáo | Chỉ audit/context |
| `trip_distance` | Quãng đường theo taximeter, đơn vị mile | Chỉ audit/context |
| `RatecodeID` | Rate code cuối chuyến | Chỉ audit/context |
| `store_and_fwd_flag` | Record có được lưu trong xe trước khi gửi hay không | Chỉ audit/context |
| `PULocationID` | Taxi zone nơi đồng hồ được bật | Bắt buộc để tạo target theo pickup zone |
| `DOLocationID` | Taxi zone nơi đồng hồ được tắt | Chỉ audit; loại khỏi feature dự báo pickup |
| `payment_type` | Mã hình thức thanh toán | Chỉ audit/context |
| `fare_amount` | Cước thời gian và quãng đường do đồng hồ tính | Chỉ audit/context |
| `extra` | Phụ phí khác | Không dùng cho target/model hiện tại |
| `mta_tax` | Thuế MTA tự động áp dụng khi phù hợp | Không dùng cho target/model hiện tại |
| `tip_amount` | Tip thẻ; tip tiền mặt không được ghi nhận | Không dùng cho target/model hiện tại |
| `tolls_amount` | Tổng toll của chuyến | Không dùng cho target/model hiện tại |
| `improvement_surcharge` | Improvement surcharge | Không dùng cho target/model hiện tại |
| `total_amount` | Tổng tiền thu hành khách, không gồm tip tiền mặt | Chỉ audit/context |
| `congestion_surcharge` | NYS congestion surcharge | Không dùng cho target/model hiện tại |
| `Airport_fee` | Phí pickup tại LGA/JFK | Không dùng cho target/model hiện tại |
| `cbd_congestion_fee` | Phí Congestion Relief Zone, bắt đầu 05/01/2025 | Schema drift từ 2025; không dùng cho target/model |

## Grain và khóa

- Grain nguồn: một completed Yellow Taxi trip record trên mỗi dòng.
- Dataset không cung cấp trip ID duy nhất đáng tin cậy trong schema này.
- Duplicate audit sử dụng hash của toàn bộ trường trong từng monthly file.
- Grain modeling sẽ được tạo ở Bước 4: một `pickup zone × block 4 giờ`.

## Hai trường quyết định target

Target pickup count chỉ phụ thuộc trực tiếp vào:

1. `tpep_pickup_datetime` để gán block thời gian;
2. `PULocationID` để gán pickup zone.

Fare, distance, duration và dropoff fields vẫn được audit để hiểu chất lượng nguồn nhưng không được dùng để loại một pickup khỏi target chỉ vì giá trị của chúng bất thường. Làm vậy có thể thay đổi định nghĩa target từ “recorded pickups” thành một khái niệm khác không được khóa trong project brief.
