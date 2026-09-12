## Genesis 

based on the skills, can you create based on the original-project so that it will have everything in the root? Make sure you follow the guidance of productionization. Also make sure we can have a UI in reactjs such that the user can paste/select the pdf files to be converted and it will create a temp folder under system's temp folder and it will pipe through the script and allow the user to retrieve from the output file? Create a backend with the python code based on original-project. Make sure you have setup.bat/setup.sh file that will dispatch setup.py such that it will setup virtual environment and run.bat/run.sh such that it will dispatch the frontend and backend? 

## Features 1

Can you update the frontend and backend that will accommedate the following features? You can have multiple screens to support them etc.

This is a collection of scripts for organizing and analyzing personal medical-record data—not a single end-user application. Its main features are:

- Medical-record ingestion: splits `.txt` records into manageable chunks and asks a local MedGemma/Ollama model to produce structured clinical-summary JSON. It records a manifest and checkpoint so work can resume. [process_medgemma.py](C:\Users\zachn\Documents\GitHub\my-doctor-viewer\skill\original-project\process_medgemma.py)
- DICOM/MRI processing: walks a patient DICOM folder, reads image metadata and pixels, converts images to enlarged PNGs, sends them for model analysis, saves reports, and skips previously completed files. [1 - read_dicom.py](C:\Users\zachn\Documents\GitHub\my-doctor-viewer\skill\original-project\1%20-%20read_dicom.py)
- JSON extraction and repair: extracts model-generated JSON code blocks, detects invalid JSON, audits lab-value omissions, and includes targeted repair scripts for known malformed files. [extract_json.py](C:\Users\zachn\Documents\GitHub\my-doctor-viewer\skill\original-project\extract_json.py)
- Record classification: deterministically categorizes documents (labs, notes, pathology, genetic screens, etc.), identifies institution/MRN/date/provider, retains evidence for each classification, marks uncertain records for review, and verifies dataset invariants. [categorize_json.py](C:\Users\zachn\Documents\GitHub\my-doctor-viewer\skill\original-project\categorize_json.py)
- Lab-trend reporting: extracts values from varied summary structures, standardizes test names, parses dates/units/reference ranges, flags suspect or out-of-range values, and produces CSV, PDF, and PNG trend reports. [plot_test_trends.py](C:\Users\zachn\Documents\GitHub\my-doctor-viewer\skill\original-project\plot_test_trends.py)
- Condition roll-up: aggregates non-normal conditions reported across image analyses and lists their source files. [summarize_conditions.py](C:\Users\zachn\Documents\GitHub\my-doctor-viewer\skill\original-project\summarize_conditions.py)
- Genetics: downloads gene lists from NCBI; searches local VCFs for variants in a requested gene; and annotates found variants with ClinVar evidence. [get_all_genes.py](C:\Users\zachn\Documents\GitHub\my-doctor-viewer\skill\original-project\get_all_genes.py), [query_gene_indels.py](C:\Users\zachn\Documents\GitHub\my-doctor-viewer\skill\original-project\query_gene_indels.py)
- Research feeds: downloads RSS/Atom feeds and retrieves complete ClinicalTrials.gov study histories from equivalent search criteria. [download_rss.py](C:\Users\zachn\Documents\GitHub\my-doctor-viewer\skill\original-project\download_rss.py), [download_all_studies.py](C:\Users\zachn\Documents\GitHub\my-doctor-viewer\skill\original-project\download_all_studies.py)

Important boundary: it processes highly sensitive health/genetic data and uses model-generated summaries. The outputs should support review, not diagnosis or treatment decisions.

## INVEST-ready user stories

| # | User story | Testable acceptance criteria | INVEST fit |
|---|---|---|---|
| 1 | As a patient, I want my text medical records converted into structured summaries so I can review them consistently. | Given valid text files, the system creates one manifest entry per chunk and a JSON-style summary output per successfully processed chunk. | Independent pipeline step; negotiable schema; valuable; bounded per chunk; testable from files. |
| 2 | As a patient, I want interrupted processing to resume without repeating completed records. | Given a checkpoint with completed chunks, rerunning processing skips those chunks and processes only remaining ones. | Independent resilience feature; small; estimable; objectively testable. |
| 3 | As a records reviewer, I want model output extracted and validated as JSON so downstream tools receive usable data. | Valid fenced JSON is saved as parseable `.json`; missing or invalid JSON is reported and does not silently appear valid. | Separate from summarization; clear output contract; small and testable. |
| 4 | As a records reviewer, I want each document classified with supporting evidence so I can filter records and audit the categorization. | Every JSON record receives a primary category, confidence, evidence, provenance, and a `needs_review` status when rules are insufficient. | Independent metadata capability; rules can be negotiated; meaningful and testable. |
| 5 | As a patient, I want ambiguous records surfaced for manual review so uncertain automated conclusions are not treated as facts. | Low-confidence, unresolved, or filename-only classifications are labeled `needs_review` with at least one reason. | Small safety story; independently releasable; binary acceptance checks. |
| 6 | As a patient, I want lab values plotted over time with reference ranges so I can spot trends to discuss with my clinician. | The system exports long-form CSV plus PDF/PNG charts; charts show dated values, a reference band when available, and out-of-range markers. | Independent reporting feature; bounded by analyte; readily demoable and testable. |
| 7 | As a radiology reviewer, I want DICOM files converted into reviewable images and associated draft reports so image studies are easier to organize. | For readable DICOMs with pixels, the system writes a PNG and a linked analysis file; unreadable files are logged without halting the batch. | Independent imaging workflow; scoped per DICOM; testable with fixtures. |
| 8 | As a patient exploring genetic data, I want variants in a selected gene identified and annotated with ClinVar evidence so I can prepare informed questions for a genetics professional. | Given a VCF and gene symbol, the system finds variants in the gene interval and exports variant, genotype, ClinVar significance, and evidence fields. | Separate genetics capability; bounded to one gene/VCF; testable with a fixture VCF. |
| 9 | As a health researcher, I want relevant RSS and ClinicalTrials.gov search results downloaded into structured files so I can review studies offline. | Given valid feed/search URLs, the system writes JSON containing feed metadata and studies, reports failures, and exits nonzero on incomplete downloads. | Independent ingestion feature; configurable inputs; straightforward to estimate and test. |
| 10 | As a patient, I want repeated non-normal conditions summarized across reports so I can see which findings recur. | Given merged analysis JSON, the system produces a condition-to-count-and-source-files summary while excluding entries labeled normal/no finding. | Small aggregation story; independently deployable; testable with sample input. |

The strongest next backlog item would be: “As a clinician reviewer, I want every AI-generated finding visibly labeled as unverified and linked to its source document/image.” That would make the current workflow safer and more auditable.


## Documentation 1
Can you provide a list of screenshot of the application and save them in the image folder and update README.md?