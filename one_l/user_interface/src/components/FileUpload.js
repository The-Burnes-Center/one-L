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
  enableAutoSync = true // New prop to control auto-sync behavior
}) => {
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState('');
  const [messageType, setMessageType] = useState(''); // 'success' or 'error'
  const fileInputRef = useRef(null);

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
      
      // Upload files
      const response = await knowledgeManagementAPI.uploadFiles(
        preparedFiles,
        bucketType,
        prefix
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
          
          try {
            const syncResponse = await knowledgeManagementAPI.syncKnowledgeBase('user_documents', 'start_sync');
            
            // Update message to include sync status
            const syncResponseData = typeof syncResponse.body === 'string' 
              ? JSON.parse(syncResponse.body) 
              : syncResponse.body || syncResponse;
            
            if (syncResponseData.successful_count > 0) {
              setMessage(`Successfully uploaded ${response.uploaded_count} file(s) and started knowledge base sync!`);
            } else {
              setMessage(`Successfully uploaded ${response.uploaded_count} file(s), but sync failed to start. You may need to manually sync.`);
            }
          } catch (syncError) {
            console.error('Auto-sync error:', syncError);
            setMessage(`Successfully uploaded ${response.uploaded_count} file(s), but auto-sync failed: ${syncError.message}. You may need to manually sync.`);
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
          disabled={uploading || selectedFiles.length === 0}
          className="btn"
          style={{ opacity: uploading ? 0.6 : 1 }}
        >
          {uploading ? 'Uploading...' : `Upload ${title} (${selectedFiles.length} file${selectedFiles.length !== 1 ? 's' : ''})`}
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