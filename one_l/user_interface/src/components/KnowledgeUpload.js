/**
 * Knowledge Upload Component for One-L Admin Dashboard
 * Handles file uploads to the knowledge bucket for admin users
 */

import React, { useState, useRef } from 'react';
import { knowledgeManagementAPI, fileUtils } from '../services/api';

const KnowledgeUpload = () => {
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState('');
  const [messageType, setMessageType] = useState(''); // 'success' or 'error'
  const fileInputRef = useRef(null);

  const handleFileSelect = (event) => {
    const files = Array.from(event.target.files);
    
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
    
    setSelectedFiles(validFiles);
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
    setMessage('Preparing knowledge base files for upload...');
    setMessageType('');
    
    try {
      // Prepare files for upload
      const preparedFiles = await fileUtils.prepareFilesForUpload(selectedFiles);
      
      setMessage('Uploading files to knowledge base...');
      
      // Upload files to knowledge bucket
      const response = await knowledgeManagementAPI.uploadFiles(
        preparedFiles,
        'knowledge', // Upload to knowledge bucket
        'admin-uploads/'
      );
      
      // Response is now direct (no body parsing needed)
      if (response.uploaded_count > 0) {
        setMessage(`Successfully uploaded ${response.uploaded_count} file(s) to knowledge base! Starting knowledge base sync...`);
        setMessageType('success');
        
        // Automatically trigger knowledge base sync for knowledge bucket
        try {
          const syncResponse = await knowledgeManagementAPI.syncKnowledgeBase('knowledge', 'start_sync');
          
          // Update message to include sync status
          const syncResponseData = typeof syncResponse.body === 'string' 
            ? JSON.parse(syncResponse.body) 
            : syncResponse.body || syncResponse;
          
          if (syncResponseData.successful_count > 0) {
            setMessage(`Successfully uploaded ${response.uploaded_count} file(s) to knowledge base and started sync!`);
          } else {
            setMessage(`Successfully uploaded ${response.uploaded_count} file(s) to knowledge base, but sync failed to start. You may need to manually sync.`);
          }
        } catch (syncError) {
          console.error('Auto-sync error:', syncError);
          setMessage(`Successfully uploaded ${response.uploaded_count} file(s) to knowledge base, but auto-sync failed: ${syncError.message}. You may need to manually sync.`);
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
      console.error('Knowledge upload error:', error);
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
      <h2>Knowledge Base Data Upload</h2>
      <p>Upload documents to the knowledge base for AI processing and retrieval. These files will be embedded and indexed for search.</p>
      
      <div className="form-group">
        <label className="form-label">
          Select Knowledge Base Files
        </label>
        <input
          type="file"
          ref={fileInputRef}
          onChange={handleFileSelect}
          multiple
          accept=".txt,.pdf,.doc,.docx,.jpg,.jpeg,.png,.gif"
          className="form-control"
          disabled={uploading}
        />
        <small className="form-text">
          Supported formats: TXT, PDF, DOC, DOCX, JPG, PNG, GIF. Maximum size: 10MB per file.
        </small>
      </div>

      {selectedFiles.length > 0 && (
        <div className="form-group">
          <label className="form-label">Selected Files ({selectedFiles.length})</label>
          <div className="file-list">
            {selectedFiles.map((file, index) => (
              <div key={index} className="file-item">
                <div className="file-info">
                  <span className="file-name">{file.name}</span>
                  <span className="file-size">({formatFileSize(file.size)})</span>
                </div>
                <button
                  type="button"
                  onClick={() => handleRemoveFile(index)}
                  className="btn-remove"
                  disabled={uploading}
                >
                  ×
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
          className="btn btn-primary"
        >
          {uploading ? 'Uploading...' : 'Upload to Knowledge Base'}
        </button>
      </div>

      {message && (
        <div className={`alert ${messageType === 'error' ? 'alert-error' : 'alert-success'}`}>
          {message}
        </div>
      )}
    </div>
  );
};

export default KnowledgeUpload; 