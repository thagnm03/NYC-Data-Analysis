# Cleaning Log — Target pickup count

**Phiên bản:** 1.0  
**Khóa tại:** Bước 3, trước panel construction và modeling  
**Development audit:** 01/2024–12/2025  
**Final test:** 01/2026–05/2026 vẫn chưa được dùng ở mức nội dung

## Định nghĩa target được bảo vệ

Target là số Yellow Taxi pickup records hợp lệ theo `pickup zone × block 4 giờ`. Cleaning chỉ được loại record không thể gán đáng tin cậy vào đúng pickup month/zone hoặc là duplicate hoàn toàn.

## Quy tắc đã khóa

| ID | Quy tắc | Áp dụng | Lý do | Tác động trên development data |
|---|---|---|---|---:|
| C01 | Chỉ đọc monthly files 01/2024–12/2025 trong development | Giữ | Bảo vệ final test 2026 | 89,892,322 raw rows được audit |
| C02 | `tpep_pickup_datetime` phải nằm trong đúng tháng ghi trên filename | Lọc | Ngăn record lạc file-month và tránh double count giữa file | 634 rows bị flag; có overlap với C03 |
| C03 | `PULocationID` phải từ 1 đến 263 | Lọc | 264 là Unknown, 265 là Outside of NYC; không phải pickup zone modeling | 249,244 rows bị flag; có overlap với C02 |
| C04 | Bỏ duplicate hoàn toàn trong cùng monthly file, giữ một dòng | Lọc | Tránh cùng một trip record làm tăng target hai lần | 5 duplicate rows, tất cả nằm trong target-valid rows |
| C05 | Không lọc theo dropoff time/duration | Giữ | Không cần để gán pickup target; lọc sẽ đổi định nghĩa target | 559,814 duration không dương; 36,939 trên 6 giờ vẫn giữ |
| C06 | Không lọc theo `trip_distance` | Giữ | Distance không cấu thành pickup count | 2,179,263 không dương; 3,380 trên 200 miles vẫn giữ |
| C07 | Không lọc theo fare/total/payment | Giữ | Fare không cấu thành pickup count; giá trị âm có thể mang semantics điều chỉnh chưa được xác minh | 3,579,644 fare âm; 1,583,065 total âm vẫn giữ |
| C08 | Không lọc theo `passenger_count` | Giữ | Một recorded pickup vẫn là pickup dù passenger field thiếu/khác thường | 15,703,126 null; 661,416 không dương; 70 trên 8 vẫn giữ |
| C09 | Không coi `cbd_congestion_fee` vắng trong 2024 là null data | Giữ theo schema | Cột chỉ xuất hiện từ 2025 và không dùng cho target/model | 12 tháng structurally absent |

## Row accounting

| Giai đoạn | Rows |
|---|---:|
| Raw development records | 89,892,322 |
| Có pickup time đúng file-month và pickup zone 1–263 | 89,642,446 |
| Target-valid exact duplicates bị bỏ | 5 |
| **Rows còn lại để tạo target panel** | **89,642,441** |

`C02` và `C03` có hai rows overlap, nên không cộng riêng 634 và 249,244 để tính tổng loại. Union của hai điều kiện loại là 249,876 rows.

## Thứ tự áp dụng ở Bước 4

1. đọc từng monthly file riêng;
2. bỏ duplicate hoàn toàn trên toàn bộ columns có trong file;
3. giữ pickup timestamp thuộc đúng nominal file month;
4. giữ `PULocationID BETWEEN 1 AND 263`;
5. aggregate theo `PULocationID × 4-hour block`;
6. kiểm tra tổng panel target phải bằng **89,642,441** trên development period.

## Quy tắc cho final test

Khi mở 01–05/2026 ở Bước 8, áp dụng nguyên vẹn C02–C09. Không được thay threshold hoặc tạo cleaning rule mới sau khi thấy kết quả model trên final test. Nếu xuất hiện lỗi schema nghiêm trọng chưa từng thấy, phải ghi nhận là external-data failure thay vì âm thầm sửa để cải thiện metric.
