/**
 * Vendor Submission Component for One-L Application
 * Handles vendor document upload to agent_processing bucket
 */

import React, { useState, useRef } from 'react';
import { knowledgeManagementAPI, fileUtils } from '../services/api';

const VendorSubmission = ({ onFilesUploaded }) => {
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState('');
  const [messageType, setMessageType] = useState(''); // 'success' or 'error'
  const fileInputRef = useRef(null);
  
  const maxFiles = 2;
  const bucketType = "agent_processing";
  const prefix = "vendor-submissions/";

  const handleFileSelect = (event) => {
    const files = Array.from(event.target.files);
    
    // Check file count limit
    if (files.length > maxFiles) {
      setMessage(`You can only upload a maximum of ${maxFiles} vendor submission files.`);
      setMessageType('error');
      return;
    }
    
    // Check if adding these files would exceed the limit
    if ((selectedFiles.length + files.length) > maxFiles) {
      const remainingSlots = maxFiles - selectedFiles.length;
      setMessage(`You can only add ${remainingSlots} more file(s). Current limit: ${maxFiles} files.`);
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
    setMessage('Preparing vendor submission files for upload...');
    setMessageType('');
    
    try {
      // Prepare files for upload with vendor submission renaming
      const preparedFiles = await fileUtils.prepareFilesForUpload(selectedFiles, {
        renameForVendorSubmission: true
      });
      
      setMessage('Uploading vendor submission files...');
      
      // Upload files to agent_processing bucket (no KB sync)
      const response = await knowledgeManagementAPI.uploadFiles(
        preparedFiles,
        bucketType,
        prefix
      );
      
      if (response.uploaded_count > 0) {
        setMessage(`Successfully uploaded ${response.uploaded_count} vendor submission file(s)! Ready for AI review.`);
        setMessageType('success');
        
        // Extract uploaded files info
        const successfulUploads = response.upload_results
          .filter(result => result.success)
          .map(result => ({
            s3_key: result.s3_key,
            filename: result.filename,
            unique_filename: result.unique_filename,
            bucket_name: result.bucket_name,
            type: 'vendor_submission'
          }));
        
        // Report uploaded files to parent component
        if (onFilesUploaded) {
          onFilesUploaded(successfulUploads);
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
      <h2>Vendor Submission</h2>
      <p>Upload vendor submission documents for AI-powered conflict review (maximum 2 files)</p>
      
      <div className="form-group">
        <label className="form-label">
          Select Vendor Submission Files (Maximum: {maxFiles} files)
        </label>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          onChange={handleFileSelect}
          className="form-control"
          accept=".txt,.pdf,.doc,.docx,.jpg,.jpeg,.png,.gif"
          disabled={uploading || selectedFiles.length >= maxFiles}
        />
        <small style={{ color: '#666', fontSize: '14px' }}>
          Supported formats: TXT, PDF, DOC, DOCX, JPG, PNG, GIF (Max 10MB per file)
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
          {uploading ? 'Uploading...' : `Upload Vendor Submission (${selectedFiles.length} file${selectedFiles.length !== 1 ? 's' : ''})`}
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

export default VendorSubmission; 