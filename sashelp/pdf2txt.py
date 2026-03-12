"""
PDF → TXT 변환 (OCR 방식)
사용법: python sashelp/pdf2txt.py sashelp/source/sqlproc.pdf
"""

import sys
import os
from pathlib import Path
from pdf2image import convert_from_path
import pytesseract


def pdf_to_txt(pdf_path: str, output_path: str = None, dpi: int = 200):
    pdf_path = Path(pdf_path)
    if output_path is None:
        output_path = pdf_path.with_suffix(".txt")
    else:
        output_path = Path(output_path)

    print(f"PDF: {pdf_path}")
    print(f"출력: {output_path}")

    print("PDF → 이미지 변환 중...")
    pages = convert_from_path(str(pdf_path), dpi=dpi)
    print(f"총 {len(pages)} 페이지")

    with open(output_path, "w", encoding="utf-8") as f:
        for i, page in enumerate(pages, 1):
            print(f"  OCR 처리 중... {i}/{len(pages)}", end="\r")
            text = pytesseract.image_to_string(page, lang="eng")
            f.write(f"\n{'='*60}\n")
            f.write(f"PAGE {i}\n")
            f.write(f"{'='*60}\n")
            f.write(text)

    print(f"\n완료: {output_path} ({output_path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python pdf2txt.py <pdf파일> [출력파일]")
        sys.exit(1)

    pdf = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None
    pdf_to_txt(pdf, out)
