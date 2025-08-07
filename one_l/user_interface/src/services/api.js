/**
 * API Service for One-L Application
 * Handles all API calls to the backend through API Gateway
 */

import { getApiGatewayUrl } from '../utils/config';
import authService from './auth';

/**
 * Base API call function
 */
const apiCall = async (endpoint, options = {}) => {
  const baseUrl = await getApiGatewayUrl();
  
  if (!baseUrl) {
    throw new Error('API Gateway URL not configured');
  }
  
  const url = `${baseUrl.replace(/\/$/, '')}${endpoint}`;
  
  const defaultOptions = {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
      ...authService.getAuthorizationHeader(), // Add auth headers if authenticated
      ...options.headers
    }
  };
  
  const finalOptions = { ...defaultOptions, ...options };
  
  // Add timeout for session-related calls
  const timeoutMs = endpoint.includes('/sessions') ? 30000 : 30000; // 30s for sessions, 30s for others
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  
  try {
    const response = await fetch(url, { ...finalOptions, signal: controller.signal });
    clearTimeout(timeoutId);
    
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      // Enhance error message for timeout scenarios
      if (response.status === 504) {
        throw new Error('API Gateway timeout - processing may continue in background');
      } else if (response.status === 502) {
        throw new Error('Bad gateway - service may be processing in background');
      }
      throw new Error(errorData.error || `HTTP ${response.status}: ${response.statusText}`);
    }
    
    return await response.json();
  } catch (error) {
    clearTimeout(timeoutId);
    
    // Handle CORS errors that often indicate timeouts on agent review endpoints
    if (error.message.includes('CORS') || error.message.includes('Access-Control-Allow-Origin')) {
      if (endpoint.includes('/agent/review')) {
        // Don't log CORS errors for agent review - they're expected timeouts
        throw new Error('continue in background');
      } else {
        console.error('API call failed:', error);
        throw new Error('Network connectivity issue - please try again');
      }
    }
    
    // Enhance timeout detection - handle various timeout scenarios
    if (error.name === 'AbortError' || 
        error.message.includes('timeout') ||
        error.message.includes('Failed to fetch') ||
        error.message.includes('ERR_FAILED') ||
        error.message.includes('ERR_NETWORK') ||
        error.message.includes('ERR_INTERNET_DISCONNECTED')) {
      
      // Special handling for agent review timeouts
      if (endpoint.includes('/agent/review')) {
        // Don't log timeouts for agent review - they're expected
        throw new Error('continue in background');
      } else {
        console.error('API call failed:', error);
        throw new Error(`Request timeout after ${timeoutMs/1000}s - please try again later`);
      }
    }
    
    // Log other errors normally
    console.error('API call failed:', error);
    throw error;
  }
};

/**
 * Knowledge Management API calls
 */
const knowledgeManagementAPI = {
  /**
   * Upload files to S3 using presigned URLs
   */
  uploadFiles: async (files, bucketType = 'user_documents', prefix = '') => {
    try {
      // Step 1: Request presigned URLs from the backend
      const filesData = files.map(file => ({
        filename: file.name,
        content_type: file.type,
        file_size: file.size
      }));
      
      const payload = {
        bucket_type: bucketType,
        files: filesData,
        prefix: prefix
      };
      
      const presignedResponse = await apiCall('/knowledge_management/upload', {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      
      // Parse the response to get presigned URLs
      const responseData = typeof presignedResponse.body === 'string' 
        ? JSON.parse(presignedResponse.body) 
        : presignedResponse.body || presignedResponse;
      
      const presignedUrls = responseData.presigned_urls || [];
      
      if (!presignedUrls.length) {
        throw new Error('No presigned URLs received from server');
      }
      
      // Step 2: Upload files directly to S3 using presigned URLs
      const uploadResults = [];
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        const urlData = presignedUrls[i];
        
        if (!urlData || !urlData.success) {
          uploadResults.push({
            success: false,
            filename: file.name,
            error: urlData?.error || 'Failed to get presigned URL'
          });
          continue;
        }
        
        try {
          // Upload directly to S3
          const uploadResponse = await fetch(urlData.presigned_url, {
            method: 'PUT',
            body: file,
            headers: {
              'Content-Type': file.type
            }
          });
          
          if (uploadResponse.ok) {
            uploadResults.push({
              success: true,
              filename: file.name,
              unique_filename: urlData.unique_filename,
              s3_key: urlData.s3_key,
              bucket_name: urlData.bucket_name
            });
          } else {
            uploadResults.push({
              success: false,
              filename: file.name,
              error: `S3 upload failed: ${uploadResponse.status} ${uploadResponse.statusText}`
            });
          }
        } catch (error) {
          uploadResults.push({
            success: false,
            filename: file.name,
            error: `Upload error: ${error.message}`
          });
        }
      }
      
      // Return results directly (not wrapped in body)
      const successfulUploads = uploadResults.filter(r => r.success);
      return {
        message: `${successfulUploads.length} of ${files.length} files uploaded successfully`,
        upload_results: uploadResults,
        uploaded_count: successfulUploads.length,
        total_count: files.length,
        success: successfulUploads.length > 0
      };
      
    } catch (error) {
      console.error('Upload files error:', error);
      // Return consistent error structure
      return {
        message: `Upload failed: ${error.message}`,
        upload_results: [],
        uploaded_count: 0,
        total_count: files.length,
        success: false,
        error: error.message
      };
    }
  },
  
  /**
   * Retrieve file metadata from S3
   */
  retrieveFile: async (s3Key, bucketType = 'user_documents', returnContent = false) => {
    const payload = {
      bucket_type: bucketType,
      s3_key: s3Key,
      return_content: returnContent
    };
    
    return await apiCall('/knowledge_management/retrieve', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
  },
  
  /**
   * Delete files from S3
   */
  deleteFiles: async (s3Keys, bucketType = 'user_documents') => {
    const payload = {
      bucket_type: bucketType,
      s3_keys: s3Keys
    };
    
    return await apiCall('/knowledge_management/delete', {
      method: 'DELETE',
      body: JSON.stringify(payload)
    });
  },
  
  /**
   * Sync Knowledge Base (trigger manual ingestion)
   */
  syncKnowledgeBase: async (dataSource = 'all', action = 'start_sync') => {
    const payload = {
      action: action,
      data_source: dataSource
    };
    
    return await apiCall('/knowledge_management/sync', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
  },
  
  /**
   * Get sync job status
   */
  getSyncJobStatus: async (jobId) => {
    const payload = {
      action: 'get_sync_status',
      job_id: jobId
    };
    
    return await apiCall('/knowledge_management/sync', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
  },
  
  /**
   * List recent sync jobs
   */
  listSyncJobs: async (dataSource = 'all') => {
    const payload = {
      action: 'list_sync_jobs',
      data_source: dataSource
    };
    
    return await apiCall('/knowledge_management/sync', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
  }
};

/**
 * Agent API calls
 */
const agentAPI = {
  /**
   * Review a document for conflicts using AI analysis
   */
  reviewDocument: async (documentS3Key, bucketType = 'user_documents', sessionId = null, userId = null) => {
    const payload = {
      document_s3_key: documentS3Key,
      bucket_type: bucketType,
      session_id: sessionId,
      user_id: userId
    };
    
    return await apiCall('/agent/review', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
  },
  
  /**
   * Download a file from S3 using a presigned URL
   */
  downloadFile: async (s3Key, bucketType = 'agent_processing', originalFilename = null) => {
    try {
      console.log('Download API - Attempting to retrieve file:');
      console.log('  - S3 Key:', s3Key);
      console.log('  - Bucket Type:', bucketType);
      console.log('  - Original Filename:', originalFilename);
      
      // First get file metadata and content
      const retrieveResponse = await knowledgeManagementAPI.retrieveFile(s3Key, bucketType, true);
      
      const responseData = typeof retrieveResponse.body === 'string' 
        ? JSON.parse(retrieveResponse.body) 
        : retrieveResponse.body || retrieveResponse;
      
      if (!responseData.success) {
        // More specific error messages
        if (responseData.error && responseData.error.includes('NoSuchKey')) {
          throw new Error('File not found - may still be processing');
        }
        throw new Error(responseData.error || 'Failed to retrieve file');
      }
      
      if (!responseData.content) {
        throw new Error('File retrieved but no content available');
      }
      
      // Decode base64 content
      const binaryContent = atob(responseData.content);
      const bytes = new Uint8Array(binaryContent.length);
      for (let i = 0; i < binaryContent.length; i++) {
        bytes[i] = binaryContent.charCodeAt(i);
      }
      
      // Create blob and download
      const blob = new Blob([bytes], { type: responseData.content_type || 'application/octet-stream' });
      const url = window.URL.createObjectURL(blob);
      
      // Create download link
      const link = document.createElement('a');
      link.href = url;
      link.download = originalFilename || s3Key.split('/').pop();
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      
      // Clean up
      window.URL.revokeObjectURL(url);
      
      return {
        success: true,
        message: 'File downloaded successfully',
        filename: link.download
      };
      
    } catch (error) {
      console.error('Download file error:', error);
      
      // Handle specific error cases
      let errorMessage = error.message;
      if (error.message.includes('not found') || error.message.includes('NoSuchKey')) {
        errorMessage = 'File not found - document may still be processing';
      } else if (error.message.includes('Failed to fetch')) {
        errorMessage = 'Network error - please check your connection';
      }
      
      return {
        success: false,
        error: errorMessage,
        message: `Download failed: ${errorMessage}`
      };
    }
  }
};

/**
 * File utilities
 */
const fileUtils = {
  /**
   * Convert File object to base64 (DEPRECATED - not needed with presigned URLs)
   * @deprecated Use presigned URLs for direct S3 upload instead
   */
  fileToBase64: (file) => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.readAsDataURL(file);
      reader.onload = () => {
        const base64 = reader.result.split(',')[1];
        resolve(base64);
      };
      reader.onerror = error => reject(error);
    });
  },
  
  /**
   * Prepare files for upload (no base64 conversion needed for presigned URLs)
   */
  prepareFilesForUpload: async (files, options = {}) => {
    const { renameForVendorSubmission = false } = options;
    const preparedFiles = [];
    
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      
      try {
        // Validate the file
        fileUtils.validateFile(file);
        
        let processedFile = file;
        
        // Rename file for vendor submission to meet Converse API requirements
        if (renameForVendorSubmission) {
          const fileExtension = file.name.split('.').pop().toLowerCase();
          const newName = files.length > 1 
            ? `vendor-submission-${i + 1}.${fileExtension}`
            : `vendor-submission.${fileExtension}`;
          
          // Create a new File object with the sanitized name
          processedFile = new File([file], newName, {
            type: file.type,
            lastModified: file.lastModified
          });
          
          console.log(`Renamed file from "${file.name}" to "${newName}" for vendor submission`);
        }
        
        preparedFiles.push(processedFile);
      } catch (error) {
        console.error(`Error preparing file ${file.name}:`, error);
        throw new Error(`Failed to prepare file ${file.name} for upload: ${error.message}`);
      }
    }
    
    return preparedFiles;
  },
  
  /**
   * Validate file for upload
   */
  validateFile: (file) => {
    const maxSize = 10 * 1024 * 1024; // 10MB
    const allowedTypes = [
      'text/plain',
      'application/pdf',
      'application/msword',
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      'image/jpeg',
      'image/png',
      'image/gif'
    ];
    
    if (file.size > maxSize) {
      throw new Error(`File ${file.name} is too large. Maximum size is 10MB.`);
    }
    
    if (!allowedTypes.includes(file.type)) {
      throw new Error(`File ${file.name} has an unsupported file type.`);
    }
    
    return true;
  }
};

/**
 * Session Management API
 * Handles user sessions and session-based file organization
 */
const sessionAPI = {
  /**
   * Create a new session for a user
   */
  createSession: async (userId, cognitoSessionId = null) => {
    try {
      console.log('Creating new session for user:', userId);
      
      const response = await apiCall('/knowledge_management/sessions?action=create', {
        method: 'POST',
        body: JSON.stringify({
          user_id: userId,
          cognito_session_id: cognitoSessionId,
          action: 'create'
        })
      });
      
      console.log('Session created:', response.session);
      return response;
    } catch (error) {
      console.error('Error creating session:', error);
      throw error;
    }
  },

  /**
   * Get all sessions for a user
   */
  getUserSessions: async (userId, filterByResults = false) => {
    try {
      console.log('Getting sessions for user:', userId, 'filterByResults:', filterByResults);
      
      const queryParams = new URLSearchParams({
        action: 'list',
        user_id: userId
      });
      
      if (filterByResults) {
        queryParams.append('filter_by_results', 'true');
      }
      
      const response = await apiCall(`/knowledge_management/sessions?${queryParams.toString()}`);
      
      console.log('Retrieved sessions:', response.sessions?.length || 0);
      return response;
    } catch (error) {
      console.error('Error getting sessions:', error);
      throw error;
    }
  },

  /**
   * Update session title
   */
  updateSessionTitle: async (sessionId, userId, title) => {
    try {
      console.log('Updating session title:', sessionId, title);
      
      const response = await apiCall('/knowledge_management/sessions?action=update', {
        method: 'PUT',
        body: JSON.stringify({
          session_id: sessionId,
          user_id: userId,
          title: title,
          action: 'update'
        })
      });
      
      console.log('Session title updated');
      return response;
    } catch (error) {
      console.error('Error updating session title:', error);
      throw error;
    }
  },

  /**
   * Delete a session
   */
  deleteSession: async (sessionId, userId) => {
    try {
      console.log('Deleting session:', sessionId);
      
      const response = await apiCall(`/knowledge_management/sessions?action=delete&session_id=${sessionId}`, {
        method: 'DELETE',
        body: JSON.stringify({
          session_id: sessionId,
          user_id: userId,
          action: 'delete'
        })
      });
      
      console.log('Session deleted');
      return response;
    } catch (error) {
      console.error('Error deleting session:', error);
      throw error;
    }
  },

  /**
   * Check job status for document processing
   */
  checkJobStatus: async (jobId, userId) => {
    try {
      console.log('Checking job status:', jobId);
      
      const response = await apiCall(`/knowledge_management/sessions?action=job_status&job_id=${jobId}&user_id=${userId}`, {
        method: 'GET'
      });
      
      console.log('Job status response:', response);
      return response;
    } catch (error) {
      console.error('Error checking job status:', error);
      throw error;
    }
  },

  /**
   * Get analysis results for a specific session
   */
  getSessionResults: async (sessionId, userId) => {
    try {
      console.log('Getting session results:', sessionId);
      
      const response = await apiCall(`/knowledge_management/sessions?action=session_results&session_id=${sessionId}&user_id=${userId}`, {
        method: 'GET'
      });
      
      console.log('Session results response:', response);
      return response;
    } catch (error) {
      console.error('Error getting session results:', error);
      throw error;
    }
  }
};

export {
  knowledgeManagementAPI,
  agentAPI,
  sessionAPI,
  fileUtils
}; 