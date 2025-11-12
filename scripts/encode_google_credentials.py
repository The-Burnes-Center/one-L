#!/usr/bin/env python3
"""
Helper script to encode Google Cloud service account JSON credentials to base64.
This encoded string can be used as the GOOGLE_APPLICATION_CREDENTIALS_JSON environment variable in Lambda.

Usage:
    python scripts/encode_google_credentials.py path/to/credentials.json

Output:
    Base64-encoded string that you can paste into Lambda environment variables.
"""

import sys
import json
import base64
import os

def encode_credentials(json_file_path: str) -> str:
    """Read JSON credentials file and return base64-encoded string."""
    try:
        with open(json_file_path, 'r') as f:
            credentials = json.load(f)
        
        # Validate it's a service account JSON
        if 'type' not in credentials or credentials['type'] != 'service_account':
            print("Warning: This doesn't look like a service account JSON file.")
        
        # Convert to JSON string and encode
        json_str = json.dumps(credentials)
        encoded = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
        
        return encoded
    except FileNotFoundError:
        print(f"Error: File not found: {json_file_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON file: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/encode_google_credentials.py <path-to-credentials.json>")
        print("\nExample:")
        print("  python scripts/encode_google_credentials.py intense-subject-477818-h6-1307d1a94cb8.json")
        sys.exit(1)
    
    json_file = sys.argv[1]
    
    if not os.path.exists(json_file):
        print(f"Error: File does not exist: {json_file}")
        sys.exit(1)
    
    encoded = encode_credentials(json_file)
    
    print("\n" + "="*80)
    print("Base64-encoded credentials (copy this entire string):")
    print("="*80)
    print(encoded)
    print("="*80)
    print("\nAdd this as the GOOGLE_APPLICATION_CREDENTIALS_JSON environment variable in Lambda.")
    print("The code will automatically decode and use it for authentication.\n")

