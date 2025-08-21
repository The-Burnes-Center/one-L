/**
 * File Upload Component for One-L Application
 * Handles multiple file uploads through API Gateway with configurable limits
 */

import React, { useState, useRef } from 'react';
import { knowledgeManagementAPI, fileUtils } from '../services/api';

const FileUpload = ({ 
  title = "File Upload", 
  maxFiles = null, 
  bucketType = "user_documents", 
  prefix = "uploads/", 
  onFilesUploaded = null,
  enableAutoSync = true,
  onSyncComplete = null,
  onSyncStatusChange = null, // ← NEW PROP
  sessionContext = null, // ← NEW: Session context for session-based storage
  acceptedFileTypes = ".txt,.pdf,.doc,.docx,.jpg,.jpeg,.png,.gif", // ← NEW: Accepted file types
  fileTypeDescription = "TXT, PDF, DOC, DOCX, JPG, PNG, GIF (Max 10MB per file)" // ← NEW: File type description
}) => {
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState('');
  const [messageType, setMessageType] = useState(''); // 'success' or 'error'
  const fileInputRef = useRef(null);
  // eslint-disable-next-line no-unused-vars
  const [syncing, setSyncing] = useState(false);
  // eslint-disable-next-line no-unused-vars
  const [syncJobIds, setSyncJobIds] = useState([]);

  const pollSyncCompletion = async (jobIds, maxAttempts = 60) => {
    let attempts = 0;
    const pollInterval = 15000; // 15 seconds
    
    // Report sync started ← NEW
    if (onSyncStatusChange) {
      onSyncStatusChange('syncing', 0, 'Starting knowledge base sync...');
    }
    
    const checkJobsStatus = async () => {
      try {
        attempts++;

        
        // Report progress during polling ← NEW
        const progressPercent = Math.min((attempts / maxAttempts) * 95, 95);
        if (onSyncStatusChange) {
          onSyncStatusChange('syncing', progressPercent, `Syncing knowledge base... (${attempts}/${maxAttempts})`);
        }
        
        // Check status of all jobs
        const jobStatuses = await Promise.all(
          jobIds.map(async (jobId) => {
            try {
              const statusResponse = await knowledgeManagementAPI.getSyncJobStatus(jobId);
              const statusData = typeof statusResponse.body === 'string' 
                ? JSON.parse(statusResponse.body) 
                : statusResponse.body || statusResponse;
              
              return {
                jobId,
                status: statusData.status,
                success: true
              };
            } catch (error) {

              return {
                jobId,
                status: 'FAILED',
                success: false,
                error: error.message
              };
            }
          })
        );
        
        // Check if all jobs are completed
        const completedJobs = jobStatuses.filter(job => 
          job.status === 'COMPLETE' || job.status === 'COMPLETED'
        );
        const failedJobs = jobStatuses.filter(job => 
          job.status === 'FAILED' || job.status === 'STOPPED' || !job.success
        );
        const inProgressJobs = jobStatuses.filter(job => 
          job.status === 'IN_PROGRESS' || job.status === 'STARTING'
        );
        

        
        // All jobs completed successfully (around line 76)
        if (completedJobs.length === jobIds.length) {
          setSyncing(false);
          setMessage('Files uploaded successfully!');
          setMessageType('success');
          
          // Report sync completed ← NEW
          if (onSyncStatusChange) {
            onSyncStatusChange('ready', 100, 'Knowledge base sync completed successfully!');
          }
          
          if (onSyncComplete) {
            onSyncComplete(true, 'Knowledge base sync completed successfully! AI review is now available.');
          }
          return;
        }
        
        // Some jobs failed, but we'll continue waiting for others
        if (failedJobs.length > 0 && inProgressJobs.length === 0) {
          setSyncing(false);
          const errorMessage = `File processing failed. Please try uploading again.`;
          setMessage(errorMessage);
          setMessageType('error');
          if (onSyncComplete) {
            onSyncComplete(false, errorMessage);
          }
          return;
        }
        
        // Still have jobs in progress, continue polling
        if (attempts < maxAttempts && inProgressJobs.length > 0) {
          setTimeout(checkJobsStatus, pollInterval);
        } else if (attempts >= maxAttempts) {
          // Timeout - assume sync failed
          setSyncing(false);
          const timeoutMessage = 'File processing timed out. Please try uploading again.';
          setMessage(timeoutMessage);
          setMessageType('error');
          if (onSyncComplete) {
            onSyncComplete(false, timeoutMessage);
          }
        }
        
      } catch (error) {

        setSyncing(false);
        const errorMessage = `File processing error. Please try uploading again.`;
        setMessage(errorMessage);
        setMessageType('error');
        if (onSyncComplete) {
          onSyncComplete(false, errorMessage);
        }
      }
    };
    
    // Start polling
    checkJobsStatus();
  };

  const handleFileSelect = (event) => {
    const files = Array.from(event.target.files);
    
    // Check file count limit
    if (maxFiles && files.length > maxFiles) {
      setMessage(`You can only upload a maximum of ${maxFiles} file(s) for ${title}.`);
      setMessageType('error');
      return;
    }
    
    // Check if adding these files would exceed the limit
    if (maxFiles && (selectedFiles.length + files.length) > maxFiles) {
      const remainingSlots = maxFiles - selectedFiles.length;
      setMessage(`You can only add ${remainingSlots} more file(s). Current limit: ${maxFiles} files for ${title}.`);
      setMessageType('error');
      return;
    }
    
    // Validate files
    const validFiles = [];
    const errors = [];
    
    files.forEach(file => {
      try {
        fileUtils.validateFile(file);
        validFiles.push(file);
      } catch (error) {
        errors.push(error.message);
      }
    });
    
    if (errors.length > 0) {
      setMessage(errors.join('\n'));
      setMessageType('error');
      return;
    }
    
    // Add to existing files or replace based on limit
    if (maxFiles && maxFiles === 1) {
      setSelectedFiles(validFiles);
    } else {
      setSelectedFiles(prevFiles => [...prevFiles, ...validFiles]);
    }
    
    setMessage('');
    setMessageType('');
  };

  const handleUpload = async () => {
    if (selectedFiles.length === 0) {
      setMessage('Please select files to upload.');
      setMessageType('error');
      return;
    }
    
    setUploading(true);
    setMessage('Preparing files for upload...');
    setMessageType('');
    
    try {
      // Prepare files for upload
      const preparedFiles = await fileUtils.prepareFilesForUpload(selectedFiles);
      
      setMessage('Uploading files...');
      
      // Upload files with session context
      const response = await knowledgeManagementAPI.uploadFiles(
        preparedFiles,
        bucketType,
        prefix,
        sessionContext // ← NEW: Pass session context for session-based storage
      );
      
      // Response is now direct (no body parsing needed)
      if (response.uploaded_count > 0) {
        let baseMessage = 'Files uploaded successfully!';
        setMessage(baseMessage);
        setMessageType('success');
        
        // Extract uploaded files info for parent callback
        const successfulUploads = response.upload_results
          .filter(result => result.success)
          .map(result => ({
            s3_key: result.s3_key,
            filename: result.filename,
            unique_filename: result.unique_filename,
            bucket_name: result.bucket_name,
            type: 'reference_document'
          }));
        
        // Report uploaded files to parent
        if (onFilesUploaded) {
          onFilesUploaded(successfulUploads);
        }
        
        // Automatically trigger knowledge base sync if enabled
        if (enableAutoSync && bucketType === 'user_documents') {
          setMessage(baseMessage);
          setSyncing(true);  // Start syncing state
          
          try {
            const syncResponse = await knowledgeManagementAPI.syncKnowledgeBase('user_documents', 'start_sync');
            
            const syncResponseData = typeof syncResponse.body === 'string' 
              ? JSON.parse(syncResponse.body) 
              : syncResponse.body || syncResponse;
            
            if (syncResponseData.successful_count > 0) {
              // Extract job IDs for polling
              const jobIds = syncResponseData.sync_jobs
                ?.filter(job => job.success)
                ?.map(job => job.job_id) || [];
              
              setSyncJobIds(jobIds);
              
              if (jobIds.length > 0) {
                setMessage('Files uploaded successfully!');
                setMessageType('success');
                
                // Start polling for completion using our new function
                pollSyncCompletion(jobIds);
              } else {
                setSyncing(false);
                const noJobsMessage = 'Files uploaded successfully.';
                setMessage(noJobsMessage);
                if (onSyncComplete) {
                  onSyncComplete(false, noJobsMessage);
                }
              }
            } else {
              setSyncing(false);
              const syncFailMessage = 'Files uploaded successfully!';
              setMessage(syncFailMessage);
              if (onSyncComplete) {
                onSyncComplete(false, syncFailMessage);
              }
            }
          } catch (syncError) {
            setSyncing(false);
            const syncErrorMessage = 'Files uploaded successfully!';
            setMessage(syncErrorMessage);
            if (onSyncComplete) {
              onSyncComplete(false, syncErrorMessage);
            }
          }
        }
        
        // Keep selectedFiles to maintain preview after upload
      } else {
        setMessage(response.message || 'Upload failed. Please try again.');
        setMessageType('error');
      }
      
    } catch (error) {
      setMessage(`Upload failed: ${error.message}`);
      setMessageType('error');
    } finally {
      setUploading(false);
    }
  };

  const handleRemoveFile = (index) => {
    const newFiles = selectedFiles.filter((_, i) => i !== index);
    setSelectedFiles(newFiles);
    
    if (newFiles.length === 0 && fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  // eslint-disable-next-line no-unused-vars
  const formatFileSize = (bytes) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  return (
    <div className="card">
      <h2>{title}</h2>
      
      <div className="form-group">
        <label className="form-label">
          Select Files {maxFiles && `(Maximum: ${maxFiles} file${maxFiles > 1 ? 's' : ''})`}
        </label>
        <input
          ref={fileInputRef}
          type="file"
          multiple={!maxFiles || maxFiles > 1}
          onChange={handleFileSelect}
          className="form-control"
          accept={acceptedFileTypes}
          disabled={uploading || (maxFiles && selectedFiles.length >= maxFiles)}
        />
        <small style={{ color: '#666', fontSize: '14px' }}>
          {fileTypeDescription}
        </small>
      </div>

      {selectedFiles.length > 0 && (
        <div className="form-group">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {selectedFiles.map((file, index) => (
              <div key={index} style={{ 
                display: 'flex', 
                alignItems: 'center', 
                padding: '12px 16px',
                backgroundColor: '#f8f9fa',
                border: '1px solid #dee2e6',
                borderRadius: '12px',
                color: '#333',
                gap: '12px',
                maxWidth: '400px'
              }}>
                <div style={{
                  width: '40px',
                  height: '40px',
                  backgroundColor: '#3498db',
                  borderRadius: '8px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: '20px'
                }}>
                  📄
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: '500', fontSize: '14px' }}>
                    {file.name}
                  </div>
                  <div style={{ fontSize: '12px', color: '#666' }}>
                    Document
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => handleRemoveFile(index)}
                  disabled={uploading || messageType === 'success'}
                  style={{
                    background: 'transparent',
                    border: 'none',
                    color: '#666',
                    cursor: (uploading || messageType === 'success') ? 'not-allowed' : 'pointer',
                    fontSize: '18px',
                    padding: '4px',
                    borderRadius: '4px',
                    opacity: messageType === 'success' ? 0.5 : 1
                  }}
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="form-group">
        <button
          onClick={handleUpload}
          disabled={uploading || selectedFiles.length === 0 || messageType === 'success'}
          className="btn btn-primary"
          style={{ opacity: (uploading || messageType === 'success') ? 0.6 : 1 }}
        >
          {uploading ? 'Uploading...' : messageType === 'success' ? 'Files Uploaded' : `Upload ${title}`}
        </button>
      </div>

      {message && (
        <div className={`alert ${messageType === 'success' ? 'alert-success' : 'alert-error'}`}>
          {message.split('\n').map((line, index) => (
            <div key={index}>{line}</div>
          ))}
        </div>
      )}
    </div>
  );
};

export default FileUpload;