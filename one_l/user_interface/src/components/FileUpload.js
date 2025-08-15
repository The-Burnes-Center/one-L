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
  description = "Upload files for processing",
  onFilesUploaded = null,
  enableAutoSync = true,
  onSyncComplete = null,
  onSyncStatusChange = null, // ← NEW PROP
  sessionContext = null // ← NEW: Session context for session-based storage
}) => {
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState('');
  const [messageType, setMessageType] = useState(''); // 'success' or 'error'
  const fileInputRef = useRef(null);
  const [syncing, setSyncing] = useState(false);
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
        console.log(`Sync polling attempt ${attempts}/${maxAttempts}`);
        
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
              console.error(`Error checking job ${jobId}:`, error);
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
        
        console.log(`Sync status: ${completedJobs.length} completed, ${failedJobs.length} failed, ${inProgressJobs.length} in progress`);
        
        // All jobs completed successfully (around line 76)
        if (completedJobs.length === jobIds.length) {
          setSyncing(false);
          setMessage('Files uploaded and knowledge base sync completed successfully!');
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
          const errorMessage = `Knowledge base sync failed for ${failedJobs.length} job(s). AI review may not include latest documents.`;
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
          const timeoutMessage = 'Knowledge base sync timed out. AI review may not include latest documents.';
          setMessage(timeoutMessage);
          setMessageType('error');
          if (onSyncComplete) {
            onSyncComplete(false, timeoutMessage);
          }
        }
        
      } catch (error) {
        console.error('Error during sync polling:', error);
        setSyncing(false);
        const errorMessage = `Sync monitoring failed: ${error.message}`;
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
        let baseMessage = `Successfully uploaded ${response.uploaded_count} ${title.toLowerCase()} file(s)!`;
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
          setMessage(`${baseMessage} Starting knowledge base sync...`);
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
                setMessage(`Successfully uploaded ${response.uploaded_count} file(s). Syncing knowledge base... (this may take several minutes)`);
                setMessageType('success');
                
                // Start polling for completion using our new function
                pollSyncCompletion(jobIds);
              } else {
                setSyncing(false);
                const noJobsMessage = 'Files uploaded but no sync jobs were started.';
                setMessage(noJobsMessage);
                if (onSyncComplete) {
                  onSyncComplete(false, noJobsMessage);
                }
              }
            } else {
              setSyncing(false);
              const syncFailMessage = `Successfully uploaded ${response.uploaded_count} file(s), but sync failed to start.`;
              setMessage(syncFailMessage);
              if (onSyncComplete) {
                onSyncComplete(false, syncFailMessage);
              }
            }
          } catch (syncError) {
            setSyncing(false);
            const syncErrorMessage = `Successfully uploaded ${response.uploaded_count} file(s), but auto-sync failed: ${syncError.message}`;
            setMessage(syncErrorMessage);
            if (onSyncComplete) {
              onSyncComplete(false, syncErrorMessage);
            }
          }
        }
        
        // Clear selection
        setSelectedFiles([]);
        if (fileInputRef.current) {
          fileInputRef.current.value = '';
        }
      } else {
        setMessage(response.message || 'Upload failed. Please try again.');
        setMessageType('error');
      }
      
    } catch (error) {
      console.error('Upload error:', error);
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
      <p>{description}</p>
      
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
          accept=".txt,.pdf,.doc,.docx,.jpg,.jpeg,.png,.gif"
          disabled={uploading || (maxFiles && selectedFiles.length >= maxFiles)}
        />
        <small style={{ color: '#666', fontSize: '14px' }}>
          Supported formats: TXT, PDF, DOC, DOCX, JPG, PNG, GIF (Max 10MB per file)
          {maxFiles && ` | File limit: ${maxFiles} file${maxFiles > 1 ? 's' : ''}`}
        </small>
      </div>

      {selectedFiles.length > 0 && (
        <div className="form-group">
          <label className="form-label">Selected Files ({selectedFiles.length})</label>
          <div style={{ border: '1px solid #ddd', borderRadius: '4px', padding: '12px' }}>
            {selectedFiles.map((file, index) => (
              <div key={index} style={{ 
                display: 'flex', 
                justifyContent: 'space-between', 
                alignItems: 'center',
                padding: '8px 0',
                borderBottom: index < selectedFiles.length - 1 ? '1px solid #eee' : 'none'
              }}>
                <div>
                  <div style={{ fontWeight: '500' }}>{file.name}</div>
                  <div style={{ fontSize: '12px', color: '#666' }}>
                    {formatFileSize(file.size)} • {file.type}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => handleRemoveFile(index)}
                  disabled={uploading}
                  style={{
                    background: '#dc3545',
                    color: 'white',
                    border: 'none',
                    padding: '4px 8px',
                    borderRadius: '4px',
                    cursor: 'pointer',
                    fontSize: '12px'
                  }}
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="form-group">
        <button
          onClick={handleUpload}
          disabled={uploading || syncing || selectedFiles.length === 0}
          className="btn btn-primary"
        >
          {uploading ? 'Uploading...' : syncing ? 'Syncing Knowledge Base...' : `Upload ${title}`}
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