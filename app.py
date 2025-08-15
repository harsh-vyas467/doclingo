import os
import tempfile
import json
import fitz  # PyMuPDF
from flask import Flask, render_template, request, send_file
from werkzeug.utils import secure_filename
import google.generativeai as genai

# ----------------------
# CONFIGURATION
# ----------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY not found in environment variables")

genai.configure(api_key=GEMINI_API_KEY)

app = Flask(__name__)

# ----------------------
# LOAD LANGUAGES FROM JSON FILE
# ----------------------
with open("languages.json", "r", encoding="utf-8") as f:
    LANGUAGES = json.load(f)

# ----------------------
# PROMPT TEMPLATES
# ----------------------
def prompt_json(text, target_language):
    return f"""
Please analyze the following document text.

Instructions:
1. Detect the original language automatically.
2. Translate all text into {target_language}.
3. Provide the result in this exact JSON format (valid JSON, no extra text outside the JSON):

{{
  "doc_type": "auto-detected document type (e.g., invoice, contract, letter, etc.)",
  "metadata": {{
    "detected_language": "ISO language code (e.g., 'ja', 'zh', 'ko', 'en')",
    "confidence": float_between_0_and_1
  }},
  "entities": {{
    // Extract all identifiable information as key-value pairs
    // Use descriptive keys written in {target_language}
  }},
  "full_translated_text": "Full translation of the document in {target_language}"
}}

4. Preserve numbers, dates, and currency formats exactly as in the original.
5. If any content is unreadable, replace it with "[unreadable]".
6. Include all readable information from the document without summarizing or omitting.

Document text:
{text}
"""

def prompt_translate(text, target_language):
    return f"""
You are a professional document translator.

Instructions:
1. Detect the original language automatically.
2. Translate the entire document into {target_language}.
3. Maintain the original document's formatting exactly:
   - Keep the same page structure, fonts, sizes, and spacing.
   - Preserve images, tables, charts, and any non-text elements.
   - Keep numbers, dates, and currency values exactly as in the original.
4. Only translate text; do not alter non-text elements.

Document text:
{text}
"""

# ----------------------
# GEMINI CALLS
# ----------------------
def call_gemini(prompt):
    model = genai.GenerativeModel("gemini-1.5-pro")
    response = model.generate_content(prompt)
    return response.text

# ----------------------
# PDF HELPERS
# ----------------------
def extract_text_with_positions(pdf_path):
    doc = fitz.open(pdf_path)
    pages_data = []
    for page_num, page in enumerate(doc):
        blocks = page.get_text("blocks")
        page_items = []
        for b in blocks:
            x0, y0, x1, y1, text, *_ = b
            if text.strip():
                page_items.append({"bbox": (x0, y0, x1, y1), "text": text})
        pages_data.append(page_items)
    return pages_data

def rebuild_pdf_with_translation(pdf_path, translated_texts, output_path):
    doc = fitz.open(pdf_path)
    for page_num, page in enumerate(doc):
        page_items = translated_texts[page_num]
        for item in page_items:
            bbox = item["bbox"]
            page.add_redact_annot(bbox, fill=(1, 1, 1))
        page.apply_redactions()
        for item in page_items:
            bbox = item["bbox"]
            text = item["text"]
            page.insert_text(
                (bbox[0], bbox[1]),
                text,
                fontname="helv",
                fontsize=10,
                color=(0, 0, 0)
            )
    doc.save(output_path)

# ----------------------
# ROUTES
# ----------------------
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        file = request.files["file"]
        target_language = request.form.get("target_language", "en")
        user_choice = request.form.get("mode")

        filename = secure_filename(file.filename)
        with tempfile.TemporaryDirectory() as tmpdir:
            input_pdf_path = os.path.join(tmpdir, filename)
            file.save(input_pdf_path)

            text = ""
            with fitz.open(input_pdf_path) as doc:
                for page in doc:
                    text += page.get_text()

            if user_choice == "json":
                gemini_output = call_gemini(prompt_json(text, target_language))
                json_data = json.loads(gemini_output)
                json_path = os.path.join(tmpdir, "output.json")
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(json_data, f, ensure_ascii=False, indent=2)
                return send_file(json_path, as_attachment=True)

            elif user_choice == "pdf":
                pages_data = extract_text_with_positions(input_pdf_path)
                translated_pages = []
                for page_items in pages_data:
                    page_text = "\n".join([item["text"] for item in page_items])
                    translated_text = call_gemini(prompt_translate(page_text, target_language))
                    split_translations = translated_text.split("\n")
                    translated_items = []
                    for i, item in enumerate(page_items):
                        translated_items.append({
                            "bbox": item["bbox"],
                            "text": split_translations[i] if i < len(split_translations) else item["text"]
                        })
                    translated_pages.append(translated_items)

                output_pdf_path = os.path.join(tmpdir, "translated.pdf")
                rebuild_pdf_with_translation(input_pdf_path, translated_pages, output_pdf_path)
                return send_file(output_pdf_path, as_attachment=True)

            elif user_choice == "both":
                gemini_output = call_gemini(prompt_json(text, target_language))
                json_data = json.loads(gemini_output)
                json_path = os.path.join(tmpdir, "output.json")
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(json_data, f, ensure_ascii=False, indent=2)

                pages_data = extract_text_with_positions(input_pdf_path)
                translated_pages = []
                for page_items in pages_data:
                    page_text = "\n".join([item["text"] for item in page_items])
                    translated_text = call_gemini(prompt_translate(page_text, target_language))
                    split_translations = translated_text.split("\n")
                    translated_items = []
                    for i, item in enumerate(page_items):
                        translated_items.append({
                            "bbox": item["bbox"],
                            "text": split_translations[i] if i < len(split_translations) else item["text"]
                        })
                    translated_pages.append(translated_items)

                output_pdf_path = os.path.join(tmpdir, "translated.pdf")
                rebuild_pdf_with_translation(input_pdf_path, translated_pages, output_pdf_path)

                import zipfile
                zip_path = os.path.join(tmpdir, "result.zip")
                with zipfile.ZipFile(zip_path, "w") as zipf:
                    zipf.write(json_path, "output.json")
                    zipf.write(output_pdf_path, "translated.pdf")
                return send_file(zip_path, as_attachment=True)

    return render_template("index.html", languages=LANGUAGES)

# ----------------------
# MAIN
# ----------------------
if __name__ == "__main__":
    app.run(debug=True)
