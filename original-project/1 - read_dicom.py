import json
import os
import io
import base64
import pydicom
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image
from ollama import chat

PAT_DIR = os.path.join(os.path.dirname(__file__), "PAT00000")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
CHECKPOINT_FILE = os.path.join(os.path.dirname(__file__), "checkpoint.json")

def collect_dicom_files(root_dir):
    """Walk the PAT directory tree and return all DICOM file paths."""
    dicom_files = []
    for dirpath, _, filenames in os.walk(root_dir):
        for fname in sorted(filenames):
            fpath = os.path.join(dirpath, fname)
            dicom_files.append(fpath)
    return dicom_files

def read_dicom_file(filepath):
    """Read a single DICOM file and return the dataset."""
    return pydicom.dcmread(filepath)

def print_dicom_info(ds, filepath):
    """Print key metadata from a DICOM dataset."""
    print(f"\nFile : {filepath}")
    for tag in ("PatientID", "StudyInstanceUID", "SeriesInstanceUID",
                "SOPInstanceUID", "Modality", "Rows", "Columns",
                "SliceLocation", "InstanceNumber"):
        value = getattr(ds, tag, "N/A")
        print(f"  {tag}: {value}")

def show_image(ds, title=""):
    """Display the pixel array of a DICOM file with matplotlib."""
    try:
        pixel_array = ds.pixel_array
    except Exception as exc:
        print(f"  Cannot decode pixel data: {exc}")
        return
    plt.figure()
    plt.imshow(pixel_array, cmap="gray")
    plt.title(title)
    plt.axis("off")
    plt.tight_layout()
    plt.show()

def process_dicom_image(ds):
    """Normalise pixel data to 8-bit, resize, and return both the PIL Image and base64 string."""
    try:
        pixels = ds.pixel_array.astype(np.float32)
    except Exception as exc:
        print(f"  Cannot decode pixel data: {exc}")
        return None, None

    # Normalise to 0-255
    pmin, pmax = pixels.min(), pixels.max()
    if pmax > pmin:
        pixels = (pixels - pmin) / (pmax - pmin) * 255.0
    pixels = pixels.astype(np.uint8)

    img = Image.fromarray(pixels)
    img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    
    # Save image to an in-memory bytes buffer
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    
    # Encode buffer contents to base64
    img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
    
    return img, img_base64

OLLAMA_MODEL = "MedGemma1.5:latest"
OLLAMA_PROMPT = (
    """
    Act as an expert clinical radiologist. I will provide an image of an MRI scan. Please analyze the image and generate a structured, comprehensive radiology report, assuming normal, baseline findings for all visible structures.
    You must format your response exactly as follows, adhering strictly to these specific headings and approximate word counts. You must output in a json format like the following.
    {
        "Image Description (~200 words)":"",
        "Key Findings (~500 words)": "" ,
        "Potential Abnormalities (~300 words)":"" ,
        "Conclusion (~200 words)": "",
        "Identified Conditions (array of strings)": [ "condition1 (confidence level)", "condition2 (confidence level)", ... ]
    }
    """
)

def analyse_image(base64_img):
    """Send a base64 encoded image to ollama and return the model's response text."""
    response = chat(
        model=OLLAMA_MODEL,
        messages=[
            {
                "role": "user",
                "content": OLLAMA_PROMPT,
                "images": [base64_img],
            }
        ],
    )
    return response.message.content

def load_checkpoint(checkpoint_file):
    """Load the set of already-analysed relative paths from the checkpoint file."""
    if os.path.exists(checkpoint_file):
        with open(checkpoint_file, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_checkpoint(checkpoint_file, completed):
    """Persist the set of completed relative paths to the checkpoint file."""
    with open(checkpoint_file, "w", encoding="utf-8") as f:
        json.dump(sorted(completed), f, indent=2)

def export_all(root_dir, out_dir, checkpoint_file=CHECKPOINT_FILE):
    """Process images, save PNG and Base64 to disk, run ollama analysis, and save text results."""
    files = collect_dicom_files(root_dir)
    print(f"Found {len(files)} file(s) under {root_dir}")

    completed = load_checkpoint(checkpoint_file)
    print(f"Checkpoint: {len(completed)} file(s) already analysed, skipping them.")

    saved = 0
    os.makedirs(out_dir, exist_ok=True)
    
    for fpath in files:
        rel = os.path.relpath(fpath, root_dir)

        if rel in completed:
            print(f"  Skipping (already analysed): {rel}")
            continue

        try:
            ds = read_dicom_file(fpath)
            print_dicom_info(ds, fpath)
        except Exception as exc:
            print(f"  Could not read {fpath}: {exc}")
            continue

        # Extract PIL Image and Base64 string
        img, b64_image = process_dicom_image(ds)
        if not b64_image:
            print(f"  Skipped (no pixel data): {fpath}")
            continue

        # ---------------------------------------------------------
        # NEW: Save the PNG file
        # ---------------------------------------------------------
        base_filename = rel.replace(os.sep, "_")
        
        png_path = os.path.join(out_dir, base_filename + ".png")
        img.save(png_path)
        print(f"  Saved PNG -> {png_path}")
        
        # ---------------------------------------------------------

        print(f"  Analysing with {OLLAMA_MODEL} in memory...")
        try:
            analysis = analyse_image(png_path)
        except Exception as exc:
            print(f"  Ollama error: {exc}")
            continue

        # Save analysis as a .txt file
        txt_path = os.path.join(out_dir, base_filename + "_analysis.txt")
        
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"Source DICOM: {fpath}\n\n{analysis}\n")
            
        print(f"  Analysis -> {txt_path}")
        print(f"  {analysis[:200]}..." if len(analysis) > 200 else f"  {analysis}")
        saved += 1

        completed.add(rel)
        save_checkpoint(checkpoint_file, completed)

    print(f"\nAnalysed {saved}/{len(files)} image(s). Results in {out_dir}")

def main():
    export_all(PAT_DIR, OUTPUT_DIR)

if __name__ == "__main__":
    main()