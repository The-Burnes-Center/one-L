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
   * Upload files to S3
   */
  uploadFiles: async (files, bucketType = 'user_documents', prefix = '') => {
    const filesData = files.map(file => ({
      filename: file.name,
      content: file.base64Content,
      content_type: file.type
    }));
    
    const payload = {
      bucket_type: bucketType,
      files: filesData,
      prefix: prefix
    };
    
    return await apiCall('/knowledge_management/upload', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
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
   * Convert File object to base64
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
   * Prepare files for upload
   */
  prepareFilesForUpload: async (files) => {
    const preparedFiles = [];
    
    for (const file of files) {
      try {
        const base64Content = await fileUtils.fileToBase64(file);
        preparedFiles.push({
          name: file.name,
          type: file.type,
          size: file.size,
          base64Content: base64Content
        });
      } catch (error) {
        console.error(`Error preparing file ${file.name}:`, error);
        throw new Error(`Failed to prepare file ${file.name} for upload`);
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