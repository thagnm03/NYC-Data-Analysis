# Bộ câu hỏi phản biện — NYC TLC Predictive Analysis

Các câu trả lời dưới đây được viết cho phần phản biện 20–45 giây/câu. Khi trả lời, ưu tiên: **kết luận trước → bằng chứng → giới hạn**.

## 1. Tại sao chọn NYC TLC thay vì bộ dữ liệu khác?

NYC TLC phù hợp với quy trình `EDA → problem discovery → temporal validation`: dữ liệu đủ lớn, có cấu trúc không gian–thời gian rõ, target đếm được và có business interpretation trực quan. So với BTS T-100, taxi zones ổn định hơn carrier–route histories và ít vấn đề entity churn hơn. Tuy nhiên, public release delay vẫn giới hạn use case near-real-time.

## 2. Tại sao lấy từ 01/2024 đến 05/2026?

Giai đoạn này cung cấp 24 tháng development trọn vẹn để quan sát seasonality và bốn quý validation, cộng năm tháng gần nhất làm final holdout. Tháng 06/2026 chưa được dùng vì tại thời điểm snapshot dữ liệu chưa tạo thành một file-month hoàn chỉnh, ổn định và đã audit. Việc chọn cutoff theo availability được khóa trước khi final evaluation.

## 3. Tại sao final test chỉ có năm tháng?

Mục tiêu là giữ lại toàn bộ dữ liệu mới nhất chưa từng tham gia EDA, feature design hay model selection. Năm tháng tạo 236.466 zone–blocks và gần 19 triệu pickups, đủ lớn cho frozen evaluation và segment analysis, nhưng chưa đủ để tuyên bố ổn định qua cả năm 2026.

## 4. Tại sao dùng block 4 giờ?

Block 4 giờ cân bằng giữa tính vận hành và độ ổn định thống kê: chi tiết hơn daily totals nhưng ít sparse/noisy hơn hourly counts. Nó cũng tạo sáu decision windows mỗi ngày. Đây là một design choice; bài toán khác có thể cần 1 hoặc 2 giờ và phải validation lại.

## 5. Tại sao dự báo theo pickup zone chứ không phải destination?

Use case là review nơi có khả năng phát sinh pickup trước block bắt đầu. Pickup zone có sẵn tại thời điểm quan sát lịch sử và trực tiếp gắn với vị trí bắt đầu nhu cầu đã được phục vụ. Destination của một chuyến tương lai chưa tồn tại ở prediction time nên không thể dùng làm feature.

## 6. “Realized pickup volume” khác “demand” như thế nào?

Realized pickup volume là số chuyến thực sự được ghi nhận. Latent demand còn gồm hành khách muốn đi nhưng không bắt được taxi hoặc chuyển sang phương tiện khác. Dữ liệu TLC không quan sát phần đó, nên kết quả chỉ hỗ trợ demand review, không chứng minh thiếu xe hay tổng nhu cầu thị trường.

## 7. Tại sao không random train/test split?

Random split phá thứ tự thời gian và có thể để model học từ quan sát tương lai của validation period. Rolling temporal folds mô phỏng đúng hơn việc dùng lịch sử đã biết để dự báo quý tiếp theo, đồng thời kiểm tra model qua nhiều mùa trong năm.

## 8. Nhóm ngăn data leakage như thế nào?

Mọi lag và rolling statistic đều được shift để chỉ dùng block đã hoàn tất. Encoder, scaler và model fit riêng trên train của từng fold. Same-block target, trip attributes phát sinh trong block và final-test information bị loại. Pipeline có 34 leakage/integrity checks và dùng cùng eligible rows khi so sánh model.

## 9. Tại sao previous-week là formal selection khi four-week mean tốt hơn?

Protocol ban đầu định nghĩa previous-week là primary baseline và formal fallback nếu regression models không qua toàn bộ selection gate. Four-week mean có development MAE thấp nhất nhưng chỉ được đăng ký là secondary baseline; rule chưa cho phép nó thay fallback. Nhóm giữ nguyên rule để tránh sửa tiêu chí sau khi đã thấy kết quả, đồng thời công bố đây là protocol limitation.

## 10. Tại sao không chọn Ridge dù final MAE thấp nhất?

Model selection đã khóa trước khi mở final test. Trên development folds, Ridge cải thiện aggregate metrics nhưng làm Low-tier MAE tăng 74,45%, nên không qua segment gate. MAE 14,17 trên final test là bằng chứng mô tả hậu nghiệm, không phải lý do hợp lệ để chọn lại model trên cùng holdout.

## 11. Vậy model nào thực sự “tốt nhất”?

Không có một câu trả lời duy nhất nếu không nêu criterion. Four-week mean có development MAE thấp nhất; Ridge có final-test MAE thấp nhất và development RMSE thấp nhất; previous-week là formal selected method vì tuân thủ protocol. Báo cáo giữ ba khái niệm này tách biệt.

## 12. MAE 16,22 có ý nghĩa gì?

Trung bình, dự báo previous-week lệch khoảng 16,22 pickups cho mỗi zone–block 4 giờ trên final test. Vì volume khác nhau rất mạnh giữa zones, nhóm báo thêm WAPE 20,22%, R² 0,935 và error theo activity tier; MAE một mình không đủ mô tả mọi segment.

## 13. R² 0,935 có phải mô hình rất tốt không?

R² cao chủ yếu cho thấy zone–time demand có persistence mạnh. Baseline đơn giản cũng đạt kết quả cao, nên giá trị gia tăng phải được đánh giá tương đối với baseline, không dựa vào R² tuyệt đối. Shock days và sparse zones vẫn tạo lỗi đáng kể.

## 14. Tại sao Low-tier WAPE hoặc tỷ lệ lỗi có thể rất cao?

Các zone ít hoạt động có target nhỏ và nhiều zero. Chỉ vài pickup sai lệch cũng tạo tỷ lệ lỗi lớn, trong khi MAE tuyệt đối vẫn nhỏ. Vì vậy nhóm dùng cả absolute metrics và segment metrics, không tối ưu một tỷ lệ duy nhất.

## 15. Tại sao polynomial regression không xuất hiện trong kết quả?

Polynomial model bị chặn ở feasibility gate do feature expansion trên hàng trăm nghìn zone–block rows và categorical identities tạo chi phí bộ nhớ/tính toán không hợp lý cho đồ án, trong khi chưa có bằng chứng nó sẽ vượt seasonal baselines. Quyết định gate được ghi lại thay vì âm thầm bỏ model.

## 16. Tại sao chưa thêm weather và event data?

Mục tiêu vòng này là thiết lập một leakage-safe benchmark chỉ từ calendar và pickup history. Weather/events có khả năng giải thích shock và cải thiện model, nhưng cần bảo đảm chúng thực sự có sẵn trước prediction time. Đây là hướng mở rộng, phải được đánh giá trên một final period mới.

## 17. Dữ liệu public có dùng để vận hành real-time được không?

Không đứng một mình. TLC công bố monthly files có độ trễ, trong khi backtest giả định stakeholder có pickup history kịp thời. Muốn vận hành, cần feed nội bộ gần real-time; nếu chỉ dùng public data thì phải lùi forecast horizon và lag features theo publication calendar.

## 18. Có thể dùng kết quả để điều phối taxi hoặc kết luận lợi nhuận không?

Chưa thể. Mô hình chỉ dự báo số pickup đã xảy ra trong lịch sử; nó không quan sát supply, unserved demand, chi phí repositioning, congestion hay phản ứng của tài xế. Automated dispatch hoặc profitability cần một decision model và causal/operational evaluation riêng.

## 19. Ngày 02/03/2026 xảy ra chuyện gì?

Đó là ngày có error lớn nhất trong final test, nhưng dữ liệu hiện tại không đủ để gán nguyên nhân. Cách trình bày đúng là mô tả nó như một shock/outlier và nói rằng event/weather data cần được bổ sung để kiểm tra giả thuyết nguyên nhân.

## 20. Nếu làm vòng tiếp theo, nhóm sẽ thay đổi gì?

Ba ưu tiên là: dùng hurdle/two-stage model cho zero-heavy zones; thêm weather và event features với availability audit; sửa protocol để cho baseline phụ cạnh tranh chính thức. Sau mọi thay đổi, nhóm phải khóa lại specification và dùng một final period mới, không tái sử dụng 01–05/2026 để lựa chọn.

## Câu hỏi ngắn về tính tái tạo

**Nguồn dữ liệu ở đâu?** NYC TLC Trip Record Data; manifest lưu URL, file size và checksum.

**Có chạy lại được không?** Có. README liệt kê script theo thứ tự, notebook chỉ đọc verified artifacts và mỗi bước có verifier.

**Các con số trên slide truy ngược được không?** Có. Mỗi slide có source path trong speaker notes; metrics lấy từ CSV/JSON do pipeline tạo.

