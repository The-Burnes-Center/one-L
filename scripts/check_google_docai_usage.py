#!/usr/bin/env python3
"""
Check CloudWatch logs to see if Google Document AI was used for PDF conversion.
Looks for specific log messages that indicate Google Document AI vs PyMuPDF fallback.
"""

import sys
import boto3
from datetime import datetime, timedelta
import re

def check_logs_for_google_docai(job_id: str = None, function_name: str = "OneL-DV2-document-review", hours_back: int = 24):
    """Check CloudWatch logs for Google Document AI usage indicators."""
    
    logs_client = boto3.client('logs')
    
    # Calculate time range
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(hours=hours_back)
    
    log_group_name = f"/aws/lambda/{function_name}"
    
    print("=" * 80)
    print("Checking CloudWatch Logs for Google Document AI Usage")
    print("=" * 80)
    print(f"\nLog Group: {log_group_name}")
    print(f"Time Range: {start_time.strftime('%Y-%m-%d %H:%M:%S')} to {end_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    if job_id:
        print(f"Filtering for Job ID: {job_id}")
    print()
    
    # Search patterns
    google_patterns = [
        r"PDF_TO_DOCX_START.*Google Document AI",
        r"PDF_TO_DOCX: Using Google Document AI",
        r"PDF_TO_DOCX: Sending PDF to Google Document AI",
        r"PDF_TO_DOCX: Google Document AI processed",
        r"PDF_TO_DOCX_SUCCESS.*Google Document AI",
        r"Using credentials from GOOGLE_APPLICATION_CREDENTIALS_JSON"
    ]
    
    fallback_patterns = [
        r"PDF_TO_DOCX_FALLBACK",
        r"PyMuPDF conversion.*fallback",
        r"PDF_TO_DOCX: Google Document AI not available",
        r"PDF_TO_DOCX_GOOGLE_ERROR",
        r"PDF_TO_DOCX_GOOGLE_FAILED"
    ]
    
    try:
        # Get log streams
        streams_response = logs_client.describe_log_streams(
            logGroupName=log_group_name,
            orderBy='LastEventTime',
            descending=True,
            limit=10
        )
        
        if not streams_response.get('logStreams'):
            print(f"ERROR: No log streams found in {log_group_name}")
            print("This might mean:")
            print("  1. The Lambda function hasn't been invoked recently")
            print("  2. The log group name is incorrect")
            print("  3. Logs have been deleted")
            return
        
        print(f"Found {len(streams_response['logStreams'])} recent log streams\n")
        
        # Search for events
        google_found = False
        fallback_found = False
        job_found = False
        
        for stream in streams_response['logStreams'][:5]:  # Check most recent 5 streams
            stream_name = stream['logStreamName']
            
            # Get log events
            try:
                events_response = logs_client.get_log_events(
                    logGroupName=log_group_name,
                    logStreamName=stream_name,
                    startTime=int(start_time.timestamp() * 1000),
                    endTime=int(end_time.timestamp() * 1000),
                    limit=1000
                )
                
                for event in events_response.get('events', []):
                    message = event['message']
                    timestamp = datetime.fromtimestamp(event['timestamp'] / 1000)
                    
                    # Filter by job_id if provided
                    if job_id and job_id not in message:
                        continue
                    
                    if job_id and job_id in message:
                        job_found = True
                    
                    # Check for Google Document AI indicators
                    for pattern in google_patterns:
                        if re.search(pattern, message, re.IGNORECASE):
                            google_found = True
                            print(f"[GOOGLE DOC AI] {timestamp.strftime('%Y-%m-%d %H:%M:%S')} - {message[:200]}")
                    
                    # Check for fallback indicators
                    for pattern in fallback_patterns:
                        if re.search(pattern, message, re.IGNORECASE):
                            fallback_found = True
                            print(f"[FALLBACK] {timestamp.strftime('%Y-%m-%d %H:%M:%S')} - {message[:200]}")
                    
                    # Check for PDF conversion start
                    if "PDF_TO_DOCX_START" in message or "PROCESSING_PDF: Converting PDF to DOCX" in message:
                        print(f"[CONVERSION START] {timestamp.strftime('%Y-%m-%d %H:%M:%S')} - {message[:200]}")
            
            except Exception as e:
                print(f"Warning: Could not read stream {stream_name}: {e}")
                continue
        
        # Summary
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        
        if job_id and not job_found:
            print(f"\n[WARN] No logs found for job ID: {job_id}")
            print("This might mean:")
            print("  - The job hasn't been processed yet")
            print("  - The job ID is incorrect")
            print("  - Logs are older than the search window")
        
        if google_found:
            print("\n[RESULT] Google Document AI WAS USED for PDF conversion")
            print("  - Found Google Document AI log messages")
            print("  - Conversion was successful with Google Document AI")
        elif fallback_found:
            print("\n[RESULT] PyMuPDF FALLBACK was used (Google Document AI not used)")
            print("  - Found fallback log messages")
            print("  - This could mean:")
            print("    * Google credentials not configured")
            print("    * Google Document AI API error")
            print("    * Google libraries not installed")
        else:
            print("\n[RESULT] No clear indication found")
            print("  - Could not find Google Document AI or fallback messages")
            print("  - This might mean:")
            print("    * No PDF was processed in the time window")
            print("    * Logs are in a different log group")
            print("    * The document was already a DOCX (no conversion needed)")
        
        print("\nTo check logs manually:")
        print(f"  1. Go to AWS CloudWatch Console")
        print(f"  2. Navigate to Log Groups > /aws/lambda/{function_name}")
        print(f"  3. Search for: 'PDF_TO_DOCX' or 'Google Document AI'")
        
    except logs_client.exceptions.ResourceNotFoundException:
        print(f"ERROR: Log group not found: {log_group_name}")
        print("This might mean:")
        print("  1. The Lambda function hasn't been invoked yet")
        print("  2. The function name is incorrect")
        print("  3. Logs retention has expired")
    except Exception as e:
        print(f"ERROR: {e}")
        print("\nMake sure you have:")
        print("  1. AWS credentials configured")
        print("  2. Permissions to read CloudWatch logs")
        print("  3. Correct AWS region set")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Check if Google Document AI was used for PDF conversion')
    parser.add_argument('--job-id', help='Specific job ID to check (optional)')
    parser.add_argument('--function-name', default='OneL-DV2-document-review', 
                       help='Lambda function name (default: OneL-DV2-document-review)')
    parser.add_argument('--hours', type=int, default=24, 
                       help='Hours to look back (default: 24)')
    
    args = parser.parse_args()
    
    check_logs_for_google_docai(
        job_id=args.job_id,
        function_name=args.function_name,
        hours_back=args.hours
    )

