# One-L API Documentation

## Overview

The One-L system provides both REST API endpoints and WebSocket connections for real-time functionality. All APIs require authentication via AWS Cognito JWT tokens.

## Authentication

All API calls require an `Authorization` header with a valid JWT token from AWS Cognito:

```
Authorization: Bearer <jwt-token>
```

## Base URLs

- **REST API**: `https://<api-gateway-id>.execute-api.<region>.amazonaws.com/prod`
- **WebSocket**: `wss://<websocket-api-id>.execute-api.<region>.amazonaws.com/prod`

## REST API Endpoints

### Knowledge Management

#### Upload Files to S3
```http
POST /knowledge_management/upload
Content-Type: application/json

{
  "bucket_type": "user_documents",
  "files": [
    {
      "filename": "contract.docx",
      "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "file_size": 1024000
    }
  ],
  "prefix": "uploads/",
  "user_id": "user123",
  "session_id": "session456"
}
```

**Response:**
```json
{
  "presigned_urls": [
    {
      "success": true,
      "presigned_url": "https://s3.amazonaws.com/...",
      "unique_filename": "contract_20241201_abc123.docx",
      "s3_key": "uploads/user123/contract_20241201_abc123.docx",
      "bucket_name": "one-l-user-documents"
    }
  ]
}
```

#### Retrieve Files from S3
```http
POST /knowledge_management/retrieve
Content-Type: application/json

{
  "bucket_type": "user_documents",
  "s3_key": "uploads/user123/contract.docx",
  "return_content": true
}
```

#### Sync Knowledge Base
```http
POST /knowledge_management/sync
Content-Type: application/json

{
  "action": "start_sync",
  "data_source": "all"
}
```

### Session Management

#### Create Session
```http
POST /knowledge_management/sessions?action=create
Content-Type: application/json

{
  "user_id": "user123",
  "action": "create"
}
```

**Response:**
```json
{
  "success": true,
  "session": {
    "session_id": "uuid-session-id",
    "user_id": "user123",
    "title": "Session 2024-12-01 10:30",
    "created_at": "2024-12-01T10:30:00Z",
    "status": "active",
    "has_results": false
  }
}
```

#### Get User Sessions
```http
GET /knowledge_management/sessions?action=list&user_id=user123&filter_by_results=false
```

#### Get Session Results
```http
GET /knowledge_management/sessions?action=session_results&session_id=session123&user_id=user123
```

### Agent Document Review (Step Functions Workflow)

#### Review Document
```http
POST /agent/review
Content-Type: application/json

{
  "document_s3_key": "vendor-submissions/contract.docx",
  "bucket_type": "agent_processing",
  "session_id": "session123",
  "user_id": "user123",
  "terms_profile": "it"
}
```

**Note**: This endpoint starts a Step Functions workflow that orchestrates the entire document analysis process. The workflow runs asynchronously and can take 2-5 minutes for completion.

**Response (Immediate):**
```json
{
  "success": true,
  "processing": true,
  "job_id": "job-uuid",
  "execution_arn": "arn:aws:states:region:account:execution:state-machine-name:execution-name",
  "message": "Document review started successfully",
  "estimated_completion_time": "2-5 minutes"
}
```

#### Get Job Status
```http
POST /agent/job-status
Content-Type: application/json

{
  "job_id": "job-uuid"
}
```

**Response:**
```json
{
  "success": true,
  "job_id": "job-uuid",
  "status": "processing",
  "stage": "analyzing",
  "progress": 50,
  "message": "Analyzing document structure...",
  "execution_status": "RUNNING"
}
```

**Response (Completion via WebSocket):**
```json
{
  "type": "job_completed",
  "job_id": "job-uuid",
  "session_id": "session123",
  "data": {
    "analysis_id": "analysis-uuid",
    "redlined_document": {
      "success": true,
      "redlined_document": "redlined/session123/contract_redlined.docx"
    }
  }
}
```

## WebSocket API

### Connection

Connect to WebSocket with user authentication:
```
wss://<websocket-api-id>.execute-api.<region>.amazonaws.com/prod?userId=user123
```

### Message Types

#### Subscribe to Job Updates
```json
{
  "action": "subscribe",
  "jobId": "job-uuid",
  "sessionId": "session123"
}
```

#### Subscribe to Session Updates
```json
{
  "action": "subscribe",
  "sessionId": "session123",
  "subscribeToSession": true
}
```

#### Ping (Keep-Alive)
```json
{
  "action": "ping"
}
```

### Incoming Message Types

#### Connection Established
```json
{
  "type": "connection_established",
  "connectionId": "connection-id",
  "message": "WebSocket connection established successfully"
}
```

#### Job Progress (Step Functions Execution Updates)
```json
{
  "type": "job_progress",
  "job_id": "job-uuid",
  "session_id": "session123",
  "data": {
    "status": "processing",
    "stage": "analyzing_structure",
    "progress": 50,
    "message": "Step Functions workflow: Analyzing document structure...",
    "execution_arn": "arn:aws:states:region:account:execution:state-machine-name:execution-name",
    "current_state": "AnalyzeStructure"
  }
}
```

#### Job Completed
```json
{
  "type": "job_completed",
  "job_id": "job-uuid",
  "session_id": "session123",
  "data": {
    "status": "completed",
    "analysis_id": "analysis-uuid",
    "redlined_document": {
      "success": true,
      "redlined_document": "redlined/session123/contract_redlined.docx"
    }
  }
}
```

## Error Handling

### HTTP Status Codes

- `200 OK` - Request successful
- `400 Bad Request` - Invalid request parameters
- `401 Unauthorized` - Missing or invalid authentication
- `403 Forbidden` - Insufficient permissions
- `404 Not Found` - Resource not found
- `500 Internal Server Error` - Server error
- `504 Gateway Timeout` - Request timeout (Step Functions workflow continues execution asynchronously)

### Error Response Format
```json
{
  "error": "Error description",
  "status_code": 400,
  "details": "Additional error context"
}
```

### Common Error Scenarios

#### File Upload Errors
- File size exceeds 10MB limit
- Unsupported file type
- Invalid S3 key format

#### Document Processing Errors
- Document not found in S3
- Step Functions execution failure (check execution ARN in response)
- Knowledge base sync in progress
- Workflow timeout (Step Functions supports up to 2 hours)

#### Authentication Errors
- Expired JWT token
- Invalid user ID
- Missing authorization header

## Rate Limits

- **File Uploads**: 50 files per minute per user
- **Document Analysis**: 5 concurrent Step Functions executions per user
- **WebSocket Connections**: 10 connections per user
- **API Calls**: 1000 requests per minute per user

## Monitoring and Debugging

### CloudWatch Logs
- Lambda function logs: `/aws/lambda/OneLStack-<function-name>`
- Step Functions execution logs: `/aws/vendedlogs/states/<state-machine-name>/<execution-name>`
- API Gateway logs: Available via CloudWatch integration
- WebSocket logs: `/aws/apigateway/<websocket-api-id>/prod`

### X-Ray Tracing
All Lambda functions include X-Ray tracing for distributed request tracking.

### Health Checks
- **API Gateway**: Returns 200 for basic connectivity
- **WebSocket**: Ping/pong mechanism for connection health
- **Lambda Functions**: Built-in CloudWatch metrics for duration, errors, throttles
