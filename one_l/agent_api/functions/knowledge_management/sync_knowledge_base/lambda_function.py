"""
Lambda function for manually triggering Knowledge Base synchronization.
"""

import json
import boto3
import os
from typing import Dict, Any
import logging

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize Bedrock client
bedrock_client = boto3.client('bedrock-agent')

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda function to manually trigger Knowledge Base synchronization.
    Can be triggered by:
    1. API Gateway (manual sync)
    2. S3 event notifications (automatic sync on upload)
    
    Expected event format from API Gateway:
    {
        "body": "{\"action\": \"start_sync\", \"data_source\": \"knowledge\" | \"user_documents\" | \"all\"}"
    }
    
    Expected event format from S3:
    {
        "Records": [
            {
                "eventName": "ObjectCreated:Put",
                "s3": {
                    "bucket": {"name": "bucket-name"},
                    "object": {"key": "uploads/file.pdf"}
                }
            }
        ]
    }
    
    Expected direct invocation format:
    {
        "action": "start_sync" | "get_sync_status" | "list_sync_jobs",
        "data_source": "knowledge" | "user_documents" | "all",  # Optional, defaults to "all"
        "job_id": "job-id"  # Required for get_sync_status
    }
    """
    
    try:
        logger.info(f"Sync Knowledge Base request received: {json.dumps(event, default=str)}")
        
        # Check if this is an S3 event notification
        if 'Records' in event and event['Records']:
            return handle_s3_event(event)
        
        # Parse request body - handle both API Gateway proxy and direct invocation
        if 'body' in event and event['body']:
            # API Gateway proxy integration format
            try:
                body_data = json.loads(event['body'])
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse request body: {e}")
                return create_error_response(400, "Invalid JSON in request body")
        else:
            # Direct invocation format
            body_data = event
        
        # Extract parameters from parsed body
        action = body_data.get('action', 'start_sync')
        data_source = body_data.get('data_source', 'all')  # all, knowledge, or user_documents
        
        logger.info(f"Parsed parameters - action: {action}, data_source: {data_source}")
        
        # Get Knowledge Base ID from environment
        knowledge_base_id = os.environ.get('KNOWLEDGE_BASE_ID')
        
        if not knowledge_base_id:
            return create_error_response(500, "Knowledge Base ID not configured")
        
        # Process based on action
        if action == "start_sync":
            return start_sync_job(knowledge_base_id, data_source)
        elif action == "get_sync_status":
            job_id = body_data.get('job_id')
            if not job_id:
                return create_error_response(400, "job_id is required for get_sync_status action")
            return get_sync_job_status(knowledge_base_id, job_id)
        elif action == "list_sync_jobs":
            return list_sync_jobs(knowledge_base_id, data_source)
        else:
            return create_error_response(400, f"Invalid action: {action}")
        
    except Exception as e:
        logger.error(f"Error in sync_knowledge_base function: {str(e)}")
        return create_error_response(500, f"Internal server error: {str(e)}")


def handle_s3_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle S3 event notifications for automatic sync."""
    try:
        logger.info("Processing S3 event notification for automatic sync")
        
        # Get Knowledge Base ID from environment
        knowledge_base_id = os.environ.get('KNOWLEDGE_BASE_ID')
        
        if not knowledge_base_id:
            return create_error_response(500, "Knowledge Base ID not configured")
        
        # Process S3 records
        uploaded_files = []
        for record in event.get('Records', []):
            if record.get('eventName', '').startswith('ObjectCreated:'):
                bucket_name = record['s3']['bucket']['name']
                object_key = record['s3']['object']['key']
                uploaded_files.append({
                    'bucket': bucket_name,
                    'key': object_key
                })
                logger.info(f"File uploaded: {bucket_name}/{object_key}")
        
        if not uploaded_files:
            logger.info("No file uploads found in S3 event")
            return create_success_response({
                "message": "No file uploads to process",
                "processed_files": 0
            })
        
        # Determine data source based on bucket and object key
        data_source = "user_documents"  # Default
        for record in event.get('Records', []):
            if record.get('eventName', '').startswith('ObjectCreated:'):
                bucket_name = record['s3']['bucket']['name']
                object_key = record['s3']['object']['key']
                
                # Determine data source based on bucket and prefix
                if 'knowledge' in bucket_name.lower() or object_key.startswith('admin-uploads/'):
                    data_source = "knowledge"
                elif 'user-documents' in bucket_name.lower() or object_key.startswith('uploads/'):
                    data_source = "user_documents"
                
                break  # Use the first record to determine the data source
        
        # Start sync job for the determined data source
        sync_result = start_sync_job(knowledge_base_id, data_source)
        
        # Add S3 event context to response
        if sync_result['statusCode'] == 200:
            response_data = json.loads(sync_result['body'])
            response_data['trigger'] = 'S3_event'
            response_data['uploaded_files'] = uploaded_files
            sync_result['body'] = json.dumps(response_data)
        
        logger.info(f"Automatic sync triggered for {len(uploaded_files)} uploaded files")
        return sync_result
        
    except Exception as e:
        logger.error(f"Error handling S3 event: {str(e)}")
        return create_error_response(500, f"Error handling S3 event: {str(e)}")


def start_sync_job(knowledge_base_id: str, data_source_filter: str = "all") -> Dict[str, Any]:
    """Start new ingestion jobs for the specified data sources."""
    try:
        # Get all data sources
        data_sources_response = bedrock_client.list_data_sources(
            knowledgeBaseId=knowledge_base_id
        )
        
        if not data_sources_response['dataSourceSummaries']:
            return create_error_response(404, "No data sources found for Knowledge Base")
        
        # Filter data sources based on request
        data_sources_to_sync = []
        for ds in data_sources_response['dataSourceSummaries']:
            ds_name = ds['name'].lower()
            if data_source_filter == "all":
                data_sources_to_sync.append(ds)
            elif data_source_filter == "knowledge" and "knowledge" in ds_name:
                data_sources_to_sync.append(ds)
            elif data_source_filter == "user_documents" and "user-documents" in ds_name:
                data_sources_to_sync.append(ds)
        
        if not data_sources_to_sync:
            return create_error_response(404, f"No data sources found matching filter: {data_source_filter}")
        
        # Start ingestion jobs for selected data sources
        sync_jobs = []
        for data_source in data_sources_to_sync:
            try:
                response = bedrock_client.start_ingestion_job(
                    knowledgeBaseId=knowledge_base_id,
                    dataSourceId=data_source['dataSourceId'],
                    description=f"Manual sync triggered via API for {data_source['name']}"
                )
                
                sync_jobs.append({
                    "job_id": response['ingestionJob']['ingestionJobId'],
                    "status": response['ingestionJob']['status'],
                    "data_source_id": data_source['dataSourceId'],
                    "data_source_name": data_source['name'],
                    "success": True
                })
                
                logger.info(f"Started ingestion job {response['ingestionJob']['ingestionJobId']} for {data_source['name']}")
                
            except Exception as e:
                sync_jobs.append({
                    "data_source_id": data_source['dataSourceId'],
                    "data_source_name": data_source['name'],
                    "success": False,
                    "error": str(e)
                })
                logger.error(f"Failed to start job for {data_source['name']}: {str(e)}")
        
        successful_jobs = [job for job in sync_jobs if job.get('success', False)]
        failed_jobs = [job for job in sync_jobs if not job.get('success', False)]
        
        return create_success_response({
            "message": f"Sync jobs processed for {len(data_sources_to_sync)} data source(s)",
            "sync_jobs": sync_jobs,
            "successful_count": len(successful_jobs),
            "failed_count": len(failed_jobs),
            "knowledge_base_id": knowledge_base_id,
            "data_source_filter": data_source_filter
        })
        
    except Exception as e:
        logger.error(f"Error starting sync jobs: {str(e)}")
        return create_error_response(500, f"Error starting sync jobs: {str(e)}")


def get_sync_job_status(knowledge_base_id: str, job_id: str) -> Dict[str, Any]:
    """Get the status of a specific ingestion job."""
    try:
        # Get data source ID (assuming first data source)
        data_sources_response = bedrock_client.list_data_sources(
            knowledgeBaseId=knowledge_base_id
        )
        
        if not data_sources_response['dataSourceSummaries']:
            return create_error_response(404, "No data sources found for Knowledge Base")
        
        data_source_id = data_sources_response['dataSourceSummaries'][0]['dataSourceId']
        
        # Get ingestion job details
        response = bedrock_client.get_ingestion_job(
            knowledgeBaseId=knowledge_base_id,
            dataSourceId=data_source_id,
            ingestionJobId=job_id
        )
        
        job = response['ingestionJob']
        
        return create_success_response({
            "message": "Sync job status retrieved successfully",
            "job_id": job['ingestionJobId'],
            "status": job['status'],
            "created_at": job['createdAt'].isoformat() if 'createdAt' in job else None,
            "updated_at": job['updatedAt'].isoformat() if 'updatedAt' in job else None,
            "statistics": job.get('statistics', {}),
            "failure_reasons": job.get('failureReasons', [])
        })
        
    except Exception as e:
        logger.error(f"Error getting sync job status: {str(e)}")
        return create_error_response(500, f"Error getting sync job status: {str(e)}")


def list_sync_jobs(knowledge_base_id: str, data_source_filter: str = "all") -> Dict[str, Any]:
    """List recent ingestion jobs for the Knowledge Base."""
    try:
        # Get all data sources
        data_sources_response = bedrock_client.list_data_sources(
            knowledgeBaseId=knowledge_base_id
        )
        
        if not data_sources_response['dataSourceSummaries']:
            return create_error_response(404, "No data sources found for Knowledge Base")
        
        # Filter data sources based on request
        data_sources_to_list = []
        for ds in data_sources_response['dataSourceSummaries']:
            ds_name = ds['name'].lower()
            if data_source_filter == "all":
                data_sources_to_list.append(ds)
            elif data_source_filter == "knowledge" and "knowledge" in ds_name:
                data_sources_to_list.append(ds)
            elif data_source_filter == "user_documents" and "user-documents" in ds_name:
                data_sources_to_list.append(ds)
        
        if not data_sources_to_list:
            return create_error_response(404, f"No data sources found matching filter: {data_source_filter}")
        
        # List ingestion jobs for all selected data sources
        all_jobs = []
        for data_source in data_sources_to_list:
            try:
                response = bedrock_client.list_ingestion_jobs(
                    knowledgeBaseId=knowledge_base_id,
                    dataSourceId=data_source['dataSourceId'],
                    maxResults=10
                )
                
                for job in response['ingestionJobSummaries']:
                    all_jobs.append({
                        "job_id": job['ingestionJobId'],
                        "status": job['status'],
                        "created_at": job['startedAt'].isoformat() if 'startedAt' in job else None,
                        "updated_at": job['updatedAt'].isoformat() if 'updatedAt' in job else None,
                        "description": job.get('description', 'No description'),
                        "data_source_id": data_source['dataSourceId'],
                        "data_source_name": data_source['name']
                    })
                    
            except Exception as e:
                logger.error(f"Error listing jobs for data source {data_source['name']}: {str(e)}")
                continue
        
        # Sort jobs by creation date (newest first)
        all_jobs.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return create_success_response({
            "message": "Sync jobs listed successfully",
            "jobs": all_jobs,
            "total_jobs": len(all_jobs),
            "knowledge_base_id": knowledge_base_id,
            "data_source_filter": data_source_filter,
            "data_sources_count": len(data_sources_to_list)
        })
        
    except Exception as e:
        logger.error(f"Error listing sync jobs: {str(e)}")
        return create_error_response(500, f"Error listing sync jobs: {str(e)}")


def create_success_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Create successful response."""
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, X-Amz-Date, Authorization, X-Api-Key, X-Amz-Security-Token'
        },
        'body': json.dumps(data)
    }


def create_error_response(status_code: int, message: str, data: Dict[str, Any] = None) -> Dict[str, Any]:
    """Create error response."""
    error_body = {'error': message, 'status_code': status_code}
    if data:
        error_body.update(data)
    
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, X-Amz-Date, Authorization, X-Api-Key, X-Amz-Security-Token'
        },
        'body': json.dumps(error_body)
    } 