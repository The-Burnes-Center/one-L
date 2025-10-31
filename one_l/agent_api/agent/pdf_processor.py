"""
PDF processing utilities for conflict detection and annotation-based redlining.
Uses PyMuPDF for superior text extraction and annotation support.
"""

import logging
import io
import os
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
            
            # Normalize search text (robust normalization)
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
                            # Try to find the first word of the search text to get approximate position
                            words = search_text.split()
                            if words:
                                first_word = words[0]
                                word_instances = page.search_for(first_word, flags=fitz.TEXT_DEHYPHENATE)
                                
                                if word_instances:
                                    # Use the first occurrence of the first word
                                    rect = word_instances[0]
                                    matches.append({
                                        'page_number': page_num + 1,
                                        'position': rect,
                                        'text': search_text,
                                        'x': rect.x0,
                                        'y': rect.y0,
                                        'fuzzy_match': True
                                    })
                                else:
                                    # Fallback: try any word in the search text
                                    for word in words[:5]:  # Try first 5 words
                                        word_instances = page.search_for(word, flags=fitz.TEXT_DEHYPHENATE)
                                        if word_instances:
                                            rect = word_instances[0]
                                            matches.append({
                                                'page_number': page_num + 1,
                                                'position': rect,
                                                'text': search_text,
                                                'x': rect.x0,
                                                'y': rect.y0,
                                                'fuzzy_match': True
                                            })
                                            break
                                    else:
                                        # Chunked/n-gram search: try distinctive clauses or sliding windows
                                        chunk_rect = None
                                        for chunk in self._generate_search_chunks(search_text):
                                            if len(chunk) < 15:
                                                continue
                                            try:
                                                chunk_hits = page.search_for(chunk, flags=fitz.TEXT_DEHYPHENATE)
                                            except Exception:
                                                chunk_hits = []
                                            if chunk_hits:
                                                chunk_rect = chunk_hits[0]
                                                matches.append({
                                                    'page_number': page_num + 1,
                                                    'position': chunk_rect,
                                                    'text': search_text,
                                                    'x': chunk_rect.x0,
                                                    'y': chunk_rect.y0,
                                                    'fuzzy_match': True,
                                                    'chunk_anchor': chunk
                                                })
                                                break
                                        if chunk_rect:
                                            continue
                                        # Keyword sweep fallback: try scanning with key compliance/security words
                                        keywords = [
                                            'security','firewall','siem','incident','sla','uptime','maintenance',
                                            'availability','response','resolution','backup','recovery','encryption',
                                            'compliance','policy','terms','conditions','vendor','customer','support'
                                        ]
                                        found_kw = None
                                        for kw in keywords:
                                            kw_instances = page.search_for(kw, flags=fitz.TEXT_DEHYPHENATE)
                                            if kw_instances:
                                                found_kw = kw_instances[0]
                                                break
                                        if found_kw:
                                            matches.append({
                                                'page_number': page_num + 1,
                                                'position': found_kw,
                                                'text': search_text,
                                                'x': found_kw.x0,
                                                'y': found_kw.y0,
                                                'fuzzy_match': True,
                                                'keyword_fallback': True
                                            })
                                        else:
                                            # Last resort: add annotation at top-left of page
                                            matches.append({
                                                'page_number': page_num + 1,
                                                'position': None,
                                                'text': search_text,
                                                'x': 50,
                                                'y': 50,
                                                'fuzzy_match': True
                                            })
                        
                except Exception as page_error:
                    logger.warning(f"Error searching page {page_num + 1}: {page_error}")
                    continue
            
            # If still no matches and OCR is enabled, try Textract across full PDF
            if not matches and os.getenv('ENABLE_TEXTRACT_OCR', '0') in ['1', 'true', 'True']:
                try:
                    ocr_matches = self._textract_find(pdf_bytes, search_text)
                    if ocr_matches:
                        matches.extend(ocr_matches)
                except Exception as tex_err:
                    logger.warning(f"TEXTRACT_FALLBACK_FAILED: {tex_err}")

            doc.close()
            
            logger.info(f"PDF_SEARCH: Found {len(matches)} matches for text '{search_text[:50]}...'")
            return matches
            
        except Exception as e:
            logger.error(f"Error searching PDF: {str(e)}")
            return []

    # Textract OCR fallback: detect text and approximate coordinates
    def _textract_find(self, pdf_bytes: bytes, search_text: str) -> List[Dict[str, Any]]:
        try:
            import boto3  # Available in Lambda runtime
        except Exception as e:
            logger.warning(f"Textract not available: {e}")
            return []

        try:
            client = boto3.client('textract')
            resp = client.detect_document_text(Document={'Bytes': pdf_bytes})
        except Exception as e:
            logger.warning(f"Textract detect_document_text failed: {e}")
            return []

        # Build page-wise lines with geometry
        blocks = resp.get('Blocks', [])
        pages: Dict[int, Dict[str, Any]] = {}
        for b in blocks:
            if b.get('BlockType') == 'LINE':
                page_num = b.get('Page', 1)
                if page_num not in pages:
                    pages[page_num] = {'lines': []}
                pages[page_num]['lines'].append({
                    'text': b.get('Text', ''),
                    'bbox': b.get('Geometry', {}).get('BoundingBox')
                })

        normalized_search = self._normalize_text(search_text)
        matches: List[Dict[str, Any]] = []

        # Open PDF to convert relative Textract bounding boxes to absolute coordinates
        try:
            pdf_file = io.BytesIO(pdf_bytes)
            doc = fitz.open(stream=pdf_file, filetype='pdf')
        except Exception:
            doc = None

        for page_num, data in pages.items():
            lines = data.get('lines', [])
            # Concatenate page text for quick inclusion check
            page_text_concat = ' '.join(l['text'] for l in lines if l.get('text'))
            if normalized_search and normalized_search not in self._normalize_text(page_text_concat):
                # Not found at page level, skip to next page
                continue

            # Try to find a matching line and use its bbox
            chosen_bbox = None
            for ln in lines:
                ln_text_norm = self._normalize_text(ln.get('text', ''))
                if not ln_text_norm:
                    continue
                # Exact-in-normalized or partial overlap heuristic
                if (normalized_search in ln_text_norm) or (
                    len(normalized_search) > 30 and ln_text_norm and ln_text_norm in normalized_search
                ):
                    chosen_bbox = ln.get('bbox')
                    break

            # Convert bbox to absolute coordinates on the page using PyMuPDF page size
            rect = None
            x = 50
            y = 50
            if chosen_bbox and doc is not None and page_num - 1 < len(doc):
                try:
                    page = doc[page_num - 1]
                    width = page.rect.width
                    height = page.rect.height
                    left = chosen_bbox.get('Left', 0) * width
                    top = chosen_bbox.get('Top', 0) * height
                    w = chosen_bbox.get('Width', 0.2) * width
                    h = chosen_bbox.get('Height', 0.02) * height
                    rect = fitz.Rect(left, top, left + w, top + h)
                    x = left
                    y = top
                except Exception as map_err:
                    logger.warning(f"Textract bbox mapping failed: {map_err}")

            matches.append({
                'page_number': page_num,
                'position': rect,
                'text': search_text,
                'x': x,
                'y': y,
                'fuzzy_match': True,
                'ocr': True
            })

        try:
            if doc is not None:
                doc.close()
        except Exception:
            pass

        logger.info(f"TEXTRACT_MATCHES: {len(matches)} for text '{search_text[:50]}...'")
        return matches
    
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
            
            # Track annotations per page, grouped by conflict to avoid duplicate tags
            page_annotations = {}
            
            for conflict in conflicts:
                conflict_text = conflict.get('text', '').strip()
                comment = conflict.get('comment', '')
                clarification_id = conflict.get('clarification_id', 'Unknown')
                
                if not conflict_text:
                    continue
                
                # Find the conflict in the PDF - use position_mapping if available
                matches = []
                if position_mapping and conflict_text in position_mapping:
                    matches = position_mapping[conflict_text]
                    logger.info(f"PDF_ANNOTATION: Using cached matches for conflict {clarification_id}: {len(matches)} matches found")
                else:
                    logger.warning(f"PDF_ANNOTATION: No cached matches found for conflict {clarification_id}, searching again")
                    matches = self.find_text_in_pdf(pdf_bytes, conflict_text, fuzzy=True)
                
                # Group matches by page to handle multi-line conflicts correctly
                if matches:
                    # Group matches by page
                    matches_by_page = {}
                    for match in matches:
                        page_num = match['page_number']
                        if page_num not in matches_by_page:
                            matches_by_page[page_num] = []
                        matches_by_page[page_num].append(match)
                    
                    # For each page with matches, group closely-spaced matches together
                    # This prevents duplicate conflict tags for multi-line conflicts
                    # while still handling separate instances correctly
                    for page_num, page_matches in matches_by_page.items():
                        if page_num not in page_annotations:
                            page_annotations[page_num] = []
                        
                        # Sort matches by y position (top to bottom) to get the first line
                        sorted_matches = sorted(page_matches, key=lambda m: (
                            m.get('y', 0),
                            m.get('x', 0)
                        ))
                        
                        # Group matches that are close together vertically (within ~50 points)
                        # This handles multi-line conflicts while avoiding grouping unrelated instances
                        grouped_clusters = []
                        current_cluster = [sorted_matches[0]]
                        
                        for i in range(1, len(sorted_matches)):
                            prev_y = sorted_matches[i-1].get('y', 0)
                            curr_y = sorted_matches[i].get('y', 0)
                            # If matches are close vertically (within 50 points), they're part of same multi-line conflict
                            if curr_y - prev_y < 50:
                                current_cluster.append(sorted_matches[i])
                            else:
                                # Start a new cluster for separate instances
                                grouped_clusters.append(current_cluster)
                                current_cluster = [sorted_matches[i]]
                        
                        # Add the last cluster
                        if current_cluster:
                            grouped_clusters.append(current_cluster)
                        
                        # Create one annotation entry per cluster
                        for cluster in grouped_clusters:
                            # Use the first (topmost) match for positioning the conflict tag
                            first_match = cluster[0]
                            
                            # Collect all positions in this cluster for creating a combined highlight
                            all_positions = [m.get('position') for m in cluster if m.get('position')]
                            
                            page_annotations[page_num].append({
                                'clarification_id': clarification_id,
                                'comment': comment,
                                'conflict_text': conflict_text[:100],
                                'position': first_match.get('position'),  # Primary position for tag placement
                                'all_positions': all_positions,  # All positions for combined highlight
                                'x': first_match.get('x', 50),
                                'y': first_match.get('y', 750)
                            })
                            
                            if len(cluster) > 1:
                                logger.info(f"PDF_ANNOTATION: Added conflict {clarification_id} to page {page_num} with {len(cluster)} matches grouped (multi-line conflict)")
                            else:
                                logger.info(f"PDF_ANNOTATION: Added conflict {clarification_id} to page {page_num} with 1 match")
                else:
                    logger.warning(f"PDF_ANNOTATION: No matches found for conflict {clarification_id}: '{conflict_text[:50]}...'")
            
            # If sparse annotations, apply per-page keyword sweep to increase coverage
            if len(page_annotations) == 0 or sum(len(v) for v in page_annotations.values()) < 6:
                try:
                    extra = self._keyword_sweep_annotations(doc)
                    for page_num, ann_list in extra.items():
                        if page_num not in page_annotations:
                            page_annotations[page_num] = []
                        page_annotations[page_num].extend(ann_list)
                    logger.info(f"PDF_KEYWORD_SWEEP: Added {sum(len(v) for v in extra.values())} fallback annotations across {len(extra)} pages")
                except Exception as sweep_err:
                    logger.warning(f"PDF_KEYWORD_SWEEP_FAILED: {sweep_err}")

            # If sparse annotations, apply per-page keyword sweep to increase coverage
            if len(page_annotations) == 0 or sum(len(v) for v in page_annotations.values()) < 6:
                try:
                    extra = self._keyword_sweep_annotations(doc)
                    for page_num, ann_list in extra.items():
                        if page_num not in page_annotations:
                            page_annotations[page_num] = []
                        page_annotations[page_num].extend(ann_list)
                    logger.info(f"PDF_KEYWORD_SWEEP: Added {sum(len(v) for v in extra.values())} fallback annotations across {len(extra)} pages")
                except Exception as sweep_err:
                    logger.warning(f"PDF_KEYWORD_SWEEP_FAILED: {sweep_err}")

            # Log summary before adding annotations
            logger.info(f"PDF_ANNOTATION_SUMMARY: {len(conflicts)} conflicts processed, {len(page_annotations)} pages will have annotations")
            
            if not page_annotations:
                logger.warning(f"PDF_ANNOTATION_WARNING: No page annotations created - conflicts may not have been found in PDF")
                # Add a warning annotation to the first page if no matches found
                if len(doc) > 0:
                    page = doc[0]
                    point = fitz.Point(50, 50)
                    annot = page.add_text_annot(point, "Legal-AI: Conflicts Detected")
                    annot.set_info(title="Legal-AI Conflict Detection", content=f"AI detected {len(conflicts)} conflicts but exact text matches could not be found in PDF. Check the analysis report for details.")
                    annot.set_colors(stroke=(1, 0, 0))
                    annot.update()
                    logger.info("PDF_ANNOTATION: Added warning annotation to first page")
            
            # Add annotations to pages
            for page_num, annotations in page_annotations.items():
                try:
                    page = doc[page_num - 1]  # Convert to 0-indexed
                    
                    # Create individual annotations for each conflict on this page
                    for item in annotations:
                        x = item.get('x', 50)
                        y = item.get('y', 750)
                        pos = item.get('position')
                        all_positions = item.get('all_positions', [])
                        
                        # If we have rectangles from matches, use them to highlight the text
                        if all_positions:
                            # Create highlight annotations for ALL lines of the conflict
                            # This ensures the entire multi-line conflict is highlighted
                            try:
                                for highlight_pos in all_positions:
                                    if highlight_pos and hasattr(highlight_pos, 'x0'):
                                        highlight = page.add_highlight_annot(highlight_pos)
                                        highlight.set_colors(stroke=(1, 0, 0))  # Red color
                                        highlight.update()
                                
                                # Only create ONE text annotation (conflict tag) per conflict
                                # Position it at the start of the first line (topmost position)
                                if pos and hasattr(pos, 'x1'):
                                    # Position the icon to the right of the highlighted text on the first line
                                    comment_x = pos.x1 + 5  # 5 points to the right
                                    comment_y = pos.y0
                                else:
                                    comment_x = x + 50
                                    comment_y = y
                                
                                comment_point = fitz.Point(comment_x, comment_y)
                                
                                # Create text annotation (shows as an icon/note that displays comment when clicked)
                                # This is the ONLY conflict tag for this conflict, even if it spans multiple lines
                                comment_annot = page.add_text_annot(comment_point, item['clarification_id'])
                                comment_annot.set_info(title=f"Conflict {item['clarification_id']}", content=item['comment'])
                                comment_annot.set_colors(stroke=(1, 0, 0))  # Red color
                                comment_annot.update()
                                
                                logger.info(f"PDF_ANNOTATION: Added {len(all_positions)} highlights + 1 text annotation for conflict {item['clarification_id']} (multi-line conflict)")
                            except Exception as annot_error:
                                logger.warning(f"Could not add annotations: {annot_error}")
                                # Fall back to text annotation only
                                point = fitz.Point(x, y)
                                annot = page.add_text_annot(point, f"[{item['clarification_id']}]")
                                annot.set_info(title=f"Conflict {item['clarification_id']}", content=item['comment'])
                                annot.set_colors(stroke=(1, 0, 0))
                                annot.update()
                        elif pos and hasattr(pos, 'x0'):
                            # Single-line conflict with position data
                            try:
                                # Create a highlight annotation on the actual text
                                highlight = page.add_highlight_annot(pos)
                                highlight.set_colors(stroke=(1, 0, 0))  # Red color
                                highlight.update()
                                
                                # Also add a text annotation icon next to the highlighted text for the comment
                                # Position the icon to the right of the highlighted text
                                comment_x = pos.x1 + 5 if hasattr(pos, 'x1') else x + 50  # 5 points to the right
                                comment_y = pos.y0
                                comment_point = fitz.Point(comment_x, comment_y)
                                
                                # Create text annotation (shows as an icon/note that displays comment when clicked)
                                comment_annot = page.add_text_annot(comment_point, item['clarification_id'])
                                comment_annot.set_info(title=f"Conflict {item['clarification_id']}", content=item['comment'])
                                comment_annot.set_colors(stroke=(1, 0, 0))  # Red color
                                comment_annot.update()
                                
                                logger.info(f"PDF_ANNOTATION: Added highlight + text annotation for conflict {item['clarification_id']} at rect {pos.x0},{pos.y0}-{pos.x1},{pos.y1}")
                            except Exception as annot_error:
                                logger.warning(f"Could not add annotations: {annot_error}")
                                # Fall back to text annotation only
                                point = fitz.Point(x, y)
                                annot = page.add_text_annot(point, f"[{item['clarification_id']}]")
                                annot.set_info(title=f"Conflict {item['clarification_id']}", content=item['comment'])
                                annot.set_colors(stroke=(1, 0, 0))
                                annot.update()
                        else:
                            # No position data, use text annotation at coordinates
                            point = fitz.Point(x, y)
                            annot = page.add_text_annot(point, f"[{item['clarification_id']}]")
                            annot.set_info(title=f"Conflict {item['clarification_id']}", content=item['comment'])
                            annot.set_colors(stroke=(1, 0, 0))
                            annot.update()
                            logger.info(f"PDF_ANNOTATION: Added text annotation for conflict {item['clarification_id']} at ({x}, {y})")
                    
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
        """Normalize text for matching across PDFs: unify quotes/dashes, strip soft hyphens, collapse spaces."""
        if not text:
            return ""
        # Common unicode normalizations
        replacements = {
            '\u00A0': ' ',   # NBSP -> space
            '\u00AD': '',    # soft hyphen -> remove
            '\u2010': '-',   # hyphen
            '\u2011': '-',   # non-breaking hyphen
            '\u2012': '-',   # figure dash
            '\u2013': '-',   # en dash
            '\u2014': '-',   # em dash
            '\u2015': '-',   # horizontal bar
            '\u2018': "'",  # left single quote
            '\u2019': "'",  # right single quote
            '\u201C': '"',  # left double quote
            '\u201D': '"',  # right double quote
            '\u2026': '...', # ellipsis
            '\ufb01': 'fi',  # ligature fi
            '\ufb02': 'fl',  # ligature fl
        }
        normalized = text
        for src, tgt in replacements.items():
            normalized = normalized.replace(src, tgt)
        # Remove stray control characters
        normalized = re.sub(r'[\u0000-\u001F\u007F]', ' ', normalized)
        # Collapse whitespace
        normalized = re.sub(r'\s+', ' ', normalized)
        # Trim
        normalized = normalized.strip().lower()
        return normalized

    def _generate_search_chunks(self, text: str) -> List[str]:
        """Generate clause-based and n-gram chunks to anchor approximate matches."""
        if not text:
            return []
        # Normalize only for splitting purposes but search with original unicode where possible
        cleaned = self._normalize_text(text)
        # Split into clauses on punctuation
        clauses = [c.strip() for c in re.split(r'[.;:!?\n]+', cleaned) if c.strip()]
        chunks: List[str] = []
        for clause in clauses:
            words = clause.split()
            if len(words) >= 4:
                # Sliding windows from longer to shorter
                for window in [12, 10, 8, 6, 5, 4]:
                    if len(words) < window:
                        continue
                    for i in range(0, len(words) - window + 1):
                        chunk = ' '.join(words[i:i+window])
                        chunks.append(chunk)
            else:
                chunks.append(clause)
        # Deduplicate preserving order
        seen = set()
        deduped: List[str] = []
        for ch in chunks:
            if ch not in seen:
                seen.add(ch)
                deduped.append(ch)
        return deduped


def is_pdf_file(filename: str) -> bool:
    """Check if a file is a PDF based on extension."""
    return filename.lower().endswith('.pdf')


def get_pdf_processor() -> PDFProcessor:
    """Get a PDF processor instance."""
    return PDFProcessor()

    
    
    
    
