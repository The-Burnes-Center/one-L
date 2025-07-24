/**
 * Main App Component for One-L Application
 */

import React, { useState, useEffect } from 'react';
import FileUpload from './components/FileUpload';
import VendorSubmission from './components/VendorSubmission';
import Sidebar from './components/Sidebar';
import AdminDashboard from './components/AdminDashboard';
import { isConfigValid, loadConfig } from './utils/config';
import { agentAPI } from './services/api';

const App = () => {
  const [configLoaded, setConfigLoaded] = useState(false);
  const [configError, setConfigError] = useState('');
  const [activeSection, setActiveSection] = useState('main');
  const [activeTab, setActiveTab] = useState('data');
  
  // Workflow state
  const [uploadedFiles, setUploadedFiles] = useState([]);
  const [generating, setGenerating] = useState(false);
  const [workflowMessage, setWorkflowMessage] = useState('');
  const [workflowMessageType, setWorkflowMessageType] = useState('');
  const [redlinedDocuments, setRedlinedDocuments] = useState([]);

  useEffect(() => {
    const initializeApp = async () => {
      try {
        await loadConfig();
        const isValid = await isConfigValid();
        
        if (!isValid) {
          setConfigError('Configuration is incomplete. Please check your deployment.');
          return;
        }
        
        setConfigLoaded(true);
      } catch (error) {
        console.error('Failed to initialize app:', error);
        setConfigError('Failed to load application configuration.');
      }
    };

    initializeApp();
  }, []);

  const handleFilesUploaded = (files) => {
    setUploadedFiles(prevFiles => {
      // Remove any existing files of the same type and add new ones
      const existingOtherType = prevFiles.filter(f => f.type !== files[0].type);
      return [...existingOtherType, ...files];
    });
  };

  const handleGenerateRedline = async () => {
    const vendorFiles = uploadedFiles.filter(f => f.type === 'vendor_submission');
    const referenceFiles = uploadedFiles.filter(f => f.type === 'reference_document');
    
    if (vendorFiles.length === 0) {
      setWorkflowMessage('Please upload vendor submission documents first.');
      setWorkflowMessageType('error');
      return;
    }
    
    if (referenceFiles.length === 0) {
      setWorkflowMessage('Please upload reference documents first to enable AI conflict detection.');
      setWorkflowMessageType('error');
      return;
    }
    
    setGenerating(true);
    setWorkflowMessage('Generating redlined documents... This may take a few minutes.');
    setWorkflowMessageType('');
    
    try {
      const redlineResults = [];
      
      // Process each vendor file
      for (const vendorFile of vendorFiles) {
        setWorkflowMessage(`Processing ${vendorFile.filename}...`);
        
        try {
          const reviewResponse = await agentAPI.reviewDocument(vendorFile.s3_key, 'agent_processing');
          
          if (reviewResponse.redlined_document && reviewResponse.redlined_document.success) {
            redlineResults.push({
              originalFile: vendorFile,
              redlinedDocument: reviewResponse.redlined_document.redlined_document,
              analysis: reviewResponse.analysis,
              success: true
            });
          } else {
            redlineResults.push({
              originalFile: vendorFile,
              error: reviewResponse.redlined_document?.error || reviewResponse.error || 'Unknown error',
              success: false
            });
          }
        } catch (error) {
          redlineResults.push({
            originalFile: vendorFile,
            error: error.message,
            success: false
          });
        }
      }
      
      const successfulResults = redlineResults.filter(r => r.success);
      const failedResults = redlineResults.filter(r => !r.success);
      
      setRedlinedDocuments(redlineResults);
      
      if (successfulResults.length > 0) {
        setWorkflowMessage(
          `Successfully generated ${successfulResults.length} redlined document(s)! ${
            failedResults.length > 0 ? `${failedResults.length} failed.` : ''
          } Scroll down to download.`
        );
        setWorkflowMessageType('success');
      } else {
        setWorkflowMessage('All redline generation attempts failed. Please check your documents and try again.');
        setWorkflowMessageType('error');
      }
      
    } catch (error) {
      console.error('Redline generation error:', error);
      setWorkflowMessage(`Failed to generate redlined documents: ${error.message}`);
      setWorkflowMessageType('error');
    } finally {
      setGenerating(false);
    }
  };

  const handleDownloadRedlined = async (redlineResult) => {
    if (!redlineResult.redlinedDocument) {
      setWorkflowMessage('No redlined document available for download.');
      setWorkflowMessageType('error');
      return;
    }
    
    try {
      const downloadResult = await agentAPI.downloadFile(
        redlineResult.redlinedDocument, 
        'agent_processing',
        `${redlineResult.originalFile.filename.replace(/\.[^/.]+$/, '')}_REDLINED.docx`
      );
      
      if (downloadResult.success) {
        setWorkflowMessage(`Downloaded: ${downloadResult.filename}`);
        setWorkflowMessageType('success');
      } else {
        setWorkflowMessage(`Download failed: ${downloadResult.error}`);
        setWorkflowMessageType('error');
      }
    } catch (error) {
      console.error('Download error:', error);
      setWorkflowMessage(`Download failed: ${error.message}`);
      setWorkflowMessageType('error');
    }
  };

  const vendorFiles = uploadedFiles.filter(f => f.type === 'vendor_submission');
  const referenceFiles = uploadedFiles.filter(f => f.type === 'reference_document');
  const canGenerateRedline = vendorFiles.length > 0 && referenceFiles.length > 0;

  const renderMainContent = () => {
    if (configError) {
      return (
        <div className="main-content">
          <div className="card">
            <h1>One-L Document Management</h1>
            <div className="alert alert-error">
              <strong>Configuration Error:</strong> {configError}
            </div>
            <p>Please ensure the application is properly deployed and configured.</p>
          </div>
        </div>
      );
    }

    if (!configLoaded) {
      return (
        <div className="main-content">
          <div className="card">
            <h1>One-L Document Management</h1>
            <p>Loading configuration...</p>
          </div>
        </div>
      );
    }

    switch (activeSection) {
      case 'admin':
        return (
          <div className="main-content">
            <AdminDashboard activeTab={activeTab} onTabChange={setActiveTab} />
          </div>
        );
      case 'main':
      default:
        return (
          <div className="main-content">
            <div className="card">
              <h1>One L</h1>
              <p>AI-based First pass review of Vendor submission</p>
            </div>
            
            <div className="upload-sections">
              <VendorSubmission onFilesUploaded={handleFilesUploaded} />
              
              <FileUpload 
                title="Reference Documents"
                maxFiles={null}
                bucketType="user_documents"
                prefix="reference-docs/"
                description="Upload reference documents (contracts, policies, etc.) that will be used by the AI for conflict detection during vendor submission review"
                onFilesUploaded={handleFilesUploaded}
                enableAutoSync={true}
              />
            </div>
            
            {/* Centralized Workflow Section */}
            <div className="card" style={{ marginTop: '20px' }}>
              <h2>AI Document Review Workflow</h2>
              <p>Generate redlined documents after uploading both reference documents and vendor submissions.</p>
              
              {/* Upload Status */}
              <div style={{ marginBottom: '20px' }}>
                <div style={{ display: 'flex', gap: '20px', marginBottom: '10px' }}>
                  <div style={{ 
                    padding: '10px', 
                    borderRadius: '4px', 
                    background: referenceFiles.length > 0 ? '#d4edda' : '#f8d7da',
                    border: `1px solid ${referenceFiles.length > 0 ? '#c3e6cb' : '#f5c6cb'}`,
                    flex: '1'
                  }}>
                    <strong>Reference Documents:</strong> {referenceFiles.length} uploaded
                    {referenceFiles.length > 0 && (
                      <div style={{ fontSize: '12px', marginTop: '4px' }}>
                        {referenceFiles.map(f => f.filename).join(', ')}
                      </div>
                    )}
                  </div>
                  
                  <div style={{ 
                    padding: '10px', 
                    borderRadius: '4px', 
                    background: vendorFiles.length > 0 ? '#d4edda' : '#f8d7da',
                    border: `1px solid ${vendorFiles.length > 0 ? '#c3e6cb' : '#f5c6cb'}`,
                    flex: '1'
                  }}>
                    <strong>Vendor Submissions:</strong> {vendorFiles.length} uploaded
                    {vendorFiles.length > 0 && (
                      <div style={{ fontSize: '12px', marginTop: '4px' }}>
                        {vendorFiles.map(f => f.filename).join(', ')}
                      </div>
                    )}
                  </div>
                </div>
              </div>
              
              {/* Generate Redline Button */}
              <div style={{ textAlign: 'center', marginBottom: '20px' }}>
                <button
                  onClick={handleGenerateRedline}
                  disabled={!canGenerateRedline || generating}
                  style={{
                    background: canGenerateRedline ? '#007bff' : '#6c757d',
                    color: 'white',
                    border: 'none',
                    padding: '12px 24px',
                    borderRadius: '8px',
                    fontSize: '16px',
                    fontWeight: 'bold',
                    cursor: canGenerateRedline ? 'pointer' : 'not-allowed',
                    opacity: generating ? 0.6 : 1
                  }}
                >
                  {generating ? 'Generating Redlined Documents...' : 'Generate Redlined Documents'}
                </button>
                
                {!canGenerateRedline && (
                  <div style={{ marginTop: '8px', fontSize: '14px', color: '#666' }}>
                    Please upload both reference documents and vendor submissions to enable redline generation.
                  </div>
                )}
              </div>
              
              {/* Workflow Messages */}
              {workflowMessage && (
                <div className={`alert ${workflowMessageType === 'success' ? 'alert-success' : 'alert-error'}`} style={{ marginBottom: '20px' }}>
                  {workflowMessage}
                </div>
              )}
              
              {/* Download Section */}
              {redlinedDocuments.length > 0 && (
                <div>
                  <h3>Generated Redlined Documents</h3>
                  <div style={{ border: '1px solid #ddd', borderRadius: '4px', padding: '12px' }}>
                    {redlinedDocuments.map((result, index) => (
                      <div key={index} style={{ 
                        padding: '12px 0',
                        borderBottom: index < redlinedDocuments.length - 1 ? '1px solid #eee' : 'none'
                      }}>
                        <div style={{ fontWeight: '500', marginBottom: '8px' }}>
                          {result.originalFile.filename}
                        </div>
                        
                        {result.success ? (
                          <div>
                            <button
                              onClick={() => handleDownloadRedlined(result)}
                              disabled={generating}
                              style={{
                                background: '#28a745',
                                color: 'white',
                                border: 'none',
                                padding: '8px 16px',
                                borderRadius: '4px',
                                cursor: 'pointer',
                                fontSize: '14px',
                                marginBottom: '8px'
                              }}
                            >
                              Download Redlined Document
                            </button>
                            
                            {result.analysis && (
                              <div style={{ 
                                marginTop: '8px', 
                                padding: '8px', 
                                background: '#f8f9fa', 
                                borderRadius: '4px',
                                fontSize: '12px'
                              }}>
                                <strong>AI Analysis Preview:</strong>
                                <div style={{ maxHeight: '100px', overflow: 'auto', marginTop: '4px' }}>
                                  {result.analysis.substring(0, 300)}...
                                </div>
                              </div>
                            )}
                          </div>
                        ) : (
                          <div style={{ color: '#dc3545', fontSize: '14px' }}>
                            Error: {result.error}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        );
    }
  };

  return (
    <div className="app-container">
      <Sidebar 
        activeSection={activeSection} 
        activeTab={activeTab} 
        onSectionChange={setActiveSection} 
        onTabChange={setActiveTab} 
      />
      {renderMainContent()}
    </div>
  );
};

export default App; 