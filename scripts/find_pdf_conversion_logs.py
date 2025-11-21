#!/usr/bin/env python3
"""
Quick guide to find PDF conversion logs in CloudWatch.
"""

print("=" * 80)
print("HOW TO CHECK IF GOOGLE DOCUMENT AI WAS USED")
print("=" * 80)

print("\nBased on the logs you showed, I don't see PDF conversion messages.")
print("This means either:")
print("  1. The conversion happened earlier (before the redlining logs)")
print("  2. The document was already DOCX (no conversion needed)")
print("  3. You need to scroll up to see earlier log entries")

print("\n" + "=" * 80)
print("WHAT TO LOOK FOR IN CLOUDWATCH LOGS")
print("=" * 80)

print("\n[GOOGLE DOCUMENT AI INDICATORS - Look for these messages:]")
print("  - PDF_TO_DOCX_START: Converting ... to DOCX using Google Document AI")
print("  - PDF_TO_DOCX: Using Google Document AI (project: ..., processor: ...)")
print("  - PDF_TO_DOCX: Sending PDF to Google Document AI for processing...")
print("  - PDF_TO_DOCX: Google Document AI processed X pages")
print("  - PDF_TO_DOCX_SUCCESS: Converted to ... using Google Document AI")

print("\n[PYMUPDF FALLBACK INDICATORS - If you see these, Google was NOT used:]")
print("  - PDF_TO_DOCX_FALLBACK: Using PyMuPDF conversion")
print("  - PDF_TO_DOCX: Google Document AI not available")
print("  - PDF_TO_DOCX_GOOGLE_ERROR")
print("  - PDF_TO_DOCX_GOOGLE_FAILED")

print("\n" + "=" * 80)
print("HOW TO FIND THE CONVERSION LOGS")
print("=" * 80)

print("\n1. In CloudWatch Logs, scroll UP to earlier entries")
print("2. Look for messages BEFORE the redlining starts")
print("3. Search for: 'PDF_TO_DOCX' or 'PROCESSING_PDF'")
print("4. The conversion happens right after document upload")
print("5. Look for timestamps around when the job started")

print("\n" + "=" * 80)
print("BASED ON YOUR LOGS")
print("=" * 80)

print("\nYour logs show:")
print("  - Job ID: b090cbd6-767b-4564-8c7d-dc2e16cc27af")
print("  - Processing started around: 2025-11-10T21:59:55")
print("  - Document has 955 paragraphs (suggests it was converted from PDF)")
print("  - 190 list items detected (good formatting preservation)")

print("\nTo find conversion logs:")
print("  1. Scroll up in the log stream")
print("  2. Look for entries BEFORE 21:59:55")
print("  3. Search for 'PDF_TO_DOCX' in the filter bar")
print("  4. Or search for 'PROCESSING_PDF'")

print("\n" + "=" * 80)
print("QUICK CHECK")
print("=" * 80)

print("\nIn the CloudWatch filter bar, type:")
print("  PDF_TO_DOCX")
print("\nThis will show only PDF conversion related messages.")
print("\nIf you see 'Google Document AI' → Google was used")
print("If you see 'FALLBACK' or 'PyMuPDF' → PyMuPDF was used")
print("If you see nothing → Document was already DOCX or logs are missing")

