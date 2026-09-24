# Speaker notes — NYC TLC Predictive Analysis

Tổng thời lượng mục tiêu: **12–14 phút**, sau đó dành 3–5 phút cho phản biện. Các notes này cũng đã được nhúng trực tiếp vào file PowerPoint.

## Slide 1 — NYC TLC (45 giây)

“Bài toán của nhóm là: lịch sử theo zone và thời điểm có dự báo được số lượt đón Yellow Taxi trong block kế tiếp hay không? Chúng tôi dự báo **realized pickup volume**, tức số chuyến thực sự xảy ra, chứ không tuyên bố đo toàn bộ nhu cầu tiềm ẩn của hành khách.”

Chuyển ý: “Trước hết, tôi khóa rõ bài toán và quyết định mà dự báo hỗ trợ.”

## Slide 2 — Bài toán dự báo (1 phút)

- Một observation là một `pickup zone × block 4 giờ`.
- Target là số pickup hợp lệ trong block đó.
- Thời điểm dự báo là ngay trước khi block bắt đầu.
- MAE là metric chính; baseline chính là cùng zone–block của tuần trước.
- Kết quả chỉ dùng để ưu tiên các zone–block cần demand review, không tự phân xe hoặc tối ưu dispatch.

Chuyển ý: “Với prediction contract này, chúng tôi xây dựng dữ liệu từ trip records công khai.”

## Slide 3 — Dữ liệu và quy trình (1 phút)

“Dữ liệu gồm 29 tháng, từ tháng 1/2024 đến tháng 5/2026, với khoảng 108,9 triệu raw records. Development period là 2024–2025; năm tháng đầu 2026 được giữ kín cho final test. Raw grain là một chuyến taxi, còn modeling grain là một zone–block. Mỗi bước của pipeline đều có verifier và output machine-readable để tái tạo kết quả.”

Chuyển ý: “Trước khi aggregate, chúng tôi audit chất lượng dữ liệu theo đúng định nghĩa target.”

## Slide 4 — Data-quality audit (1 phút)

“Cleaning phụ thuộc vào bài toán. Chúng tôi loại exact duplicate, timestamp không thuộc file-month và location bucket 264/265 vì chúng làm sai định nghĩa pickup theo zone. Ngược lại, fare âm, distance không dương hoặc duration không dương vẫn được giữ vì chúng không chứng minh rằng pickup record không tồn tại. Sau cleaning, development data còn 89,64 triệu pickup hợp lệ.”

Chuyển ý: “Sau khi có target đáng tin cậy, EDA cho thấy tín hiệu thời gian rất mạnh.”

## Slide 5 — EDA xác nhận tín hiệu dự báo (1 phút 15 giây)

- Hình dạng time-of-week giữa 2024 và 2025 gần như trùng nhau, correlation 0,9967.
- Previous-week correlation sau khi center trong từng zone vẫn đạt 0,918.
- Top 20 zones chiếm 60,01% volume, nên metric tổng hợp có thể che giấu lỗi ở zone ít hoạt động.

“EDA vì vậy vừa ủng hộ seasonal baseline, vừa bắt buộc chúng tôi đánh giá theo activity tier.”

Chuyển ý: “Để đo khả năng tổng quát hóa đúng cách, validation phải đi theo thời gian.”

## Slide 6 — Temporal validation và leakage controls (1 phút)

“Random split không phù hợp vì có thể cho training nhìn thấy tương lai của validation. Chúng tôi dùng bốn expanding-window folds, mỗi fold dự báo một quý của năm 2025. Toàn bộ lag và rolling feature được shift trước target; encoder, scaler và model chỉ fit trên train. Final test 2026 không tham gia chọn feature, alpha hay model.”

Chuyển ý: “Với protocol đó, đây là kết quả trên bốn development folds.”

## Slide 7 — Development results (1 phút 15 giây)

“Previous-block và previous-day yếu hơn rõ rệt. Previous-week đạt MAE 16,39. Four-week seasonal mean thấp nhất với 14,88; Ridge đứng rất sát ở 14,97 và có RMSE tốt nhất. Điều quan trọng là không kết luận từ R² cao: persistence vốn đã rất mạnh, nên cần so trực tiếp với các baseline lịch sử.”

Chuyển ý: “Nhưng aggregate MAE chưa đủ để chọn model.”

## Slide 8 — Sparse-zone errors (1 phút 15 giây)

“Ridge giảm High-tier MAE 13,13%, nơi tập trung phần lớn volume. Tuy nhiên, ở Low tier, MAE tăng 74,45%. Các zone này có nhiều target bằng zero; Ridge thường dự báo dương nên tạo sai số. Vì selection rule yêu cầu không làm xấu nghiêm trọng bất kỳ activity tier nào, Ridge không qua segment gate.”

Chuyển ý: “Do đó quyết định trước khi mở final test có hai lớp: formal selection và empirical winner.”

## Slide 9 — Quyết định trước final test (1 phút)

“Theo protocol đã khóa, previous-week là formal fallback vì không regression model nào qua toàn bộ rule. Four-week mean là empirical winner về MAE, nhưng rule ban đầu không cho baseline phụ thay formal fallback. Đây là một hạn chế thiết kế nghiên cứu; chúng tôi công bố nó thay vì sửa rule sau khi biết kết quả.”

Chuyển ý: “Sau khi khóa quyết định, chúng tôi mới mở năm tháng final test.”

## Slide 10 — Frozen final test (1 phút 15 giây)

“Final test có 236.466 zone–blocks và gần 18,97 triệu pickups. Formal previous-week đạt MAE 16,22, WAPE 20,22% và R² 0,935. Ridge có MAE thấp hơn, 14,17, nhưng final test chỉ dùng để đánh giá quyết định đã khóa, không dùng để chọn lại model. Previous-week không suy giảm tổng thể rõ so với development.”

Chuyển ý: “Kết quả trung bình ổn định, nhưng sai số theo thời gian cho thấy các shock vẫn là rủi ro lớn.”

## Slide 11 — Monthly error và shock (1 phút 15 giây)

“Ridge tốt nhất trong tháng 1; four-week mean tốt nhất từ tháng 2 đến tháng 5. Ngày 2/3/2026 là ngày tệ nhất: actual 113.243 pickups nhưng previous-week aggregate chỉ 26.000. Seasonal lag còn có thể sao chép một shock sang đúng tuần sau và tạo sai số theo chiều ngược lại. Vì chưa có event data, chúng tôi không gán nguyên nhân cho các ngày này.”

Chuyển ý: “Từ toàn bộ bằng chứng, kết luận cuối cùng gồm cả giá trị sử dụng và giới hạn.”

## Slide 12 — Kết luận (1 phút)

“Historical zone–time patterns có giá trị dự báo rõ ràng. Previous-week là benchmark mạnh, dễ giải thích; final WAPE 20,22% phù hợp để tạo tín hiệu demand review nhưng chưa đủ để automated dispatch. Model phức tạp hơn không bảo đảm lợi ích đồng đều: Ridge giúp high-volume zones nhưng làm xấu sparse zones. Các giới hạn chính là độ trễ của public files, target chỉ là realized demand, và chưa có weather hoặc event data.”

Kết thúc: “Nếu tiếp tục nghiên cứu, chúng tôi sẽ thử hurdle model cho zero-heavy zones, bổ sung weather/event features, rồi khóa một final period mới. Xin cảm ơn và mời câu hỏi.”

## Checklist rehearsal

- Nói rõ `realized pickup volume` ngay ở slide 1 và nhắc lại ở kết luận.
- Không đọc toàn bộ bảng; chỉ chỉ ra con số phục vụ lập luận.
- Phân biệt rõ **formal selected method**, **empirical winner** và **descriptive final-test result**.
- Không nói “Ridge tốt nhất” nếu không kèm phạm vi metric/segment.
- Không gán nguyên nhân cho ngày shock nếu chưa có event data.
- Nếu thiếu thời gian, rút slide 4, 6 và 9 xuống khoảng 40 giây mỗi slide; không bỏ slide 8.

