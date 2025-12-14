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

def get_kb_id_by_name(name: str) -> str:
    """Resolve Knowledge Base ID from Name."""
    try:
        paginator = bedrock_client.get_paginator('list_knowledge_bases')
        for page in paginator.paginate(MaxResults=100):
            for kb in page.get('knowledgeBaseSummaries', []):
                if kb.get('name') == name:
                    logger.info(f"Resolved Knowledge Base ID {kb.get('knowledgeBaseId')} for name {name}")
                    return kb.get('knowledgeBaseId')
        logger.warning(f"Knowledge Base with name {name} not found")
    except Exception as e:
        logger.error(f"Error resolving KB ID for name {name}: {e}")
    return None

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
        data_source = body_data.get('data_source', 'all')  # all, knowledge, user_documents, or terms
        terms_bucket = body_data.get('terms_bucket')  # Optional: 'general_terms', 'it_terms_updated', or 'it_terms_old'
        
        logger.info(f"Parsed parameters - action: {action}, data_source: {data_source}, terms_bucket: {terms_bucket}")
        
        # Get Knowledge Base ID from environment
        knowledge_base_id = os.environ.get('KNOWLEDGE_BASE_ID')
        
        # Fallback to name lookup if ID not set
        if not knowledge_base_id and os.environ.get('KNOWLEDGE_BASE_NAME'):
            kb_name = os.environ.get('KNOWLEDGE_BASE_NAME')
            logger.info(f"KNOWLEDGE_BASE_ID not set, attempting to resolve from name: {kb_name}")
            knowledge_base_id = get_kb_id_by_name(kb_name)
        
        if not knowledge_base_id:
            return create_error_response(500, "Knowledge Base ID not configured")
        
        # Process based on action
        if action == "start_sync":
            return start_sync_job(knowledge_base_id, data_source, terms_bucket)
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
        
        # Fallback to name lookup if ID not set
        if not knowledge_base_id and os.environ.get('KNOWLEDGE_BASE_NAME'):
            kb_name = os.environ.get('KNOWLEDGE_BASE_NAME')
            knowledge_base_id = get_kb_id_by_name(kb_name)
        
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
        terms_bucket = None
        for record in event.get('Records', []):
            if record.get('eventName', '').startswith('ObjectCreated:'):
                bucket_name = record['s3']['bucket']['name']
                object_key = record['s3']['object']['key']
                
                # Determine data source based on bucket and prefix
                if 'knowledge' in bucket_name.lower() or object_key.startswith('admin-uploads/'):
                    data_source = "knowledge"
                elif 'user-documents' in bucket_name.lower() or object_key.startswith('uploads/'):
                    data_source = "user_documents"
                elif 'general-terms' in bucket_name.lower():
                    data_source = "terms"
                    terms_bucket = "general_terms"
                elif 'it-terms-updated' in bucket_name.lower():
                    data_source = "terms"
                    terms_bucket = "it_terms_updated"
                elif 'it-terms-old' in bucket_name.lower():
                    data_source = "terms"
                    terms_bucket = "it_terms_old"
                
                break  # Use the first record to determine the data source
        
        # Start sync job for the determined data source
        sync_result = start_sync_job(knowledge_base_id, data_source, terms_bucket)
        
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


def delete_documents_from_data_source(knowledge_base_id: str, data_source_id: str, data_source_name: str) -> Dict[str, Any]:
    """Delete all documents from a specific data source in the knowledge base."""
    try:
        logger.info(f"Listing documents from data source: {data_source_name} ({data_source_id})")
        
        # List all documents from this data source
        document_identifiers = []
        try:
            paginator = bedrock_client.get_paginator('list_knowledge_base_documents')
            
            for page in paginator.paginate(
                knowledgeBaseId=knowledge_base_id,
                dataSourceId=data_source_id,
                maxResults=100
            ):
                for doc in page.get('documentDetails', []):
                    # Extract document identifier - for S3 data sources, documentId is the S3 URI
                    doc_id = doc.get('documentId', '')
                    doc_name = doc.get('name', '')
                    
                    if doc_id:
                        # For S3 data sources, documentId is typically the S3 URI (s3://bucket/key)
                        # If it's not already a URI, we'll try using it as-is since Bedrock may handle it
                        if doc_id.startswith('s3://'):
                            s3_uri = doc_id
                        else:
                            # If documentId is not a URI, construct it from the document name or use as-is
                            # Bedrock may accept documentId directly
                            s3_uri = doc_id
                        
                        document_identifiers.append({
                            'dataSourceType': 'S3',
                            's3': {
                                'uri': s3_uri
                            }
                        })
        except Exception as list_error:
            # If listing fails, log and return - might be no documents or API issue
            logger.warning(f"Could not list documents from {data_source_name}: {str(list_error)}")
            # Check if it's because there are no documents
            if "not found" in str(list_error).lower() or "empty" in str(list_error).lower():
                return {
                    'success': True,
                    'deleted_count': 0,
                    'message': f'No documents found in {data_source_name}'
                }
            # Otherwise, return error
            return {
                'success': False,
                'error': f'Failed to list documents: {str(list_error)}',
                'deleted_count': 0
            }
        
        if not document_identifiers:
            logger.info(f"No documents found in data source {data_source_name} to delete")
            return {
                'success': True,
                'deleted_count': 0,
                'message': f'No documents found in {data_source_name}'
            }
        
        logger.info(f"Found {len(document_identifiers)} documents to delete from {data_source_name}")
        
        # Delete documents in batches (Bedrock may have limits on batch size)
        batch_size = 100
        total_deleted = 0
        failed_deletions = []
        
        for i in range(0, len(document_identifiers), batch_size):
            batch = document_identifiers[i:i + batch_size]
            try:
                delete_response = bedrock_client.delete_knowledge_base_documents(
                    knowledgeBaseId=knowledge_base_id,
                    dataSourceId=data_source_id,
                    documentIdentifiers=batch
                )
                
                # Check for failed documents
                failed_docs = delete_response.get('failedDocuments', [])
                successful_deletions = len(batch) - len(failed_docs)
                total_deleted += successful_deletions
                
                if failed_docs:
                    failed_deletions.extend(failed_docs)
                    logger.warning(f"Batch {i//batch_size + 1}: {len(failed_docs)} documents failed to delete")
                
                logger.info(f"Deleted batch {i//batch_size + 1}: {successful_deletions} successful, {len(failed_docs)} failed")
                
            except Exception as e:
                logger.error(f"Error deleting batch from {data_source_name}: {str(e)}")
                # Continue with other batches even if one fails
                failed_deletions.extend(batch)
        
        if failed_deletions:
            logger.warning(f"Some deletions failed: {len(failed_deletions)} documents could not be deleted")
        
        logger.info(f"Successfully deleted {total_deleted} documents from {data_source_name}")
        return {
            'success': True,
            'deleted_count': total_deleted,
            'failed_count': len(failed_deletions),
            'message': f'Deleted {total_deleted} documents from {data_source_name}',
            'failed_documents': failed_deletions if failed_deletions else None
        }
        
    except Exception as e:
        logger.error(f"Error deleting documents from {data_source_name}: {str(e)}")
        return {
            'success': False,
            'error': str(e),
            'deleted_count': 0
        }


def start_sync_job(knowledge_base_id: str, data_source_filter: str = "all", terms_bucket: str = None) -> Dict[str, Any]:
    """Start new ingestion jobs for the specified data sources."""
    try:
        # Get all data sources
        data_sources_response = bedrock_client.list_data_sources(
            knowledgeBaseId=knowledge_base_id
        )
        
        if not data_sources_response['dataSourceSummaries']:
            return create_error_response(404, "No data sources found for Knowledge Base")
        
        # Map terms bucket names to data source name patterns
        terms_bucket_patterns = {
            'general_terms': ['general-terms', 'general_terms'],
            'it_terms_updated': ['it-terms-updated', 'it_terms_updated'],
            'it_terms_old': ['it-terms-old', 'it_terms_old']
        }
        
        # If syncing a specific terms bucket, first remove documents from other terms buckets
        cleanup_results = []
        if terms_bucket and data_source_filter == "terms":
            logger.info(f"Syncing terms bucket {terms_bucket} - cleaning up other terms buckets first")
            
            # Find all terms bucket data sources
            for ds in data_sources_response['dataSourceSummaries']:
                ds_name = ds['name'].lower()
                # Check if this is a terms bucket data source
                is_terms_source = any(
                    pattern in ds_name 
                    for patterns in terms_bucket_patterns.values() 
                    for pattern in patterns
                )
                
                if is_terms_source:
                    # Check if this is NOT the bucket we're syncing
                    selected_patterns = terms_bucket_patterns.get(terms_bucket, [])
                    is_selected_bucket = any(pattern in ds_name for pattern in selected_patterns)
                    
                    if not is_selected_bucket:
                        # This is another terms bucket - delete its documents
                        logger.info(f"Removing documents from other terms bucket: {ds['name']}")
                        cleanup_result = delete_documents_from_data_source(
                            knowledge_base_id,
                            ds['dataSourceId'],
                            ds['name']
                        )
                        cleanup_results.append({
                            'data_source_name': ds['name'],
                            'data_source_id': ds['dataSourceId'],
                            'cleanup_result': cleanup_result
                        })
        
        # Filter data sources based on request
        data_sources_to_sync = []
        for ds in data_sources_response['dataSourceSummaries']:
            ds_name = ds['name'].lower()
            if data_source_filter == "all":
                # If terms_bucket is specified, only sync that specific terms bucket
                if terms_bucket:
                    patterns = terms_bucket_patterns.get(terms_bucket, [])
                    if any(pattern in ds_name for pattern in patterns):
                        data_sources_to_sync.append(ds)
                    elif "knowledge" in ds_name or "user-documents" in ds_name:
                        # Don't sync other buckets when a specific terms bucket is selected
                        continue
                else:
                    data_sources_to_sync.append(ds)
            elif data_source_filter == "knowledge" and "knowledge" in ds_name:
                data_sources_to_sync.append(ds)
            elif data_source_filter == "user_documents" and "user-documents" in ds_name:
                data_sources_to_sync.append(ds)
            elif data_source_filter.startswith("terms"):
                # Handle terms bucket selection
                if terms_bucket:
                    # Specific terms bucket requested
                    patterns = terms_bucket_patterns.get(terms_bucket, [])
                    if any(pattern in ds_name for pattern in patterns):
                        data_sources_to_sync.append(ds)
                else:
                    # All terms buckets if terms is specified but no specific bucket
                    if any(pattern in ds_name for patterns in terms_bucket_patterns.values() for pattern in patterns):
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
        
        response_data = {
            "message": f"Sync jobs processed for {len(data_sources_to_sync)} data source(s)",
            "sync_jobs": sync_jobs,
            "successful_count": len(successful_jobs),
            "failed_count": len(failed_jobs),
            "knowledge_base_id": knowledge_base_id,
            "data_source_filter": data_source_filter
        }
        
        # Include cleanup results if we cleaned up other terms buckets
        if cleanup_results:
            response_data["cleanup_performed"] = True
            response_data["cleanup_results"] = cleanup_results
            total_cleaned = sum(
                r['cleanup_result'].get('deleted_count', 0) 
                for r in cleanup_results 
                if r['cleanup_result'].get('success', False)
            )
            response_data["cleanup_message"] = f"Removed {total_cleaned} documents from other terms buckets before syncing"
        else:
            response_data["cleanup_performed"] = False
        
        return create_success_response(response_data)
        
    except Exception as e:
        logger.error(f"Error starting sync jobs: {str(e)}")
        return create_error_response(500, f"Error starting sync jobs: {str(e)}")


def get_sync_job_status(knowledge_base_id: str, job_id: str) -> Dict[str, Any]:
    """Get the status of a specific ingestion job."""
    try:
        # Get all data sources
        data_sources_response = bedrock_client.list_data_sources(
            knowledgeBaseId=knowledge_base_id
        )
        
        if not data_sources_response['dataSourceSummaries']:
            return create_error_response(404, "No data sources found for Knowledge Base")
        
        # Try to find the job in each data source until we find it
        last_error = None
        for data_source in data_sources_response['dataSourceSummaries']:
            try:
                data_source_id = data_source['dataSourceId']
                data_source_name = data_source['name']
                
                logger.info(f"Checking for job {job_id} in data source {data_source_name} ({data_source_id})")
                
                # Try to get ingestion job from this data source
                response = bedrock_client.get_ingestion_job(
                    knowledgeBaseId=knowledge_base_id,
                    dataSourceId=data_source_id,
                    ingestionJobId=job_id
                )
                
                job = response['ingestionJob']
                
                logger.info(f"Found job {job_id} in data source {data_source_name}")
                
                return create_success_response({
                    "message": "Sync job status retrieved successfully",
                    "job_id": job['ingestionJobId'],
                    "status": job['status'],
                    "data_source_id": data_source_id,
                    "data_source_name": data_source_name,
                    "created_at": job['createdAt'].isoformat() if 'createdAt' in job else None,
                    "updated_at": job['updatedAt'].isoformat() if 'updatedAt' in job else None,
                    "statistics": job.get('statistics', {}),
                    "failure_reasons": job.get('failureReasons', [])
                })
                
            except Exception as e:
                # If job not found in this data source, try the next one
                if "not found" in str(e).lower() or "ResourceNotFoundException" in str(e):
                    logger.info(f"Job {job_id} not found in data source {data_source['name']}, trying next data source")
                    last_error = e
                    continue
                else:
                    # Other error types should be reported immediately
                    logger.error(f"Error checking data source {data_source['name']}: {str(e)}")
                    last_error = e
                    continue
        
        # If we get here, job wasn't found in any data source
        logger.error(f"Job {job_id} not found in any data source. Checked {len(data_sources_response['dataSourceSummaries'])} data sources")
        return create_error_response(404, f"Ingestion job {job_id} not found in any data source")
        
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