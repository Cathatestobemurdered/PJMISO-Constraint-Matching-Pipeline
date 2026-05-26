# Power Constraint Entity Resolution Pipeline

This repository contains my solution for **Assignment 1: Constraint Mapping Across Data Sources** for the Ecesis Investments 2026 Summer Power Systems Modeling Internship assignment.

The goal is to match PJMISO constraints across three data sources: **Market**, **Dayzer**, and **Panorama/Pano**. Since each source uses different naming conventions, the project treats the task as a record-matching problem. The pipeline standardizes the three files, cleans facility and contingency names, applies fuzzy matching, and exports a candidate mapping table with confidence scores and review flags.

## Project Overview

In power market data, the same physical constraint may appear under different names across vendors. A constraint is mainly defined by two parts:

- **facility**: the monitored equipment or transmission element
- **contingency**: the outage or system condition under which the facility is monitored

This project uses **Market** as the anchor source and finds the closest matching records from **Dayzer** and **Pano** for each Market constraint.

## Repository Files

| File | Description |
|---|---|
| `pipeline.py` | Main Python script for loading data, cleaning text, matching constraints, and exporting results |
| `Assignment1_Constraint_Mapping.ipynb` | Jupyter Notebook showing the workflow, sample checks, and output review |
| `constraint_mapping_results.csv` | Final output file with matched Market, Dayzer, and Pano constraints |
| `summary_report.md` | Short report explaining the method, results, limitations, and conclusions |
| `Market_PJMISO_constraint_list.csv` | Raw Market constraint list |
| `Dayzer_PJMISO_constraint_list.csv` | Raw Dayzer constraint list |
| `Pano_PJMISO_constraint_list.csv` | Raw Panorama/Pano constraint list |
| `requirements.txt` | Python package requirements |

## Method

The pipeline follows these steps:

1. **Load the raw files**  
   Read Market, Dayzer, and Pano constraint lists into pandas DataFrames.

2. **Standardize the schemas**  
   Convert each source into a common structure with fields such as `source`, `id`, `raw_name`, `facility_raw`, and `contingency_raw`.

3. **Clean and normalize text**  
   Standardize capitalization, voltage notation, punctuation, and generic words so that names from different sources are easier to compare.

4. **Match constraints across sources**  
   For each Market constraint, the pipeline finds the best Dayzer and Pano candidate using fuzzy string matching. Facility and contingency similarities are scored separately and then combined.

5. **Export results**  
   The final CSV includes the required mapping columns plus extra fields for confidence scoring and manual review.

## Matching Logic

The final score combines facility and contingency similarity:

```text
final_score = 0.65 * facility_score + 0.35 * contingency_score
```

The facility receives more weight because the monitored facility is usually the more stable part of a constraint definition. Contingency names can be less consistent across data sources.

The output is a **candidate mapping table**, not a guaranteed ground-truth table. Fuzzy matching always returns a nearest candidate, so the pipeline includes confidence labels and review statuses to avoid over-claiming weak matches.

## Output Columns

The assignment requires these three main columns:

- `market_constraint`
- `dayzer_constraint`
- `pano_constraint`

Additional audit columns are included:

- `dayzer_score`
- `dayzer_confidence`
- `dayzer_status`
- `dayzer_id`
- `pano_score`
- `pano_confidence`
- `pano_status`
- `pano_id`
- `pano_latest`
- `pano_stale`
- `overall_score`
- `overall_confidence`
- `market_id`

## How to Run

Install the required packages:

```bash
python3 -m pip install -r requirements.txt
```

Run the pipeline:

```bash
python3 pipeline.py
```

The script writes:

```text
constraint_mapping_results.csv
```

You can also pass file paths manually:

```bash
python3 pipeline.py \
  --market Market_PJMISO_constraint_list.csv \
  --dayzer Dayzer_PJMISO_constraint_list.csv \
  --pano Pano_PJMISO_constraint_list.csv \
  --output constraint_mapping_results.csv
```
