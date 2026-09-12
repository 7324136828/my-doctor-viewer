import argparse
import json
import os
import re
from ollama import chat

OLLAMA_MODEL = "MedGemma1.5:latest"
OLLAMA_PROMPT = """"
You are an expert clinical informatics AI. Analyze the provided medical records (including lab panels, pathology reports, and consultation notes) for patient Yuzhe Ni. Synthesize these records into a high-level, structured clinical summary using the suggested JSON schema provided below. 

{
  "name_of_patient": "",
  "doctor_name" : "",
  "date_of_visit" : "", 
  "properties": {
    "patient_demographics": {...},
    "clinical_background": { ... },
    "significant_test_results": {...},
    "visit_notes" : {...},
    "care_plans_and_next_steps": {...}
  }
}

"""
INPUT_FOLDER = "input"
CHUNKS_FOLDER = "chunks"
OUTPUT_FOLDER = "output"
MANIFEST_FILE = "manifest.json"
CHECKPOINT_FILE = "checkpoint.json"
MAX_CHARS = 24000       # ~6 000 tokens; leaves headroom in an 8 192-token context
OLLAMA_NUM_CTX = int(8192 * 3)   # fits in 8 GiB VRAM with MedGemma1.5 Q4_K_M


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def split_text(text, max_chars=MAX_CHARS):
    """Split text into chunks at line boundaries, respecting max_chars."""
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    chunks = []
    current = ""
    for line in lines:
        if current and len(current) + len(line) + 1 > max_chars:
            chunks.append(current)
            current = line
        else:
            current = (current + "\n" + line).strip() if current else line
    if current:
        chunks.append(current)
    return chunks


def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"processed": [], "failed": []}


def save_checkpoint(checkpoint):
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, indent=2)


# ---------------------------------------------------------------------------
# Step 1 – preprocess
# ---------------------------------------------------------------------------

def preprocess():
    """Split input files into chunks and write a manifest."""
    os.makedirs(CHUNKS_FOLDER, exist_ok=True)

    input_files = sorted(
        f
        for f in os.listdir(INPUT_FOLDER)
        if f.endswith(".txt") and os.path.isfile(os.path.join(INPUT_FOLDER, f))
    )
    print(f"Found {len(input_files)} text file(s) in '{INPUT_FOLDER}'")

    manifest = []

    for file_idx, filename in enumerate(input_files):
        filepath = os.path.join(INPUT_FOLDER, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()

        basename = os.path.splitext(filename)[0]

        if len(text) > MAX_CHARS:
            chunks = split_text(text, MAX_CHARS)
            print(
                f"  [{file_idx + 1}/{len(input_files)}] {filename}: "
                f"{len(text)} chars -> {len(chunks)} chunks"
            )
        else:
            chunks = [text]
            print(
                f"  [{file_idx + 1}/{len(input_files)}] {filename}: "
                f"{len(text)} chars (single chunk)"
            )

        is_multi = len(chunks) > 1
        for chunk_idx, chunk in enumerate(chunks, 1):
            chunk_filename = (
                f"{basename}_{chunk_idx}.txt" if is_multi else f"{basename}.txt"
            )
            chunk_path = os.path.join(CHUNKS_FOLDER, chunk_filename)
            with open(chunk_path, "w", encoding="utf-8") as f:
                f.write(chunk)

            manifest.append({
                "source_file": filename,
                "chunk_file": chunk_filename,
                "chunk_index": chunk_idx if is_multi else None,
                "total_chunks": len(chunks) if is_multi else None,
            })

    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\nPreprocessing done. {len(manifest)} chunk(s) written to '{CHUNKS_FOLDER}/'")
    print(f"Manifest saved to '{MANIFEST_FILE}'")


# ---------------------------------------------------------------------------
# Step 2 – process
# ---------------------------------------------------------------------------

def call_medgemma(text):
    response = chat(
        model=OLLAMA_MODEL,
        messages=[
            {
                "role": "user",
                "content": f"{OLLAMA_PROMPT}\n\n{text}",
            }
        ],
        options={"num_ctx": OLLAMA_NUM_CTX},
    )
    return response.message.content


def process():
    """Call MedGemma on every chunk listed in the manifest and save results."""
    if not os.path.exists(MANIFEST_FILE):
        print(f"Manifest '{MANIFEST_FILE}' not found. Run 'preprocess' first.")
        return

    with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    checkpoint = load_checkpoint()
    processed_set = set(checkpoint["processed"])
    failed_set = set(checkpoint["failed"])

    total = len(manifest)
    total_processed = 0
    total_failed = 0

    for idx, entry in enumerate(manifest):
        chunk_file = entry["chunk_file"]
        chunk_path = os.path.join(CHUNKS_FOLDER, chunk_file)
        output_path = os.path.join(OUTPUT_FOLDER, chunk_file)

        if chunk_file in processed_set:
            print(f"[{idx + 1}/{total}] Skipping already processed: {chunk_file}")
            continue

        with open(chunk_path, "r", encoding="utf-8") as f:
            chunk_text = f.read()

        print(f"[{idx + 1}/{total}] Processing {chunk_file} ...")
        try:
            result = call_medgemma(chunk_text)

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(result)

            checkpoint["processed"].append(chunk_file)
            processed_set.add(chunk_file)
            if chunk_file in failed_set:
                checkpoint["failed"].remove(chunk_file)
                failed_set.discard(chunk_file)

            total_processed += 1
            print(f"  -> Saved to {output_path}")

        except Exception as e:
            print(f"  Error processing {chunk_file}: {e}")
            if chunk_file not in failed_set:
                checkpoint["failed"].append(chunk_file)
                failed_set.add(chunk_file)
            total_failed += 1

        save_checkpoint(checkpoint)

    print(f"\nDone. {total_processed} chunk(s) processed, {total_failed} failed.")
    print(f"Results saved to '{OUTPUT_FOLDER}/'")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MedGemma medical record processor")
    parser.add_argument(
        "step",
        choices=["preprocess", "process"],
        help="'preprocess' splits input files into chunks; 'process' calls MedGemma on them",
    )
    args = parser.parse_args()

    if args.step == "preprocess":
        preprocess()
    else:
        process()
