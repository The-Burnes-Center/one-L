"""
PDF processing utilities for conflict detection and annotation-based redlining.
Uses PyMuPDF for superior text extraction and annotation support.
"""

import logging
import io
from typing import Dict, Any, List, Optional, Tuple
import re

logger = logging.getLogger()
logger.setLevel(logging.INFO)

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    logger.warning("PyMuPDF not installed - PDF support disabled")
    fitz = None
    PYMUPDF_AVAILABLE = False


class PDFProcessor:
    """Handles PDF text extraction and annotation-based redlining using PyMuPDF."""
    
    def __init__(self):
        if not PYMUPDF_AVAILABLE:
            raise ImportError("PyMuPDF is required for PDF processing. Install with: pip install PyMuPDF")
    
    def extract_text(self, pdf_bytes: bytes) -> str:
        """
        Extract all text content from a PDF document.
        
        Args:
            pdf_bytes: PDF file content as bytes
            
        Returns:
            Extracted text content as a single string
        """
        try:
            pdf_file = io.BytesIO(pdf_bytes)
            doc = fitz.open(stream=pdf_file, filetype="pdf")
            
            text_content = []
            for page_num in range(len(doc)):
                try:
                    page = doc[page_num]
                    page_text = page.get_text()
                    if page_text.strip():
                        # Add page separator for better chunking
                        text_content.append(f"[Page {page_num + 1}]\n{page_text.strip()}")
                except Exception as page_error:
                    logger.warning(f"Error extracting text from page {page_num + 1}: {page_error}")
                    continue
            
            doc.close()
            
            full_text = '\n\n'.join(text_content)
            logger.info(f"PDF_TEXT_EXTRACTED: {len(doc)} pages, {len(full_text)} characters")
            
            return full_text
            
        except Exception as e:
            logger.error(f"Error extracting text from PDF: {str(e)}")
            raise Exception(f"Failed to extract PDF text: {str(e)}")
    
    def extract_text_with_positions(self, pdf_bytes: bytes) -> List[Dict[str, Any]]:
        """
        Extract text with character positions for later annotation matching.
        
        Args:
            pdf_bytes: PDF file content as bytes
            
        Returns:
            List of text segments with position information
        """
        try:
            pdf_file = io.BytesIO(pdf_bytes)
            doc = fitz.open(stream=pdf_file, filetype="pdf")
            
            text_segments = []
            for page_num in range(len(doc)):
                try:
                    page = doc[page_num]
                    page_text = page.get_text()
                    if page_text.strip():
                        text_segments.append({
                            'page_number': page_num + 1,
                            'text': page_text.strip(),
                            'char_start': sum(len(seg['text']) for seg in text_segments),
                            'char_end': sum(len(seg['text']) for seg in text_segments) + len(page_text.strip())
                        })
                except Exception as page_error:
                    logger.warning(f"Error extracting text from page {page_num + 1}: {page_error}")
                    continue
            
            doc.close()
            return text_segments
            
        except Exception as e:
            logger.error(f"Error extracting text with positions: {str(e)}")
            return []
    
    def find_text_in_pdf(self, pdf_bytes: bytes, search_text: str, fuzzy=False) -> List[Dict[str, Any]]:
        """
        Find occurrences of text in PDF and return their page positions.
        Uses PyMuPDF's native text search for accurate results.
        
        Args:
            pdf_bytes: PDF file content as bytes
            search_text: Text to search for
            fuzzy: If True, use fuzzy matching (currently uses exact match)
            
        Returns:
            List of matches with page and position info
        """
        try:
            pdf_file = io.BytesIO(pdf_bytes)
            doc = fitz.open(stream=pdf_file, filetype="pdf")
            
            matches = []
            
            # Normalize search text
            normalized_search = self._normalize_text(search_text)
            
            for page_num in range(len(doc)):
                try:
                    page = doc[page_num]
                    
                    # Use PyMuPDF's text search - returns TextPage with positions
                    text_instances = page.search_for(search_text, flags=fitz.TEXT_DEHYPHENATE)
                    
                    if text_instances:
                        # Found exact matches
                        for rect in text_instances:
                            matches.append({
                                'page_number': page_num + 1,
                                'position': rect,  # PyMuPDF rectangle object
                                'text': search_text,
                                'x': rect.x0,
                                'y': rect.y0
                            })
                    elif fuzzy:
                        # Try normalized search
                        page_text = page.get_text()
                        normalized_page = self._normalize_text(page_text)
                        
                        if normalized_search in normalized_page:
                            matches.append({
                                'page_number': page_num + 1,
                                'position': None,  # Fuzzy match, no exact position
                                'text': search_text,
                                'fuzzy_match': True
                            })
                        
                except Exception as page_error:
                    logger.warning(f"Error searching page {page_num + 1}: {page_error}")
                    continue
            
            doc.close()
            
            logger.info(f"PDF_SEARCH: Found {len(matches)} matches for text '{search_text[:50]}...'")
            return matches
            
        except Exception as e:
            logger.error(f"Error searching PDF: {str(e)}")
            return []
    
    def redline_pdf(self, pdf_bytes: bytes, conflicts: List[Dict[str, str]], 
                   position_mapping: Optional[Dict[str, List[Dict]]] = None) -> bytes:
        """
        Add annotations to PDF for each conflict found.
        Uses PyMuPDF's native annotation support for better results.
        
        Args:
            pdf_bytes: PDF file content as bytes
            conflicts: List of conflict items with text and comments
            position_mapping: Optional mapping of conflict text to page positions
            
        Returns:
            Redlined PDF as bytes
        """
        try:
            pdf_file = io.BytesIO(pdf_bytes)
            doc = fitz.open(stream=pdf_file, filetype="pdf")
            
            # Track annotations per page
            page_annotations = {}
            
            for conflict in conflicts:
                conflict_text = conflict.get('text', '').strip()
                comment = conflict.get('comment', '')
                clarification_id = conflict.get('clarification_id', 'Unknown')
                
                if not conflict_text:
                    continue
                
                # Find the conflict in the PDF
                if position_mapping and conflict_text in position_mapping:
                    matches = position_mapping[conflict_text]
                else:
                    matches = self.find_text_in_pdf(pdf_bytes, conflict_text, fuzzy=True)
                
                # Group annotations by page
                for match in matches:
                    page_num = match['page_number']
                    
                    if page_num not in page_annotations:
                        page_annotations[page_num] = []
                    
                    page_annotations[page_num].append({
                        'clarification_id': clarification_id,
                        'comment': comment,
                        'conflict_text': conflict_text[:100],
                        'position': match.get('position'),  # PyMuPDF rectangle if available
                        'x': match.get('x', 50),  # Default position
                        'y': match.get('y', 750)
                    })
            
            # Add annotations to pages
            for page_num, annotations in page_annotations.items():
                try:
                    page = doc[page_num - 1]  # Convert to 0-indexed
                    
                    # Combine comments for this page
                    combined_comment = '\n\n'.join([
                        f"[{item['clarification_id']}] {item['comment']}"
                        for item in annotations
                    ])
                    
                    # Create annotation
                    # Use position from first match or default to top-left
                    x = annotations[0].get('x', 50)
                    y = annotations[0].get('y', 750)
                    
                    # Create rectangular area for annotation
                    rect = fitz.Rect(x, y, x + 200, y + 100)
                    
                    # Add sticky note annotation
                    annot = page.add_sticky_note(rect.tl, icon="note", color=(1, 0, 0))  # Red sticky note
                    
                    # Set annotation content
                    annot.set_info(title="Legal-AI Conflict", content=combined_comment)
                    annot.update()
                    
                    logger.info(f"PDF_ANNOTATION_ADDED: Page {page_num} with {len(annotations)} conflicts")
                    
                except Exception as annot_error:
                    logger.warning(f"Could not add annotation to page {page_num}: {annot_error}")
                    continue
            
            # Save to bytes
            output = io.BytesIO()
            doc.save(output, garbage=4, deflate=True)
            doc.close()
            output.seek(0)
            
            annotated_pages = len(page_annotations)
            logger.info(f"PDF_REDLINE_COMPLETE: {annotated_pages} pages annotated, {len(conflicts)} conflicts processed")
            
            return output.getvalue()
            
        except Exception as e:
            logger.error(f"Error redlining PDF: {str(e)}")
            raise Exception(f"Failed to redline PDF: {str(e)}")
    
    def _normalize_text(self, text: str) -> str:
        """Normalize text for comparison by removing extra whitespace and punctuation."""
        if not text:
            return ""
        # Replace multiple spaces with single space
        normalized = re.sub(r'\s+', ' ', text)
        # Remove trailing punctuation
        normalized = re.sub(r'[^\w\s]$', '', normalized)
        return normalized.lower().strip()


def is_pdf_file(filename: str) -> bool:
    """Check if a file is a PDF based on extension."""
    return filename.lower().endswith('.pdf')


def get_pdf_processor() -> PDFProcessor:
    """Get a PDF processor instance."""
    return PDFProcessor()
