/**
 * Vendor Submission Component for One-L Application
 * Handles vendor document upload to agent_processing bucket
 */

import React, { useState, useRef, useEffect } from 'react';
import { knowledgeManagementAPI, fileUtils } from '../services/api';

const VendorSubmission = ({ onFilesUploaded, previouslyUploadedFiles = [], sessionContext = null }) => {
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState('');
  const [messageType, setMessageType] = useState(''); // 'success' or 'error'
  const fileInputRef = useRef(null);
  
  // eslint-disable-next-line no-unused-vars
  const maxFiles = 1;
  const bucketType = "agent_processing";
  const prefix = "vendor-submissions/";

  // Clear selected files when session changes
  useEffect(() => {
    if (sessionContext?.session_id) {
      setSelectedFiles([]);
      setMessage('');
      setMessageType('');
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  }, [sessionContext?.session_id]);

  const handleFileSelect = (event) => {
    const files = Array.from(event.target.files);
    
    if (files.length === 0) return;
    
    const file = files[0]; // Only take the first file
    
    // Validate file
    try {
      fileUtils.validateFile(file);
      setSelectedFiles([file]); // Replace any existing file
      setMessage('');
      setMessageType('');
    } catch (error) {
      setMessage(error.message);
      setMessageType('error');
    }
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
        setMessage('File uploaded successfully!');
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




//DOC, DOCX, PDF (Max 10MB per file)
  return (
    <div className="card">
      <h2>Vendor Submission</h2>
      
      <div className="form-group">
        <label className="form-label">
          Select Document
        </label>
        <input
          ref={fileInputRef}
          type="file"
          onChange={handleFileSelect}
          className="form-control"
          accept=".doc,.docx,.pdf"
          description="DOC, DOCX, PDF (Max 10MB per file)"
          disabled={uploading}
        />
      </div>

      {/* Previously uploaded vendor file */}
      {previouslyUploadedFiles && previouslyUploadedFiles.length > 0 && (
        <div className="form-group">
          <label className="form-label" style={{ fontSize: '14px', fontWeight: '500', color: '#666', marginBottom: '8px' }}>
            Previously Uploaded File
          </label>
          <div style={{ 
            display: 'flex', 
            alignItems: 'center', 
            padding: '12px 16px',
            backgroundColor: '#d4edda',
            border: '1px solid #c3e6cb',
            borderRadius: '12px',
            color: '#333',
            gap: '12px',
            maxWidth: '400px'
          }}>
            <div style={{
              width: '40px',
              height: '40px',
              backgroundColor: '#28a745',
              borderRadius: '8px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '20px'
            }}>
              ✓
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: '500', fontSize: '14px' }}>
                {previouslyUploadedFiles[0].filename || previouslyUploadedFiles[0].name}
              </div>
              <div style={{ fontSize: '12px', color: '#666' }}>
                Already uploaded
              </div>
            </div>
          </div>
        </div>
      )}

      {/* File selected for upload */}
      {selectedFiles.length > 0 && (
        <div className="form-group">
          <label className="form-label" style={{ fontSize: '14px', fontWeight: '500', color: '#666', marginBottom: '8px' }}>
            File to Upload
          </label>
          <div style={{ 
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
              backgroundColor: '#ff4757',
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
                {selectedFiles[0].name}
              </div>
              <div style={{ fontSize: '12px', color: '#666' }}>
                Document
              </div>
            </div>
            <button
              type="button"
              onClick={() => setSelectedFiles([])}
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
        </div>
      )}

      <div className="form-group">
        <button
          onClick={handleUpload}
          disabled={uploading || selectedFiles.length === 0 || messageType === 'success'}
          className="btn"
          style={{ opacity: (uploading || messageType === 'success') ? 0.6 : 1 }}
        >
          {uploading ? 'Uploading...' : messageType === 'success' ? 'Document Uploaded' : 'Upload Vendor Document'}
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