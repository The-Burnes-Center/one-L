"""
Split document Lambda function.
Uses character-based _split_document_into_chunks and saves chunks to S3.
"""

import json
import boto3
import logging
import os
import io
from agent_api.agent.prompts.models import DocumentSplitOutput
from agent_api.agent.model import _split_document_into_chunks

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')

def lambda_handler(event, context):
    """
    Split document into chunks using character-based chunking.
    
    Args:
        event: Lambda event with document_s3_key, bucket_name, session_id
        context: Lambda context
        
    Returns:
        DocumentSplitOutput with chunk_count and chunks metadata
    """
    try:
        document_s3_key = event.get('document_s3_key')
        bucket_name = event.get('bucket_name')
        session_id = event.get('session_id')
        
        if not document_s3_key or not bucket_name:
            raise ValueError("document_s3_key and bucket_name are required")
        
        # Download document from S3
        response = s3_client.get_object(Bucket=bucket_name, Key=document_s3_key)
        document_data = response['Body'].read()
        
        # Check if PDF or DOCX
        is_pdf = document_s3_key.lower().endswith('.pdf')
        
        chunks = []
        if is_pdf:
            # For PDFs, use character-based chunking
            chunks = _split_document_into_chunks(
                doc=None,
                is_pdf=True,
                pdf_bytes=document_data
            )
        else:
            # For DOCX, parse and chunk
            from docx import Document
            doc = Document(io.BytesIO(document_data))
            chunks = _split_document_into_chunks(doc=doc, is_pdf=False)
        
        # Save chunks to S3
        chunk_s3_keys = []
        for chunk_info in chunks:
            chunk_num = chunk_info['chunk_num']
            chunk_bytes = chunk_info['bytes']
            
            # Save chunk to S3
            chunk_key = f"{session_id}/chunks/chunk_{chunk_num}.docx"
            s3_client.put_object(
                Bucket=bucket_name,
                Key=chunk_key,
                Body=chunk_bytes
            )
            
            chunk_s3_keys.append({
                'chunk_num': chunk_num,
                'start_char': chunk_info['start_char'],
                'end_char': chunk_info['end_char'],
                's3_key': chunk_key
            })
        
        logger.info(f"Split document into {len(chunks)} chunks")
        
        # Create validated output
        output = DocumentSplitOutput(
            chunk_count=len(chunks),
            chunks=chunk_s3_keys
        )
        
        return {
            "statusCode": 200,
            "body": output.model_dump_json()
        }
        
    except Exception as e:
        logger.error(f"Error splitting document: {e}")
        raise

