# Rà soát mức độ sẵn sàng của bốn đồ án — 07/10/2026

## Kết luận

Chưa nên coi cả bốn dự án là đã hoàn tất để nộp ngay. DS310 sẵn sàng nhất; DS111 có nội dung và slide nhưng còn lỗi tài liệu và môi trường; IE212 có hệ thống và báo cáo nguồn khá đầy đủ nhưng còn khoảng trống vận hành và đóng gói; DS314 có kết quả retrieval kiểm chứng được nhưng thiếu bộ bàn giao và cần làm rõ phạm vi môn học.

Đây là đánh giá dựa trên file, mã nguồn và các kiểm tra nêu dưới đây. Chưa có rubric, đề bài chính thức hoặc template của giảng viên để chứng nhận đủ mọi yêu cầu hay dự đoán điểm. Không chạy lại full training, xử lý toàn bộ raw data hoặc toàn bộ Docker integration gates. Không chỉnh model, threshold hay quyết định đã khóa. Một số verifier chạy lại có ghi mới JSON kết quả kiểm tra theo hành vi mặc định của script.

| Dự án | Nội dung hiện có | Mức sẵn sàng hiện tại | Điểm cần xử lý trước nộp |
|---|---|---|---|
| DS111 — NYC TLC | Final report Markdown, notebook có outputs, PPTX 12 slide, speaker notes, Q&A | Gần sẵn sàng về nội dung; chưa sẵn sàng chạy lại theo README | Sửa Q&A polynomial; phục hồi môi trường; xuất báo cáo theo yêu cầu |
| DS310 — WDC matching | Báo cáo PDF 8 trang, PPTX 10 slide, demo inference, script và Q&A | Sẵn sàng nhất trong bốn dự án | Đối chiếu rubric; chuẩn bị bảo vệ giới hạn một epoch và kết quả âm |
| DS314 — LOCuST retrieval | Code BM25/BM25F/dense/RRF, protocol, kết quả và phân tích lỗi | Chưa hoàn tất bộ bàn giao | Phạm vi IR so với yêu cầu IE; sửa diễn giải ablation; môi trường; báo cáo tổng hợp, slide, demo/Q&A |
| IE212 — Citi Bike | Batch/streaming, serving/dashboard, monitoring/recovery, report và demo runbook | Hệ thống có bằng chứng tốt; chưa xác nhận live demo trên máy này | Kiểm tra silent/stale source; sửa lệch số liệu; Docker rehearsal; PDF/DOCX và slide nếu yêu cầu |

## Kiểm tra thực hiện lần này

### DS310

- `python -m unittest discover -s tests -q`: 30/30 tests PASS.
- `python -m src.evaluation.verify_release`: 32/32 checks PASS, gồm seal, predictions, thresholds, metrics và CLI.
- `python -m src.demo.verify_demo`: 6/6 inference comparisons PASS; drift lớn nhất khoảng 3,87 × 10^-7.
- Đã đọc báo cáo cuối, protocol/reproducibility, self-review và script/Q&A. Chưa render lại PDF/PPTX trong lần rà này.

### DS314

- Tính lại metrics từ rankings và qrels test hiện có bằng evaluator của dự án: cả BM25, BM25F, dense và RRF khớp metrics đã lưu, sai lệch tối đa bằng 0.
- Accuracy@10 lần lượt 75,7329%; 80,4560%; 72,3127%; 89,5765% trên 307 queries.
- `.venv` không khởi động do trỏ tới Python của tài khoản `thang` trên máy cũ.
- Runtime Python hiện có chạy thử discovery nhưng thiếu `rank_bm25`; không xác nhận toàn bộ unit tests PASS trên máy này.
- Không thấy báo cáo tổng hợp cuối, slide deck, speaker script/Q&A hoặc giao diện demo trong bộ file được rà. Các tài liệu phương pháp hiện là nhiều Markdown riêng.

### DS111

- `run_pipeline.ps1 -Stage verify` dừng ở bước đầu vì `.venv` ở workspace cha trỏ tới Python313 của tài khoản `thang` đã không còn.
- Dùng Python runtime hiện tại chạy `verify_step9.py`: 15/16 checks PASS; thiếu nbformat, nbclient, nbconvert, jupyterlab. Headline số liệu, frozen hashes và notebook có outputs đều đạt các kiểm tra của script; upstream checks ở đây đọc evidence đã lưu.
- `verify_step10.py`: PASS về sự tồn tại deliverables, 12 slides, 12 notes, 4 native charts và nội dung kiểm tra.
- `verify_final_evaluation.py` không chạy được bằng runtime thay thế vì thiếu duckdb. Không gọi kết quả 29/29 lịch sử là kiểm chứng mới của lần rà này.
- Báo cáo cuối hiện ở Markdown; chưa thấy bản PDF/DOCX cuối trong thư mục dự án.

### IE212

- Full `pytest -q` lỗi collection ở hai modules do Windows Application Control chặn DLL `confluent_kafka.cimpl`.
- Chạy phần còn lại, loại hai modules bị chặn: 40 tests PASS. Không thể nói 56/56 đã PASS lại trên máy này.
- `verify_step15.py`: PASS về cấu trúc tài liệu, 29 local links và evidence Bước 4–14. Verifier có hạn chế đối chiếu số liệu được mô tả bên dưới.
- `docker compose ... ps`: không có container Compose của project đang chạy tại thời điểm kiểm tra. Chưa xác nhận dashboard/live end-to-end demo hoạt động trong lần rà này.

## Những điểm dễ bị phản biện và mức độ nghiêm trọng

### 1. DS111: Q&A polynomial mâu thuẫn mã và báo cáo — cần sửa trước bảo vệ

`deliverables/Q_AND_A.md`, câu 15, giải thích polynomial không chạy vì feasibility/memory cost. Trong khi `FINAL_REPORT.md`, `config/modeling_config.json`, `scripts/run_models.py` và `outputs/modeling/polynomial_gate.json` thống nhất rằng residual-curvature gate không đạt: có 4 deciles đủ magnitude nhưng không có cả hai dấu residual.

Câu trả lời đúng: polynomial degree 2 không được fit vì không đạt residual-curvature gate đã khóa; không dùng lý do hết RAM như một kết quả thực nghiệm đã chứng minh. Nếu môn bắt buộc phải chạy polynomial, cần đối chiếu đề bài: gate có lý do không tự động thay thế một deliverable bắt buộc.

### 2. DS111: vì sao formal selected method kém four-week mean? — phản biện mạnh nhưng bảo vệ được

Four-week mean thắng previous-week ở 4/4 development folds và có mean MAE 14,88 so với 16,39. Protocol chỉ cho regression thay fallback, nên vẫn chọn previous-week. Đây là hạn chế thiết kế selection rule, không phải bằng chứng previous-week là phương pháp tốt nhất.

Nên nói rõ ngay trên slide kết quả: empirical development winner là four-week mean; formal method theo protocol là previous-week. Mức 5% low-tier guardrail cũng cần được giải thích như một policy giả định, chưa phải yêu cầu đã xác nhận từ stakeholder thật. Không đổi formal model sau khi đã xem final test. Ridge alpha=1000 nằm ở biên grid cũng là giới hạn tuning đã được báo cáo.

### 3. DS314: phạm vi bài toán so với môn Information Extraction — phải đối chiếu đề bài

README và problem formulation ghi rõ đây là Information Retrieval/table retrieval: xếp hạng table IDs, không trích xuất entities, relations, events hoặc facts. Một hệ thống retrieval có thể phù hợp nếu giảng viên chấp nhận phạm vi đó; chưa có rubric để xác nhận. Nếu đề bài yêu cầu một task extraction cụ thể, kết quả retrieval 89,58% không bù được việc sai phạm vi.

### 4. DS314: ablation BM25–BM25F chưa cô lập riêng field-aware scoring — cần hạ mức khẳng định

`docs/bm25f_ablation.md` nói so sánh isolates field-aware scoring. Tuy nhiên baseline dùng `rank_bm25.BM25Okapi`: IDF log((N-df+0.5)/(df+0.5)) và xử lý negative IDF bằng epsilon floor. BM25F tự viết dùng log(1+(N-df+0.5)/(df+0.5)). Hai triển khai khác cả field handling và IDF.

Kết quả xếp hạng và metrics vẫn có giá trị, nhưng chưa đủ để quy toàn bộ cải thiện cho field weighting. Cách hoàn tất nhanh là sửa mô tả thành so sánh hai cấu hình lexical retrieval và công khai khác biệt IDF. Nếu cần ablation sạch, thêm đối chứng cùng IDF trên development theo protocol mới; không tune tiếp trên test đã mở.

### 5. DS314: Accuracy@10 không phải tỷ lệ trả lời đúng hoàn toàn

89,58% là trung bình tỷ lệ gold tables tìm được trong top 10, cho partial credit với câu có hai tables. All-target coverage là 264/307 = 85,99%; official Exact Match là 40,07%; Accuracy@1 là 42,83%. Đã có giải thích đúng trong error analysis, nhưng `docs/hybrid_rrf.md` còn câu diễn giải theo khả năng every needed table xuất hiện. Cần thống nhất cách gọi, tránh nói hệ thống trả lời chính xác 89,58% câu hỏi.

Chưa thấy confidence interval/paired significance analysis cho uplift 9,12 điểm phần trăm; do đó chỉ kết luận cải thiện quan sát trên benchmark này. Không cần đổi model để hoàn tất báo cáo, nhưng nên nêu giới hạn và bổ sung tài liệu tham khảo/phương trình nguồn trong báo cáo tổng hợp.

### 6. IE212: stale station khi không còn event mới — khoảng trống correctness đáng kiểm tra

Collector bỏ qua exact duplicate snapshot. Station-state processor bỏ event có event_time không mới hơn state trước. Alert classification dùng `source_age_seconds` trong payload; các processors được rà không đăng ký timer để chuyển station thành STALE chỉ vì thời gian thực trôi qua.

Theo đường mã này, một feed đóng băng hoặc station im lặng có thể giữ current business state cũ mà chưa tự sinh lifecycle STALE. Monitoring projection age có thể bắt việc pipeline ngừng cập nhật toàn cục; điều đó chưa chứng minh từng station im lặng được xử lý đúng khi các station khác vẫn cập nhật.

Đây là nhận định từ mã, chưa chạy fault scenario mới để xác nhận end-to-end. Cần một scenario riêng: station ban đầu fresh, dừng update quá ngưỡng, tiếp tục cho station khác cập nhật, quan sát current state/priority queue và INSPECT_STATION. Nếu chưa hỗ trợ, phải ghi giới hạn cụ thể hoặc bổ sung timer/freshness evaluation cùng kiểm tra tương ứng.

### 7. IE212: báo cáo và verifier lệch evidence — cần sửa trước nộp

`docs/final-report.md` ghi final demo 122,213 giây, còn `artifacts/step14/verification-summary.json` hiện là 80,437 giây. `verify_step15.py` yêu cầu report chứa chuỗi 122,213 nhưng lại lấy demo duration mới từ JSON để xuất kết quả mà không so hai giá trị; vì vậy PASS không chứng minh chúng khớp.

Chọn và ghi rõ run nào là nguồn số liệu của báo cáo, hoặc cập nhật về latest run đồng bộ README/report/review. Verifier nên kiểm tra số liệu report với chính evidence được chỉ định thay vì chỉ kiểm tra một chuỗi hard-code.

### 8. DS310: một epoch và kết quả âm — giới hạn nghiên cứu, không phải lý do đồ án thất bại

M2 vượt lexical baselines, nhưng M3 không cải thiện nhất quán, cả hai delta-F1 CI chứa 0 và hard-negative FPR tăng. Báo cáo hiện trung thực, có ba seeds, audit, thresholds khóa và error analysis nên vẫn có thể bảo vệ như một nghiên cứu thực nghiệm.

Dễ bị hỏi: chỉ một epoch dù có GPU; representation screening một seed; pair-bootstrap chưa xử lý shared-product dependence; dataset tải mirror; novelty của loss đơn giản. Câu trả lời phải giữ phạm vi kết luận đúng như báo cáo. Không nói M3 tốt hơn hay CI chứng minh hai phương pháp tương đương. Việc khóa budget giúp giữ protocol nhưng không chứng minh model đã hội tụ.

### 9. IE212: Big Data và giá trị nghiệp vụ — chuẩn bị chứng minh đúng mức

Kafka/Spark có justification và integration evidence; batch gần 3,86 triệu trips là workload cụ thể. Tuy nhiên single-host, một broker/worker chưa chứng minh scale-out hoặc high availability. Baseline một tháng và bridge short_name chưa effective-dated làm historical context có giới hạn. Priority weights/persistence là heuristic chưa được đánh giá bằng ground truth rebalancing outcomes.

Nên bảo vệ contribution về event-time correctness, replay, idempotency, recovery và operational workflow; không nói đã tối ưu điều phối hoặc chứng minh hiệu quả kinh doanh ngoài đời.

## Checklist đóng trước khi nộp

1. Đối chiếu bốn đề bài/rubric: task được phép, mô hình bắt buộc, cấu trúc báo cáo, thời lượng, định dạng và cách đóng gói. Ưu tiên xác nhận phạm vi DS314.
2. Sửa các mâu thuẫn đã xác định: DS111 Q&A polynomial; DS314 ablation/metric wording; IE212 demo duration và verifier.
3. Khôi phục môi trường DS111/DS314 trên máy hiện tại; giải quyết đường chạy IE212 qua runtime/Docker được hỗ trợ, không coi lỗi DLL là test code PASS.
4. Kiểm tra silent/stale scenario của IE212 và ghi đúng giới hạn nếu chưa hoàn tất.
5. Xuất báo cáo DS111/IE212 sang định dạng giảng viên yêu cầu; tổng hợp báo cáo DS314 với nguồn tham khảo, phương pháp, protocol, kết quả, phân tích lỗi và giới hạn.
6. Hoàn tất slide và script/Q&A cho DS314; slide IE212 nếu buổi bảo vệ yêu cầu. DS310/DS111 đã có deck.
7. Rehearsal đúng laptop dùng bảo vệ: notebook DS111, inference DS310, một query retrieval DS314, end-to-end demo IE212. Chuẩn bị screenshots/video từ run thật để dự phòng.
8. Tạo gói nộp có README, source, configs, dependency locks, báo cáo, slides và evidence đủ nhỏ; hướng dẫn lấy raw/checkpoints nếu không đính kèm. Không cần nén `.venv`, cache hoặc toàn bộ raw data không được yêu cầu.

## Nhận định về nguy cơ bị bắt bẻ

Không có căn cứ dự đoán giảng viên sẽ bắt bẻ nặng hoặc chấm bao nhiêu điểm. Các dự án có nội dung thực nghiệm và bằng chứng đáng kể. Rủi ro cao hiện tại đến từ phạm vi môn học, mâu thuẫn tài liệu, khả năng chạy lại và khẳng định vượt bằng chứng; không đơn thuần từ việc model có điểm thấp hay phương pháp mới không thắng baseline.

Nếu đóng các mục bắt buộc ở trên và người trình bày hiểu rõ data split, metric, model-selection rule, state/replay và giới hạn, phần phản biện còn lại phần lớn là những câu hỏi chuyên môn có thể trả lời bằng evidence. DS310 hiện có mức sẵn sàng cao nhất; DS314 cần ưu tiên về scope và bộ bàn giao.
