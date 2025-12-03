#!/usr/bin/env python3
"""
Fix SyntaxWarning in python-docx library.
Changes invalid escape sequence in docx/text/paragraph.py line 177.
"""
import os
import sys
import re

def fix_docx_warning(search_dir='./python'):
    """Fix the invalid escape sequence warning in python-docx."""
    # Search for the file in the specified directory
    for root, dirs, files in os.walk(search_dir):
        if 'paragraph.py' in files and 'docx' in root and 'text' in root:
            filepath = os.path.join(root, 'paragraph.py')
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Check if the problematic line exists
                # The file contains: headerPattern = re.compile(".*Heading (\d+)$")
                # We need to match the literal backslash-d in the file
                # Try multiple patterns to catch different representations
                patterns_to_fix = [
                    ('headerPattern = re.compile(".*Heading (\\d+)$")', 'headerPattern = re.compile(r".*Heading (\\d+)$")'),
                    ('headerPattern = re.compile(".*Heading (\\\\d+)$")', 'headerPattern = re.compile(r".*Heading (\\d+)$")'),
                    # Also try with single quotes
                    ("headerPattern = re.compile('.*Heading (\\d+)$')", "headerPattern = re.compile(r'.*Heading (\\d+)$')"),
                ]
                
                fixed = False
                for old_pattern, new_pattern in patterns_to_fix:
                    if old_pattern in content:
                        content = content.replace(old_pattern, new_pattern)
                        fixed = True
                        break
                
                # If no exact match, try regex-based replacement as fallback
                if not fixed:
                    # Match: headerPattern = re.compile(".*Heading (\d+)$")
                    # Replace with raw string version
                    pattern = r'headerPattern\s*=\s*re\.compile\(["\']\.\*Heading\s*\(\\d\+\)\$["\']\)'
                    replacement = 'headerPattern = re.compile(r".*Heading (\\d+)$")'
                    new_content = re.sub(pattern, replacement, content)
                    if new_content != content:
                        content = new_content
                        fixed = True
                
                if fixed:
                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write(content)
                    print(f"Fixed SyntaxWarning in {filepath}")
                    return True
            except Exception as e:
                print(f"Error fixing {filepath}: {e}", file=sys.stderr)
                return False
    
    print("Could not find docx/text/paragraph.py to fix")
    return False

if __name__ == '__main__':
    # Allow specifying search directory as command line argument
    search_dir = sys.argv[1] if len(sys.argv) > 1 else './python'
    fix_docx_warning(search_dir)

