/**
 * Knowledge Upload Component for One-L Admin Dashboard
 * Handles file uploads to the knowledge bucket for admin users
 */

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { knowledgeManagementAPI, fileUtils } from '../services/api';

const KnowledgeUpload = () => {
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState('');
  const [messageType, setMessageType] = useState(''); // 'success' or 'error'
  const [knowledgeFiles, setKnowledgeFiles] = useState([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [filesError, setFilesError] = useState('');
  const [listContinuationToken, setListContinuationToken] = useState(null);
  const [prefixFilter, setPrefixFilter] = useState('');
  const [filesMeta, setFilesMeta] = useState({
    bucketName: '',
    prefix: '',
    keyCount: 0
  });
  const [hasMore, setHasMore] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState(null);
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

  const fetchKnowledgeFiles = useCallback(async ({ continuationToken = null, append = false, prefixOverride } = {}) => {
    // Avoid multiple parallel load-more calls
    setFilesLoading(true);
    if (!append) {
      setFilesError('');
      setListContinuationToken(null);
    }

    const effectivePrefix = prefixOverride !== undefined ? prefixOverride : prefixFilter;

    try {
      const response = await knowledgeManagementAPI.listFiles('knowledge', {
        prefix: effectivePrefix || undefined,
        maxKeys: 100,
        continuationToken
      });

      if (!response?.success) {
        const errorMessage = response?.error || 'Failed to retrieve knowledge base files.';
        setFilesError(errorMessage);
        if (!append) {
          setKnowledgeFiles([]);
          setFilesMeta({
            bucketName: '',
            prefix: effectivePrefix || '',
            keyCount: 0
          });
          setHasMore(false);
        }
        return;
      }

      const files = response.files || [];
      setKnowledgeFiles(prev =>
        append ? [...prev, ...files] : files
      );
      setListContinuationToken(response.next_continuation_token || null);
      setHasMore(Boolean(response.is_truncated));
      setFilesMeta({
        bucketName: response.bucket_name || '',
        prefix: response.prefix || effectivePrefix || '',
        keyCount: response.key_count ?? files.length
      });
      setLastRefreshed(new Date());
    } catch (error) {
      setFilesError(error?.message || 'Failed to retrieve knowledge base files.');
      if (!append) {
        setKnowledgeFiles([]);
        setFilesMeta({
          bucketName: '',
          prefix: effectivePrefix || '',
          keyCount: 0
        });
        setHasMore(false);
      }
    } finally {
      setFilesLoading(false);
    }
  }, [prefixFilter]);

  useEffect(() => {
    fetchKnowledgeFiles({ prefixOverride: prefixFilter });
  }, [fetchKnowledgeFiles, prefixFilter]);

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

        // Refresh knowledge files list
        fetchKnowledgeFiles({ prefixOverride: prefixFilter });
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

  const handleRefreshKnowledgeList = () => {
    setListContinuationToken(null);
    fetchKnowledgeFiles({ prefixOverride: prefixFilter });
  };

  const handleLoadMoreFiles = () => {
    if (listContinuationToken) {
      fetchKnowledgeFiles({ continuationToken: listContinuationToken, append: true });
    }
  };

  const formatFileSize = (bytes) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const formatTimestamp = (timestamp) => {
    if (!timestamp) return '—';
    try {
      return new Date(timestamp).toLocaleString();
    } catch (error) {
      return timestamp;
    }
  };

  const renderMetaValue = (value, fallback = '—') => {
    if (value === null || value === undefined || value === '') {
      return fallback;
    }
    return value;
  };

  return (
    <div>
      {/* Upload Section */}
      <div className="card" style={{ marginBottom: '2rem' }}>
        <div style={{ marginBottom: '1.5rem' }}>
          <h2 style={{ marginBottom: '0.5rem' }}>Upload Documents</h2>
          <p style={{ color: '#666', margin: 0 }}>
            Upload documents to the knowledge base for AI processing and retrieval. Files will be embedded and indexed for search.
          </p>
        </div>
        
        <div className="form-group" style={{ marginBottom: '1rem' }}>
          <label className="form-label">
            Select Files
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
          <div className="form-group" style={{ marginBottom: '1rem' }}>
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
          <div className={`alert ${messageType === 'error' ? 'alert-error' : 'alert-success'}`} style={{ marginTop: '1rem' }}>
            {message}
          </div>
        )}
      </div>

      {/* Knowledge Base Contents Section */}
      <div className="card">
        <div style={{ 
          display: 'flex', 
          justifyContent: 'space-between', 
          alignItems: 'flex-start',
          marginBottom: '1.5rem',
          flexWrap: 'wrap',
          gap: '1rem'
        }}>
          <div>
            <h2 style={{ marginBottom: '0.5rem' }}>Knowledge Base Contents</h2>
            <p style={{ color: '#666', margin: 0 }}>
              Browse existing documents stored in the knowledge bucket.
            </p>
          </div>
          <div style={{ 
            display: 'flex', 
            gap: '0.75rem', 
            alignItems: 'flex-end',
            flexWrap: 'wrap'
          }}>
            <div style={{ minWidth: '150px' }}>
              <label className="form-label" htmlFor="kb-prefix-filter" style={{ marginBottom: '0.25rem', display: 'block' }}>
                Filter
              </label>
              <select
                id="kb-prefix-filter"
                className="form-control"
                value={prefixFilter}
                onChange={(event) => {
                  const newPrefix = event.target.value;
                  setListContinuationToken(null);
                  setPrefixFilter(newPrefix);
                }}
                disabled={filesLoading}
                style={{ width: '100%' }}
              >
                <option value="">All files</option>
                <option value="admin-uploads/">Admin uploads</option>
              </select>
            </div>
            <button
              type="button"
              onClick={handleRefreshKnowledgeList}
              className="btn btn-secondary"
              disabled={filesLoading}
            >
              {filesLoading ? 'Refreshing...' : 'Refresh'}
            </button>
          </div>
        </div>

        <div style={{ 
          display: 'flex', 
          gap: '1.5rem', 
          marginBottom: '1.5rem',
          paddingBottom: '1rem',
          borderBottom: '1px solid #e0e0e0',
          flexWrap: 'wrap'
        }}>
          {filesMeta.bucketName && (
            <div>
              <span style={{ color: '#666', fontSize: '0.875rem', display: 'block', marginBottom: '0.25rem' }}>Bucket</span>
              <span style={{ fontWeight: '500', color: '#333' }}>{renderMetaValue(filesMeta.bucketName)}</span>
            </div>
          )}
          <div>
            <span style={{ color: '#666', fontSize: '0.875rem', display: 'block', marginBottom: '0.25rem' }}>Showing</span>
            <span style={{ fontWeight: '500', color: '#333' }}>
              {knowledgeFiles.length} file{knowledgeFiles.length === 1 ? '' : 's'}
              {hasMore ? '+' : ''}
            </span>
          </div>
          {filesMeta.prefix && (
            <div>
              <span style={{ color: '#666', fontSize: '0.875rem', display: 'block', marginBottom: '0.25rem' }}>Prefix</span>
              <span style={{ 
                fontWeight: '500', 
                color: '#333',
                backgroundColor: '#f0f0f0',
                padding: '0.25rem 0.5rem',
                borderRadius: '4px',
                fontSize: '0.875rem'
              }}>{filesMeta.prefix}</span>
            </div>
          )}
          {lastRefreshed && (
            <div>
              <span style={{ color: '#666', fontSize: '0.875rem', display: 'block', marginBottom: '0.25rem' }}>Updated</span>
              <span style={{ fontWeight: '500', color: '#333' }}>
                {lastRefreshed.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}
              </span>
            </div>
          )}
        </div>

        {filesError && (
          <div className="alert alert-error" style={{ marginBottom: '1rem' }}>
            {filesError}
          </div>
        )}

        {!filesError && knowledgeFiles.length === 0 && !filesLoading && (
          <div style={{ 
            padding: '3rem 1rem', 
            textAlign: 'center',
            color: '#666'
          }}>
            <p style={{ margin: 0, fontWeight: '500', marginBottom: '0.5rem' }}>No knowledge base documents found.</p>
            <p style={{ margin: 0, fontSize: '0.875rem' }}>Upload files or adjust the filter to see existing documents.</p>
          </div>
        )}

        {knowledgeFiles.length > 0 && (
          <div style={{ position: 'relative' }}>
            <div className={`knowledge-files-table ${filesLoading ? 'is-loading' : ''}`}>
              <div className="table-header">
                <span>File Name</span>
                <span>Size</span>
                <span>Last Modified</span>
              </div>
              <div className="table-body">
                {knowledgeFiles.map((file) => {
                  const fileName = file?.s3_key ? file.s3_key.split('/').pop() : 'Unknown file';
                  return (
                    <div key={file.s3_key} className="table-row">
                      <span title={file.s3_key}>{fileName || file.s3_key}</span>
                      <span>{typeof file.size === 'number' ? formatFileSize(file.size) : '—'}</span>
                      <span>{formatTimestamp(file.last_modified)}</span>
                    </div>
                  );
                })}
              </div>
            </div>
            {filesLoading && (
              <div className="table-loading-overlay">
                <div className="loading-spinner" />
                <span>Loading documents...</span>
              </div>
            )}
          </div>
        )}

        {listContinuationToken && (
          <div className="form-group" style={{ marginTop: '1.5rem', textAlign: 'center' }}>
            <button
              type="button"
              className="btn btn-outline"
              onClick={handleLoadMoreFiles}
              disabled={filesLoading}
            >
              {filesLoading ? 'Loading...' : 'Load more'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

export default KnowledgeUpload; 