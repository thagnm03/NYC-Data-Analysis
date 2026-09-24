# Bước 2 — Kết quả tải và xác minh dữ liệu

**Trạng thái:** hoàn thành, chờ người dùng kiểm tra  
**Phạm vi:** 2024-01 đến 2026-05  
**Nguồn:** NYC TLC official public distribution endpoint

## Kết quả

| Kiểm tra | Kết quả |
|---|---:|
| Số tháng kỳ vọng | 29 |
| Số monthly Parquet đã có | 29 |
| File bị thiếu | 0 |
| File tháng ngoài phạm vi | 0 |
| Tổng raw rows từ Parquet metadata | 108,891,604 |
| Tổng dung lượng monthly Parquet | 1,848,231,983 bytes (khoảng 1.72 GiB) |
| Schema variants | 2 |
| Parquet không đọc được | 0 |
| File sai kích thước so với manifest | 0 |
| File sai SHA-256 khi rehash | 0 |

Sáu monthly Parquet của 01–06/2024 và taxi-zone lookup được hard-link từ cache pilot đã có. Hai mươi ba monthly Parquet còn lại được tải từ official TLC endpoint.

## Schema-level observation

- Các file 2024 có 19 cột.
- Các file từ 2025 có 20 cột.
- Cột được bổ sung là `cbd_congestion_fee`.
- Hai cột cần cho target aggregation, `tpep_pickup_datetime` và `PULocationID`, tồn tại trong toàn bộ 29 file.

Đây mới là file-level verification. Ý nghĩa, kiểu dữ liệu, missingness, duplicates, timestamp/zone anomalies và schema drift sẽ được phân tích ở Bước 3.

## Artifacts

- Config: `config/project_config.json`
- Downloader: `scripts/download_data.py`
- Verifier: `scripts/verify_download.py`
- Raw manifest: `data/raw/manifest.json`
- Verification report: `data/raw/verification_report.json`

## Reproduce

Từ workspace root:

```powershell
.\.venv\Scripts\python.exe nyc_tlc_project\scripts\download_data.py
.\.venv\Scripts\python.exe nyc_tlc_project\scripts\verify_download.py --rehash
```

Downloader là idempotent: file hợp lệ đã có được tái sử dụng. Network download ghi vào `.part` trước khi đổi tên thành file chính thức.

## Gate Bước 2

- [x] Config sinh đúng chuỗi 29 tháng liên tục.
- [x] Đủ 29 monthly Parquet.
- [x] Manifest có URL, bytes và SHA-256.
- [x] Từng Parquet có magic bytes hợp lệ và đọc được metadata.
- [x] Tất cả monthly files có row count lớn hơn 0.
- [x] Hai cột modeling bắt buộc tồn tại trong mọi file.
- [x] Zone lookup đọc được và có `LocationID`, `Borough`, `Zone`.
- [x] Full rehash khớp manifest.

Final-test files 01–05/2026 mới chỉ được tải và kiểm tra tính toàn vẹn. Không có EDA, feature selection hoặc model fitting nào sử dụng giai đoạn này.
