# NYC TLC Yellow Taxi Predictive Analysis

Đồ án dự báo số lượt đón NYC Yellow Taxi theo `pickup zone × block 4 giờ`, sử dụng dữ liệu công khai từ 01/2024 đến 05/2026. Quy trình đi từ data understanding, data-quality audit và EDA đến rolling temporal validation, frozen final test và error analysis.

## Kết quả chính

- Development: 01/2024–12/2025, 89.642.441 clean pickups, 1.144.746 panel rows.
- Final test: 01/2026–05/2026, 18.969.497 clean pickups, 236.466 panel rows.
- Formal selected method: `baseline_previous_week`.
- Final performance: MAE **16,22**, RMSE **55,74**, WAPE **20,22%**, R² **0,935**.
- Ridge có empirical final MAE thấp nhất (**14,17**) nhưng không được dùng để chọn lại phương pháp sau khi mở test.
- Toàn bộ final evaluation vượt qua **29/29 independent checks**.

Báo cáo đầy đủ: [FINAL_REPORT.md](FINAL_REPORT.md).  
Notebook trình bày: [notebooks/NYC_TLC_predictive_analysis.ipynb](notebooks/NYC_TLC_predictive_analysis.ipynb).

## Cấu trúc dự án

```text
nyc_tlc_project/
├── config/                 # Cấu hình đã khóa cho từng bước
├── data/
│   ├── raw/                # 29 monthly Parquet files + zone lookup
│   └── processed/          # Development/final panels và modeling frames
├── notebooks/              # Notebook trình bày có thể execute
├── outputs/                # CSV, JSON, predictions và figures
├── scripts/                # Builders, runners, verifiers và entry point
├── PROJECT_BRIEF.md        # Scope, target, validation và selection rule
├── CLEANING_LOG.md         # Target-specific cleaning rules
├── DATA_DICTIONARY.md      # Data dictionary rút gọn
├── FINAL_REPORT.md         # Báo cáo cuối
└── PROGRESS.md             # Checklist 10 bước
```

Raw data và các output lớn không nên đưa vào source control. Downloader và manifest cho phép dựng lại dữ liệu từ nguồn chính thức.

## Yêu cầu hệ thống

- Windows PowerShell 5.1+ hoặc PowerShell 7+;
- Python 3.11–3.13;
- đủ dung lượng cho 29 monthly Parquet files;
- kết nối mạng chỉ cần khi tải raw data/cài package.

Các lệnh bên dưới chạy từ workspace root — thư mục chứa `.venv`, `requirements.txt` và `nyc_tlc_project/`.

## Cài đặt

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Để mở và execute notebook:

```powershell
.\.venv\Scripts\python.exe -m pip install -r nyc_tlc_project\requirements-notebook.txt
```

Muốn tái tạo đúng môi trường đã dùng để xác minh bước 9, thay lệnh trên bằng:

```powershell
.\.venv\Scripts\python.exe -m pip install -r nyc_tlc_project\requirements-lock.txt
```

## Cách chạy khuyến nghị

Entry point nhận năm stage: `download`, `development`, `final`, `verify`, `all`.

### 1. Chỉ kiểm tra các artifact hiện có

```powershell
powershell -ExecutionPolicy Bypass -File nyc_tlc_project\scripts\run_pipeline.ps1 -Stage verify
```

Thêm `-RehashRaw` nếu muốn đọc lại toàn bộ raw files và xác minh SHA-256:

```powershell
powershell -ExecutionPolicy Bypass -File nyc_tlc_project\scripts\run_pipeline.ps1 -Stage verify -RehashRaw
```

### 2. Tải dữ liệu

```powershell
powershell -ExecutionPolicy Bypass -File nyc_tlc_project\scripts\run_pipeline.ps1 -Stage download -RehashRaw
```

Downloader tái sử dụng file hợp lệ, dùng `.part` cho download dang dở, kiểm tra Parquet magic bytes và tạo manifest SHA-256.

### 3. Rebuild development pipeline

```powershell
powershell -ExecutionPolicy Bypass -File nyc_tlc_project\scripts\run_pipeline.ps1 -Stage development
```

Stage này lần lượt chạy audit, panel construction, EDA, modeling-frame build, rolling models và verifier tương ứng. Chỉ đọc development period 01/2024–12/2025.

### 4. Rebuild frozen final evaluation

```powershell
powershell -ExecutionPolicy Bypass -File nyc_tlc_project\scripts\run_pipeline.ps1 -Stage final
```

Final stage kiểm tra checksum của locked modeling config, training config và model-selection result trước khi đọc 01/2026–05/2026. Final test không được dùng để chọn lại model.

### 5. Rebuild toàn bộ

```powershell
powershell -ExecutionPolicy Bypass -File nyc_tlc_project\scripts\run_pipeline.ps1 -Stage all -RehashRaw
```

Full run xử lý hơn 100 triệu raw rows và sẽ tốn nhiều thời gian/tài nguyên hơn verification-only run.

## Chạy từng script

```powershell
# Raw data
.\.venv\Scripts\python.exe nyc_tlc_project\scripts\download_data.py
.\.venv\Scripts\python.exe nyc_tlc_project\scripts\verify_download.py --rehash

# Development audit
.\.venv\Scripts\python.exe nyc_tlc_project\scripts\audit_data.py
.\.venv\Scripts\python.exe nyc_tlc_project\scripts\verify_audit.py

# Balanced panel
.\.venv\Scripts\python.exe nyc_tlc_project\scripts\build_panel.py
.\.venv\Scripts\python.exe nyc_tlc_project\scripts\verify_panel.py

# EDA
.\.venv\Scripts\python.exe nyc_tlc_project\scripts\run_eda.py
.\.venv\Scripts\python.exe nyc_tlc_project\scripts\verify_eda.py

# Modeling specification và leakage audit
.\.venv\Scripts\python.exe nyc_tlc_project\scripts\build_modeling_frame.py
.\.venv\Scripts\python.exe nyc_tlc_project\scripts\verify_modeling_spec.py

# Rolling-fold models
.\.venv\Scripts\python.exe nyc_tlc_project\scripts\run_models.py
.\.venv\Scripts\python.exe nyc_tlc_project\scripts\verify_models.py

# Frozen final test
.\.venv\Scripts\python.exe nyc_tlc_project\scripts\build_final_test.py
.\.venv\Scripts\python.exe nyc_tlc_project\scripts\run_final_evaluation.py
.\.venv\Scripts\python.exe nyc_tlc_project\scripts\verify_final_evaluation.py

# Step 9 package
.\.venv\Scripts\python.exe nyc_tlc_project\scripts\verify_step9.py

# Step 10 presentation package
.\.venv\Scripts\python.exe nyc_tlc_project\scripts\verify_step10.py
```

## Chạy notebook

Notebook chỉ đọc verified CSV/JSON artifacts; nó không refit model hoặc thay đổi quyết định đã khóa.

Mở tương tác:

```powershell
.\.venv\Scripts\python.exe -m jupyter lab nyc_tlc_project\notebooks\NYC_TLC_predictive_analysis.ipynb
```

Execute không tương tác và ghi bản có outputs:

```powershell
.\.venv\Scripts\python.exe -m jupyter nbconvert `
  --to notebook `
  --execute nyc_tlc_project\notebooks\NYC_TLC_predictive_analysis.ipynb `
  --output NYC_TLC_predictive_analysis_executed.ipynb `
  --output-dir nyc_tlc_project\outputs\reproducibility `
  --ExecutePreprocessor.timeout=600
```

## Traceability

| Phần báo cáo | Output nguồn |
|---|---|
| Data quality | `outputs/audit/audit_summary.json`, `outputs/audit/*.csv` |
| Panel | `outputs/panel/panel_build_summary.json` |
| EDA | `outputs/eda/eda_summary.json`, `outputs/eda/*.csv` |
| Feature/leakage | `outputs/modeling_spec/*.json`, `validation_folds.csv` |
| Development models | `outputs/modeling/mean_fold_metrics.csv`, `fold_metrics.csv` |
| Final evaluation | `outputs/final_evaluation/final_metrics.csv`, `final_segment_metrics.csv` |
| Frozen decision | `outputs/final_evaluation/frozen_decision.json` |

Ba checksums bảo vệ frozen protocol:

- modeling config: `9a70f5987d653abef71ede2a20ce31a3801339b8c0dab8ab74a3096865130fe7`;
- training config: `3bda0eb733295ba242f196697dadf469d82f80c09ae29714b459351a52fcf181`;
- model selection: `10a2a7c394154e2313544c7785a5a386247d9c1295cd97241df82d36941a517f`.

## Giới hạn diễn giải

Pipeline dự báo realized pickup volume. Kết quả không chứng minh latent demand, thiếu/thừa tài xế, tác động nhân quả, lợi nhuận hoặc dispatch policy tối ưu. Backtest giả định stakeholder có pickup history kịp thời; public TLC files có độ trễ công bố nên không đủ cho near-real-time operation nếu đứng một mình.

## Deliverables thuyết trình

- `deliverables/NYC_TLC_Predictive_Analysis_Presentation.pptx`: deck 12 slide, có speaker notes nhúng trong từng slide.
- `deliverables/SPEAKER_NOTES.md`: kịch bản 12–14 phút, chuyển ý và checklist rehearsal.
- `deliverables/Q_AND_A.md`: 20 câu phản biện chính cùng câu trả lời ngắn, cộng câu hỏi về reproducibility.
- `outputs/presentation/step10_verification.json`: kiểm tra số slide, notes, native charts, nội dung và checksum của deck.
