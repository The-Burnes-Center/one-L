/**
 * API Service for One-L Application
 * Handles all API calls to the backend through API Gateway
 */

import { getApiGatewayUrl } from '../utils/config';

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
      ...options.headers
    }
  };
  
  const finalOptions = { ...defaultOptions, ...options };
  
  try {
    const response = await fetch(url, finalOptions);
    
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${response.status}: ${response.statusText}`);
    }
    
    return await response.json();
  } catch (error) {
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
    
    // Return results in the same format as before
    const successfulUploads = uploadResults.filter(r => r.success);
    return {
      body: JSON.stringify({
        message: `${successfulUploads.length} of ${files.length} files uploaded successfully`,
        upload_results: uploadResults,
        uploaded_count: successfulUploads.length
      })
    };
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
  prepareFilesForUpload: async (files) => {
    const preparedFiles = [];
    
    for (const file of files) {
      try {
        // Validate the file
        fileUtils.validateFile(file);
        
        // No base64 conversion needed - just pass the file object
        preparedFiles.push(file);
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

export {
  knowledgeManagementAPI,
  fileUtils
}; 