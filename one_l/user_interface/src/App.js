/**
 * Main App Component for One-L Application
 */

import React, { useState, useEffect } from 'react';
import { BrowserRouter as Router, Routes, Route, useNavigate, useParams, useLocation } from 'react-router-dom';
import FileUpload from './components/FileUpload';
import VendorSubmission from './components/VendorSubmission';
import SessionSidebar from './components/SessionSidebar';
import AdminDashboard from './components/AdminDashboard';
import UserHeader from './components/UserHeader';
import { isConfigValid, loadConfig } from './utils/config';
import { agentAPI, sessionAPI } from './services/api';
import authService from './services/auth';
import webSocketService from './services/websocket';

// Simple session component that loads session from URL
const SessionView = () => {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    loadSessionFromUrl();
  }, [sessionId]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadSessionFromUrl = async () => {
    if (!sessionId) {
      setError('No session ID provided');
      setLoading(false);
      return;
    }

    try {
      setLoading(true);
      const userId = authService.getUserId();
      if (!userId) {
        navigate('/');
        return;
      }

      // Check if session was passed via navigation state (for newly created sessions)
      if (location.state?.session && location.state.session.session_id === sessionId) {

        setSession(location.state.session);
        setLoading(false);
        return;
      }

      // Fallback: Load ALL user sessions (including new ones without results) and find the current one

      const response = await sessionAPI.getUserSessions(userId, false); // Don't filter by results for session lookup
      if (response.success && response.sessions) {
        const foundSession = response.sessions.find(s => s.session_id === sessionId);
        if (foundSession) {

          setSession(foundSession);
        } else {
          // Session not found - might be a new session that hasn't been persisted yet

          setSession({
            session_id: sessionId,
            title: 'Loading Session...',
            created_at: new Date().toISOString(),
            has_results: false,
            status: 'active'
          });
        }
      } else {
        setError('Failed to load sessions');
      }
    } catch (err) {

      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="main-content">
        <div className="card">
          <h1>One L</h1>
          <p>Loading session...</p>
          <div style={{
            width: '32px',
            height: '32px',
            border: '3px solid #dee2e6',
            borderTop: '3px solid #0066cc',
            borderRadius: '50%',
            animation: 'spin 1s linear infinite',
            margin: '20px auto'
          }}></div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="main-content">
        <div className="card">
          <h1>One L</h1>
          <div className="alert alert-error">
            <strong>Session Error:</strong> {error}
          </div>
          <button 
            onClick={() => navigate('/')} 
            className="btn"
            style={{ marginTop: '16px' }}
          >
            Go to Sessions
          </button>
        </div>
      </div>
    );
  }

  return (
    <SessionWorkspace session={session} />
  );
};

// Session workspace component for document processing
const SessionWorkspace = ({ session }) => {
  const location = useLocation();
  
  // Workflow state for this session
  const [uploadedFiles, setUploadedFiles] = useState([]);
  const [generating, setGenerating] = useState(false);
  const [workflowMessage, setWorkflowMessage] = useState('');
  const [workflowMessageType, setWorkflowMessageType] = useState('');
  const [processingStage, setProcessingStage] = useState(''); // 'syncing', 'identifying', 'generating'
  const [stageProgress, setStageProgress] = useState(0); // 0-100
  const [redlinedDocuments, setRedlinedDocuments] = useState([]);
  const [sessionResults, setSessionResults] = useState([]);
  const [loadingResults, setLoadingResults] = useState(false);
  
  // ← NEW KB SYNC STATE
  // eslint-disable-next-line no-unused-vars
  const [kbSyncStatus, setKbSyncStatus] = useState('unknown'); // 'syncing', 'ready', 'unknown'
  // eslint-disable-next-line no-unused-vars
  const [kbSyncProgress, setKbSyncProgress] = useState(0);
  // eslint-disable-next-line no-unused-vars
  const [kbSyncMessage, setKbSyncMessage] = useState('');
  
  // Determine if this is a new session (came from navigation state) or existing session (clicked from sidebar)
  const isNewSession = location.state?.session?.session_id === session?.session_id;

  // Load session results when component mounts and setup WebSocket
  useEffect(() => {
    if (session?.session_id) {
      loadSessionResults();
      setupWebSocket();
    }
    
    // Cleanup WebSocket and progress on unmount
    return () => {
      cleanupWebSocket();
      // Clean up progress interval
      if (window.progressInterval) {
        clearInterval(window.progressInterval);
        window.progressInterval = null;
      }
    };
  }, [session?.session_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadSessionResults = async () => {
    try {
      setLoadingResults(true);
      const userId = authService.getUserId();
      
      // Always try to load results - the backend will return empty if none exist

      
      const response = await sessionAPI.getSessionResults(session.session_id, userId);
      
      if (response.success && response.results) {
        setSessionResults(response.results);

      } else {
        // Session might not have results yet

        setSessionResults([]);
      }
    } catch (error) {
      // Don't log as error for new sessions - they won't have results yet
      if (session?.has_results) {

      } else {

      }
      setSessionResults([]);
    } finally {
      setLoadingResults(false);
    }
  };

  const handleFilesUploaded = (files) => {
    setUploadedFiles(prevFiles => {
      // Remove any existing files of the same type and add new ones
      const existingOtherType = prevFiles.filter(f => f.type !== files[0].type);
      return [...existingOtherType, ...files];
    });
  };

  // ← NEW HANDLER FUNCTION
  const handleKbSyncStatusChange = (status, progress, message) => {

    setKbSyncStatus(status);
    setKbSyncProgress(progress);
    setKbSyncMessage(message);
  };

  const setupWebSocket = async () => {
    try {
      // Connect to WebSocket
      await webSocketService.connect();

      
      // Subscribe to session-level updates to catch all notifications for this session
      if (session?.session_id) {
        webSocketService.subscribeToSession(session.session_id);

      }
      
      // Set up message handlers
      webSocketService.onMessageType('job_progress', handleJobProgress);
      webSocketService.onMessageType('job_completed', handleJobCompleted);
      webSocketService.onMessageType('session_update', handleSessionUpdate);
      webSocketService.onMessageType('error', handleWebSocketError);
      
      // Add catch-all handler to debug any missed messages
      const catchAllHandler = (message) => {

        // Look for any completion notifications for this session, regardless of job ID
        if ((message.type === 'job_completed' || message.type === 'document_completed') && 
            message.session_id === session?.session_id) {

          handleJobCompleted(message);
        }
      };
      webSocketService.onMessageType('*', catchAllHandler);
      
    } catch (error) {

      // WebSocket failure shouldn't break the app - polling will still work
    }
  };

  const cleanupWebSocket = () => {
    // Remove message handlers
    webSocketService.offMessageType('job_progress', handleJobProgress);
    webSocketService.offMessageType('job_completed', handleJobCompleted);
    webSocketService.offMessageType('session_update', handleSessionUpdate);
    webSocketService.offMessageType('error', handleWebSocketError);
    // Note: Can't remove the specific catch-all handler here as it's defined inline
    
    // Disconnect WebSocket
    webSocketService.disconnect();
  };

  const handleJobProgress = (message) => {

    const { job_id, session_id, data } = message;
    
    // Update UI with progress
    if (session_id === session?.session_id) {
      setWorkflowMessage(data.message || `Processing... ${data.progress || 0}%`);
      setWorkflowMessageType('progress');
      
      // Update progress for any active jobs
      setRedlinedDocuments(prev => prev.map(doc => {
        if (doc.jobId === job_id) {
          return {
            ...doc,
            progress: data.progress || 0,
            status: data.status || 'processing',
            message: data.message
          };
        }
        return doc;
      }));
    }
  };

  const handleJobCompleted = (message) => {

    const { job_id, session_id, data } = message;
    
    if (session_id === session?.session_id) {

      
      // Stop progress and update UI
      if (window.progressInterval) {
        clearInterval(window.progressInterval);
        window.progressInterval = null;
      }
      
      setStageProgress(100);
      setProcessingStage('completed');
      setWorkflowMessage('Document processing completed successfully!');
      setWorkflowMessageType('success');
      
      // Use functional update to properly handle existing entries
      setRedlinedDocuments(prev => {
        const existingIndex = prev.findIndex(doc => doc.jobId === job_id);
        
        if (existingIndex !== -1) {
          // UPDATE existing entry instead of adding new one
          const updated = [...prev];
          updated[existingIndex] = {
            ...updated[existingIndex],
            status: 'completed',
            progress: 100,
            success: data.redlined_document && data.redlined_document.success,
            redlinedDocument: data.redlined_document?.redlined_document,
            analysis: data.analysis_id,
            processing: false,
            message: 'Document processing completed'
          };
          return updated;
        } else {
          // Only add new entry if somehow none exists (fallback)
          if (data.redlined_document && data.redlined_document.success) {
            return [...prev, {
              originalFile: { 
                filename: window.currentProcessingJob?.filename || `Document for job ${job_id}` 
              },
              redlinedDocument: data.redlined_document.redlined_document,
              analysis: data.analysis_id,
              success: true,
              processing: false,
              jobId: job_id
            }];
          }
          return prev;
        }
      });
      
      window.currentProcessingJob = null;
      
      // Keep progress bar visible and show completed state
      // Don't reset the progress - keep it at 100% to show completion
    }
  };

  const handleSessionUpdate = (message) => {

    // Handle session updates (e.g., title changes, status updates)
  };

  const handleWebSocketError = (message) => {

    setWorkflowMessage(`WebSocket error: ${message.message}`);
    setWorkflowMessageType('error');
  };

  // Add polling function for job status
  const pollJobStatus = async (jobId, userId, filename) => {
    /*
    const maxAttempts = 75; // 10 minutes with 8-second intervals (75 * 8 = 600 seconds = 10 minutes)
    let attempts = 0;
    
    while (attempts < maxAttempts) {
      try {
        const statusResponse = await sessionAPI.checkJobStatus(jobId, userId);
        
        if (statusResponse.success && statusResponse.job) {
          const { status, result, error } = statusResponse.job;
          
          if (status === 'completed' && result) {
            return {
              success: true,
              processing: false,
              redlined_document: result.redlined_document,
              analysis: result.analysis
            };
          } else if (status === 'failed') {
            return {
              success: false,
              processing: false,
              error: error || 'Processing failed'
            };
          } else if (status === 'analyzing') {
            setWorkflowMessage(`Analyzing ${filename} with AI...`);
          } else if (status === 'generating_redline') {
            setWorkflowMessage(`Generating redlined version of ${filename}...`);
          }
        }
        
        await new Promise(resolve => setTimeout(resolve, 10000)); // Wait 10 seconds
        attempts++;
      } catch (error) {

        await new Promise(resolve => setTimeout(resolve, 10000));
        attempts++;
      }
    }
    
    return {
      success: false,
      processing: false,
      error: 'Processing timeout - document may still be processing in background'
    };
    */
  };

  // Unified progress management system
  const updateProcessingStage = (stage, progress, message) => {
    setProcessingStage(stage);
    setStageProgress(progress);
    setWorkflowMessage(message);
    setWorkflowMessageType('progress');
    

  };

  // Smooth progress flow - 1% per 2 seconds for entire progress bar
  const startProgressFlow = () => {
    let currentProgress = 0;
    
    // Start with KB sync stage
    updateProcessingStage('kb_sync', 0, 'Syncing knowledge base...');
    
    const progressInterval = setInterval(() => {
      currentProgress += 1; // 1% per 2 seconds for entire progress bar
      
      if (currentProgress <= 33) {
        // Stage 1: KB Sync (0-33%)
        updateProcessingStage('kb_sync', currentProgress, 'Syncing knowledge base...');
      } else if (currentProgress <= 66) {
        // Stage 2: Identifying conflicts (34-66%)
        updateProcessingStage('identifying', currentProgress, 'Identifying conflicts...');
      } else if (currentProgress < 99) {
        // Stage 3: Generating redlines (67-98%)
        updateProcessingStage('generating', currentProgress, 'Generating redlines...');
        } else {
        // Wait at 99% until WebSocket completion
        updateProcessingStage('generating', 99, 'Generating redlines...');
        clearInterval(progressInterval);
        setStageProgress(99);
      }
    }, 2000); // 2000ms interval for 1% per 2 seconds
    
    // Store interval ID for cleanup
    window.progressInterval = progressInterval;
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
    
    // Start the smooth 3-stage progress flow
    startProgressFlow();
    
    try {
      const redlineResults = [];
      
      // Process each vendor file
      for (const vendorFile of vendorFiles) {
        setWorkflowMessage(`Starting analysis of ${vendorFile.filename}...`);
        
        try {
          const reviewResponse = await agentAPI.reviewDocument(
            vendorFile.s3_key, 
            'agent_processing',
            session?.session_id,
            session?.user_id
          );
          
          // Check if processing is asynchronous
          if (reviewResponse.processing && reviewResponse.job_id) {
            setWorkflowMessage(`Processing ${vendorFile.filename} in background...`);
            
            // Subscribe to WebSocket notifications for this job
            try {
              webSocketService.subscribeToJob(reviewResponse.job_id, session?.session_id);

              
              // Add job to tracking with initial progress
              setRedlinedDocuments(prev => [...prev, {
                originalFile: vendorFile,
                jobId: reviewResponse.job_id,
                status: 'processing',
                progress: 0,
                message: 'Starting document analysis...',
                processing: true
              }]);
              
            } catch (error) {

            }
            
            // Poll for completion (WebSocket is primary, polling is fallback)
            const finalResult = await pollJobStatus(
              reviewResponse.job_id, 
              session?.user_id, 
              vendorFile.filename
            );
            
            if (finalResult.success) {
              redlineResults.push({
                originalFile: vendorFile,
                redlinedDocument: finalResult.redlined_document.redlined_document,
                analysis: finalResult.analysis,
                success: true
              });
            } else {
              redlineResults.push({
                originalFile: vendorFile,
                error: finalResult.error,
                success: false
              });
            }
          } else if (reviewResponse.processing === false && reviewResponse.redlined_document && reviewResponse.redlined_document.success) {
            redlineResults.push({
              originalFile: vendorFile,
              redlinedDocument: reviewResponse.redlined_document.redlined_document,
              analysis: reviewResponse.analysis,
              success: true
            });
          } else if (reviewResponse.processing) {
            redlineResults.push({
              originalFile: vendorFile,
              processing: true,
              jobId: `job_${vendorFile.s3_key.replace(/[^a-zA-Z0-9]/g, '_')}_${Date.now()}`,
              success: false,
              message: reviewResponse.message || 'Processing in background...'
            });
          } else {
            redlineResults.push({
              originalFile: vendorFile,
              error: reviewResponse.redlined_document?.error || reviewResponse.error || 'Unknown error',
              success: false
            });
          }
        } catch (error) {
          if (error.message.includes('timeout') || error.message.includes('504') || error.message.includes('502') || 
              error.message.includes('CORS error') || error.message.includes('continue in background')) {
            
            // Don't log timeout/CORS errors as errors - they're expected for long processing
            

            
            // DON'T add to redlineResults - just use the unified progress UI
            // Store the job info for WebSocket tracking without showing old UI
            // Note: We can't get the real job ID from the backend due to timeout,
            // but the session-level subscription will catch the completion notification
            window.currentProcessingJob = {
              filename: vendorFile.filename,
              sessionId: session?.session_id,
              vendorFileKey: vendorFile.s3_key
            };
            
            // Ensure WebSocket connection is strong and subscribe to any updates for this session
            try {
              if (!webSocketService.getConnectionStatus().isConnected) {

                await webSocketService.connect();
              }
              
              // Session-level subscription is already set up in setupWebSocket()

              
            } catch (wsError) {

            }
            
            // Don't interfere with the natural progress flow
            // The progress will continue naturally and complete when WebSocket notification arrives
            
          } else {
            redlineResults.push({
              originalFile: vendorFile,
              error: error.message,
              success: false
            });
          }
        }
      }
      
      const successfulResults = redlineResults.filter(r => r.success);
      const failedResults = redlineResults.filter(r => !r.success && !r.processing);
      const processingResults = redlineResults.filter(r => r.processing);
      
      setRedlinedDocuments(redlineResults);
      
      if (successfulResults.length > 0) {
        let message = `Successfully generated ${successfulResults.length} redlined document(s)!`;
        if (processingResults.length > 0) {
          message += ` ${processingResults.length} document(s) are still processing in background.`;
        }
        if (failedResults.length > 0) {
          message += ` ${failedResults.length} failed.`;
        }
        if (successfulResults.length > 0) {
          message += ' Scroll down to download completed documents.';
        }
        setWorkflowMessage(message);
        setWorkflowMessageType('success');
      } else if (processingResults.length > 0) {
        setWorkflowMessage(
          `${processingResults.length} document(s) are processing in background due to complexity. Please wait for completion.`
        );
        setWorkflowMessageType('');
      } else if (failedResults.length > 0) {
        setWorkflowMessage('All redline generation attempts failed. Please check your documents and try again.');
        setWorkflowMessageType('error');
      } else {
        // No results yet - processing is starting, show processing message
        setWorkflowMessage('Starting document processing... Please wait for completion.');
        setWorkflowMessageType('');
      }
      
    } catch (error) {

      
      // Clean up progress interval on error
      if (window.progressInterval) {
        clearInterval(window.progressInterval);
        window.progressInterval = null;
      }
      
      setWorkflowMessage(`Failed to generate redlined documents: ${error.message}`);
      setWorkflowMessageType('error');
      setProcessingStage('');
      setStageProgress(0);
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

      setWorkflowMessage(`Download failed: ${error.message}`);
      setWorkflowMessageType('error');
    }
  };

  const vendorFiles = uploadedFiles.filter(f => f.type === 'vendor_submission');
  const referenceFiles = uploadedFiles.filter(f => f.type === 'reference_document');
  const canGenerateRedline = vendorFiles.length > 0 && referenceFiles.length > 0;

  // If this is an existing session (not new), show only the results table
  if (!isNewSession && sessionResults.length > 0) {
    return (
      <div className="main-content">
        <div className="card">
          <h1>One L</h1>
          <p>Session Analysis History</p>
          {session && (
            <div style={{ 
              fontSize: '14px', 
              color: '#666', 
              marginTop: '8px',
              padding: '8px',
              background: '#f8f9fa',
              borderRadius: '4px'
            }}>
              <strong>Session:</strong> {session.title}
            </div>
          )}
        </div>

        {/* Session Analysis Results - Full View */}
        <div style={{ marginTop: '20px' }}>
          <div style={{ 
            display: 'flex', 
            justifyContent: 'space-between', 
            alignItems: 'center',
            marginBottom: '16px'
          }}>
            <h3 style={{ margin: 0, color: '#333' }}>Analysis Results</h3>
            <span style={{ fontSize: '14px', color: '#666' }}>
              {sessionResults.length} result{sessionResults.length !== 1 ? 's' : ''}
            </span>
          </div>

          <div style={{
            border: '1px solid #dee2e6',
            borderRadius: '8px',
            background: '#fff',
            overflow: 'hidden'
          }}>
            {sessionResults.map((result, index) => (
              <div
                key={result.analysis_id}
                style={{
                  padding: '16px',
                  borderBottom: index < sessionResults.length - 1 ? '1px solid #dee2e6' : 'none'
                }}
              >
                {/* Result Header */}
                <div style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  marginBottom: '12px'
                }}>
                  <div>
                    <h4 style={{ margin: 0, color: '#333', fontSize: '16px' }}>
                      {result.document_name}
                    </h4>
                    <div style={{ fontSize: '12px', color: '#666', marginTop: '4px' }}>
                      {new Date(result.timestamp).toLocaleString()} • {result.conflicts_count} conflicts found
                    </div>
                  </div>
                  <div style={{
                    background: result.conflicts_count > 0 ? '#dc3545' : '#28a745',
                    color: 'white',
                    padding: '4px 8px',
                    borderRadius: '12px',
                    fontSize: '12px',
                    fontWeight: 'bold'
                  }}>
                    {result.conflicts_count} conflicts
                  </div>
                </div>

                {/* Conflicts Table */}
                {result.conflicts.length > 0 && (
                  <div style={{
                    border: '1px solid #dee2e6',
                    borderRadius: '4px',
                    overflow: 'hidden',
                    maxHeight: '400px',
                    overflowY: 'auto'
                  }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                      <thead>
                        <tr style={{ background: '#f8f9fa' }}>
                          <th style={{ 
                            padding: '8px 12px', 
                            textAlign: 'left', 
                            borderBottom: '1px solid #dee2e6',
                            fontSize: '12px',
                            fontWeight: 'bold',
                            color: '#666'
                          }}>
                            ID
                          </th>
                          <th style={{ 
                            padding: '8px 12px', 
                            textAlign: 'left', 
                            borderBottom: '1px solid #dee2e6',
                            fontSize: '12px',
                            fontWeight: 'bold',
                            color: '#666'
                          }}>
                            Vendor Text
                          </th>
                          <th style={{ 
                            padding: '8px 12px', 
                            textAlign: 'left', 
                            borderBottom: '1px solid #dee2e6',
                            fontSize: '12px',
                            fontWeight: 'bold',
                            color: '#666'
                          }}>
                            Source
                          </th>
                          <th style={{ 
                            padding: '8px 12px', 
                            textAlign: 'left', 
                            borderBottom: '1px solid #dee2e6',
                            fontSize: '12px',
                            fontWeight: 'bold',
                            color: '#666'
                          }}>
                            Type
                          </th>
                          <th style={{ 
                            padding: '8px 12px', 
                            textAlign: 'left', 
                            borderBottom: '1px solid #dee2e6',
                            fontSize: '12px',
                            fontWeight: 'bold',
                            color: '#666'
                          }}>
                            Rationale
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.conflicts.map((conflict, conflictIndex) => (
                          <tr 
                            key={conflictIndex}
                            style={{ 
                              background: conflictIndex % 2 === 0 ? 'white' : '#f8f9fa',
                              borderBottom: '1px solid #eee'
                            }}
                          >
                            <td style={{ 
                              padding: '8px 12px', 
                              fontSize: '12px',
                              fontFamily: 'monospace',
                              color: '#666'
                            }}>
                              {conflict.clarification_id}
                            </td>
                            <td style={{ 
                              padding: '8px 12px', 
                              fontSize: '12px',
                              maxWidth: '200px',
                              wordBreak: 'break-word'
                            }}>
                              {conflict.vendor_conflict}
                            </td>
                            <td style={{ 
                              padding: '8px 12px', 
                              fontSize: '12px',
                              color: '#666'
                            }}>
                              {conflict.source_doc}
                            </td>
                            <td style={{ 
                              padding: '8px 12px', 
                              fontSize: '12px'
                            }}>
                              <span style={{
                                background: '#ffc107',
                                color: 'black',
                                padding: '2px 6px',
                                borderRadius: '8px',
                                fontSize: '10px',
                                fontWeight: 'bold'
                              }}>
                                {conflict.conflict_type}
                              </span>
                            </td>
                            <td style={{ 
                              padding: '8px 12px', 
                              fontSize: '12px',
                              maxWidth: '300px',
                              wordBreak: 'break-word'
                            }}>
                              {conflict.rationale}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {result.conflicts.length === 0 && (
                  <div style={{
                    padding: '20px',
                    textAlign: 'center',
                    color: '#28a745',
                    background: '#d4edda',
                    border: '1px solid #c3e6cb',
                    borderRadius: '4px'
                  }}>
                    ✓ No conflicts found in this document
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        {loadingResults && (
          <div style={{ 
            textAlign: 'center', 
            margin: '20px 0',
            fontSize: '14px',
            color: '#666'
          }}>
            Loading session history...
          </div>
        )}

        {!loadingResults && sessionResults.length === 0 && (
          <div style={{
            textAlign: 'center',
            padding: '40px',
            color: '#666',
            border: '1px solid #dee2e6',
            borderRadius: '8px',
            marginTop: '20px'
          }}>
            No analysis results found for this session.
          </div>
        )}
      </div>
    );
  }

  // For new sessions or sessions without results, show the main page
  return (
    <div className="main-content">
      <div className="card">
        <h1>One L</h1>
        <p>AI-based First pass review of Vendor submission</p>
        {session && (
          <div style={{ 
            fontSize: '14px', 
            color: '#666', 
            marginTop: '8px',
            padding: '8px',
            background: '#f8f9fa',
            borderRadius: '4px'
          }}>
            <strong>Session:</strong> {session.title}
          </div>
        )}
      </div>
      
      <div className="upload-sections">
        <VendorSubmission onFilesUploaded={handleFilesUploaded} />
        
        <FileUpload 
          title="Reference Documents"
          maxFiles={null}
          bucketType="user_documents"
          prefix="reference-docs/"
          acceptedFileTypes=".doc,.docx,.pdf"
          fileTypeDescription="DOC, DOCX, PDF (Max 10MB per file)"
          onFilesUploaded={handleFilesUploaded}
          enableAutoSync={true}
          onSyncStatusChange={handleKbSyncStatusChange}
          sessionContext={session} //  Pass session context for session-based storage
        />
      </div>
      


      {/* AI Document Review Workflow */}
      <div className="card" style={{ marginTop: '20px' }}>
          <h2>AI Document Review Workflow</h2>
          <p>Generate redlined documents after uploading both reference documents and vendor submissions.</p>
        

        
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
              borderRadius: '4px',
              cursor: canGenerateRedline && !generating ? 'pointer' : 'not-allowed',
              fontSize: '16px',
              fontWeight: 'bold',
              transition: 'background-color 0.2s',
              opacity: generating ? 0.6 : 1
            }}
          >
            {generating ? 'Generating Redlines...' : 'Generate Redlined Documents'}
          </button>
        </div>
        
        {/* Unified Progress UI */}
        {(generating || processingStage || (redlinedDocuments.filter(doc => doc.success && !doc.processing).length > 0)) && (() => {
          // Determine display state
          const hasCompletedResults = redlinedDocuments.filter(doc => doc.success && !doc.processing).length > 0;
          const isCompleted = hasCompletedResults && !generating && !processingStage;
          const displayProgress = isCompleted ? 100 : stageProgress;
          const displayStage = isCompleted ? 'completed' : processingStage;
          const displayMessage = isCompleted ? 'Document processing completed successfully!' : workflowMessage;
          
          return (
            <div style={{ 
              marginTop: '20px', 
              padding: '20px', 
              border: '1px solid #ddd', 
              borderRadius: '8px',
              backgroundColor: isCompleted ? '#d4edda' : '#f8f9fa'
            }}>
              <h4 style={{ marginBottom: '16px', color: '#333' }}>
                {isCompleted ? 'Document Processing Complete' : 'Processing Document'}
              </h4>
            
            {/* Stage Indicators */}
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '16px' }}>
              <div style={{ 
                textAlign: 'center', 
                flex: 1,
                color: displayStage === 'kb_sync' || displayProgress >= 1 || isCompleted ? '#007bff' : '#6c757d'
              }}>
                <div style={{
                  width: '12px',
                  height: '12px',
                  borderRadius: '50%',
                  backgroundColor: displayProgress > 33 || isCompleted ? '#28a745' : (displayStage === 'kb_sync' ? '#007bff' : '#6c757d'),
                  margin: '0 auto 4px'
                }}></div>
                <small>Knowledge Base Sync</small>
              </div>
              <div style={{ 
                textAlign: 'center', 
                flex: 1,
                color: displayStage === 'identifying' || displayProgress > 33 || isCompleted ? '#007bff' : '#6c757d'
              }}>
                <div style={{
                  width: '12px',
                  height: '12px',
                  borderRadius: '50%',
                  backgroundColor: displayProgress > 66 || isCompleted ? '#28a745' : (displayStage === 'identifying' ? '#007bff' : '#6c757d'),
                  margin: '0 auto 4px'
                }}></div>
                <small>Identifying Conflicts</small>
              </div>
              <div style={{ 
                textAlign: 'center', 
                flex: 1,
                color: displayStage === 'generating' || displayProgress > 66 || isCompleted ? '#007bff' : '#6c757d'
              }}>
                <div style={{
                  width: '12px',
                  height: '12px',
                  borderRadius: '50%',
                  backgroundColor: displayProgress >= 100 || isCompleted ? '#28a745' : (displayStage === 'generating' ? '#007bff' : '#6c757d'),
                  margin: '0 auto 4px'
                }}></div>
                <small>Generating Redlines</small>
              </div>
            </div>
            
            {/* Progress Bar */}
            <div style={{ 
              width: '100%', 
              height: '8px', 
              backgroundColor: '#e9ecef', 
              borderRadius: '4px',
              marginBottom: '12px',
              overflow: 'hidden'
            }}>
              <div style={{
                width: `${displayProgress}%`,
                height: '100%',
                backgroundColor: isCompleted ? '#28a745' : '#007bff',
                transition: 'width 0.3s ease',
                borderRadius: '4px'
              }}></div>
            </div>
            
            {/* Current Status */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              {(!isCompleted && displayProgress < 100) && (
                <div style={{
                  width: '16px',
                  height: '16px',
                  border: '2px solid #007bff',
                  borderTop: '2px solid transparent',
                  borderRadius: '50%',
                  animation: 'spin 1s linear infinite'
                }}></div>
              )}
              {(isCompleted || displayProgress >= 100) && (
                <div style={{
                  width: '16px',
                  height: '16px',
                  color: '#28a745',
                  fontSize: '16px'
                }}>
                  ✓
                </div>
              )}
              <span style={{ color: '#333' }}>
                {displayMessage} ({Math.round(displayProgress)}%)
              </span>
            </div>
          </div>
          );
        })()}
        
        {/* Other Messages (errors, success without progress) */}
        {workflowMessage && workflowMessageType !== 'progress' && !generating && !processingStage && redlinedDocuments.filter(doc => doc.success && !doc.processing).length === 0 && (
          <div className={`alert ${
            workflowMessageType === 'success' ? 'alert-success' : 'alert-error'
          }`}>
            {workflowMessage}
          </div>
        )}
        
        {/* Redlined Documents Results - Only show completed documents */}
        {redlinedDocuments.filter(doc => doc.success && !doc.processing).length > 0 && (
          <div style={{ marginTop: '20px' }}>
            <h3>Generated Redlined Documents</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              {redlinedDocuments.filter(doc => doc.success && !doc.processing).map((result, index) => (
                <div key={index} style={{ 
                  padding: '12px', 
                  border: '1px solid #ddd', 
                  borderRadius: '4px',
                  background: result.success ? '#f8f9fa' : (result.processing ? '#fff3cd' : '#f8d7da')
                }}>
                  <div style={{ fontWeight: 'bold', marginBottom: '8px' }}>
                    {result.originalFile.filename}
                  </div>
                  {result.success ? (
                    <div>
                      <button
                        onClick={() => handleDownloadRedlined(result)}
                        style={{
                          background: '#28a745',
                          color: 'white',
                          border: 'none',
                          padding: '6px 12px',
                          borderRadius: '4px',
                          cursor: 'pointer',
                          fontSize: '14px'
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
                  ) : result.processing ? (
                    <div>
                      {/* Progress Bar */}
                      <div style={{ 
                        background: '#f8f9fa',
                        borderRadius: '8px',
                        padding: '12px',
                        marginBottom: '8px'
                      }}>
                        <div style={{ 
                          display: 'flex', 
                          justifyContent: 'space-between', 
                          alignItems: 'center',
                          marginBottom: '8px'
                        }}>
                          <span style={{ fontSize: '14px', fontWeight: 'bold', color: '#495057' }}>
                            {result.message || 'Processing...'}
                          </span>
                          <span style={{ fontSize: '12px', color: '#6c757d' }}>
                            {result.progress || 0}%
                          </span>
                        </div>
                        
                        {/* Progress bar */}
                        <div style={{
                          width: '100%',
                          height: '8px',
                          background: '#e9ecef',
                          borderRadius: '4px',
                          overflow: 'hidden'
                        }}>
                          <div style={{
                            width: `${result.progress || 0}%`,
                            height: '100%',
                            background: result.progress >= 100 ? '#28a745' : '#007bff',
                            borderRadius: '4px',
                            transition: 'width 0.3s ease-in-out'
                          }}></div>
                        </div>
                      </div>
                      
                      <div style={{ fontSize: '12px', color: '#666' }}>
                        {result.timeoutError ? 
                          'API Gateway timed out, but processing continues via WebSocket. Download will appear when ready.' :
                          'Document processing in background due to complexity. Download will appear when ready.'
                        }
                      </div>
                    </div>
                  ) : (
                    <div style={{ color: '#dc3545', fontSize: '14px' }}>
                      Error: {result.error || result.message}
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
};

// Auto-redirect component that creates session and navigates
const AutoSessionRedirect = () => {
  const navigate = useNavigate();

  const [error, setError] = useState(null);
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    createSessionAndRedirect();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const createSessionAndRedirect = async () => {
    try {
      setError(null);
      
      const userId = authService.getUserId();
      if (!userId) {
        throw new Error('No user ID available. Please try logging in again.');
      }


      const response = await sessionAPI.createSession(userId);
      
      if (response.success && response.session?.session_id) {

        navigate(`/${response.session.session_id}`, { 
          replace: true, 
          state: { session: response.session } 
        });
      } else {
        throw new Error(response.message || 'Failed to create session');
      }
    } catch (err) {

      
      let errorMessage = err.message;
      if (err.message.includes('timeout')) {
        errorMessage = 'Session creation timed out. Please try again.';
      } else if (err.message.includes('Failed to fetch')) {
        errorMessage = 'Network error. Please check your connection.';
      }
      
      setError(errorMessage);
    }
  };

  const handleRetry = () => {
    setRetryCount(prev => prev + 1);
    createSessionAndRedirect();
  };

  if (error) {
    return (
      <div className="main-content">
        <div className="card">
          <h1>One L</h1>
          <div className="alert alert-error">
            <strong>Error:</strong> {error}
          </div>
          <div style={{ marginTop: '16px', display: 'flex', gap: '12px', justifyContent: 'center' }}>
            <button 
              onClick={handleRetry} 
              className="btn"
              style={{ backgroundColor: '#007bff', color: 'white' }}
            >
              Retry {retryCount > 0 ? `(${retryCount})` : ''}
            </button>
            <button 
              onClick={() => window.location.reload()} 
              className="btn"
              style={{ backgroundColor: '#6c757d', color: 'white' }}
            >
              Refresh Page
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="main-content">
      <div className="card">
        <h1>One L</h1>
        <p>Setting up your workspace...</p>
        <div style={{
          width: '32px',
          height: '32px',
          border: '3px solid #dee2e6',
          borderTop: '3px solid #0066cc',
          borderRadius: '50%',
          animation: 'spin 1s linear infinite',
          margin: '20px auto'
        }}></div>
      </div>
    </div>
  );
};

// Create a separate component that uses router hooks
const AppContent = () => {
  const navigate = useNavigate();
  const [configLoaded, setConfigLoaded] = useState(false);
  const [configError, setConfigError] = useState('');
  const [activeTab, setActiveTab] = useState('data');
  
  // Authentication state
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [currentUser, setCurrentUser] = useState(null);
  const [authLoading, setAuthLoading] = useState(true);

  useEffect(() => {
    const initializeApp = async () => {
      try {
        setAuthLoading(true);
        
        // Load configuration first
        await loadConfig();
        const isValid = await isConfigValid();
        
        if (!isValid) {
          setConfigError('Configuration is incomplete. Please check your deployment.');
          setAuthLoading(false);
          return;
        }
        
        setConfigLoaded(true);
        
        // Initialize authentication
        const authInitialized = await authService.initialize();
        if (!authInitialized) {

          setAuthLoading(false);
          return;
        }
        
        // Check if user is authenticated
        if (authService.isUserAuthenticated()) {
          setIsAuthenticated(true);
          setCurrentUser(authService.getCurrentUser());

        }
        
        setAuthLoading(false);
      } catch (error) {

        setConfigError('Failed to load application configuration.');
        setAuthLoading(false);
      }
    };

    initializeApp();
  }, []);



  // Authentication handlers
  const handleLogin = () => {
    authService.login(); // This redirects to Cognito
  };

  const handleLogout = () => {
    // Clear local state
    setIsAuthenticated(false);
    setCurrentUser(null);
    
    // Logout from Cognito (this will redirect)
    authService.logout();
  };

  // Simplified session handlers - no longer needed with direct navigation
  // All workflow logic moved to SessionWorkspace component

  // Handle admin section navigation
  const handleAdminSectionChange = async (section) => {
    if (section === 'admin') {
      navigate('/admin/knowledgebase');
    } else {
      // Go back to main page by creating a new session
      try {
        const userId = authService.getUserId();
        const response = await sessionAPI.createSession(userId);
        if (response.success) {
          navigate(`/${response.session.session_id}`, { 
            state: { session: response.session } 
          });
        }
      } catch (error) {

        navigate('/');
      }
    }
  };

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

    // Use Routes instead of switch statement
    return (
      <Routes>
        <Route path="/admin/knowledgebase" element={
          <div className="main-content">
            <AdminDashboard activeTab={activeTab} onTabChange={setActiveTab} />
          </div>
        } />
        <Route path="/:sessionId" element={<SessionView />} />
        <Route path="/" element={<AutoSessionRedirect />} />
      </Routes>
    );
  };

  // Show loading screen during auth initialization
  if (authLoading) {
    return (
      <div style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        height: '100vh',
        backgroundColor: '#f8f9fa',
        flexDirection: 'column',
        gap: '16px'
      }}>
        <div style={{
          width: '32px',
          height: '32px',
          border: '3px solid #dee2e6',
          borderTop: '3px solid #0066cc',
          borderRadius: '50%',
          animation: 'spin 1s linear infinite'
        }}></div>
        <div style={{ fontSize: '16px', color: '#666' }}>
          Initializing One-L...
        </div>
      </div>
    );
  }

  // Show configuration error
  if (configError) {
    return (
      <div style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        height: '100vh',
        backgroundColor: '#f8f9fa',
        padding: '20px'
      }}>
        <div style={{
          backgroundColor: 'white',
          padding: '32px',
          borderRadius: '8px',
          boxShadow: '0 2px 4px rgba(0,0,0,0.1)',
          textAlign: 'center',
          maxWidth: '400px'
        }}>
          <h2 style={{ color: '#dc3545', marginBottom: '16px' }}>Configuration Error</h2>
          <p style={{ color: '#666', marginBottom: '16px' }}>{configError}</p>
          <button
            onClick={() => window.location.reload()}
            style={{
              padding: '8px 16px',
              backgroundColor: '#0066cc',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer'
            }}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  // Show login screen if not authenticated
  if (!isAuthenticated) {
    return (
      <div style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        height: '100vh',
        backgroundColor: '#f8f9fa',
        flexDirection: 'column',
        gap: '24px'
      }}>
        <div style={{ textAlign: 'center' }}>
          <h1 style={{ color: '#333', marginBottom: '16px' }}>Welcome to One-L</h1>
          <p style={{ color: '#666', marginBottom: '24px' }}>
            AI-based First pass review of Vendor submission
          </p>
          <button
            onClick={handleLogin}
            style={{
              padding: '12px 24px',
              backgroundColor: '#0066cc',
              color: 'white',
              border: 'none',
              borderRadius: '8px',
              fontSize: '16px',
              fontWeight: 'bold',
              cursor: 'pointer'
            }}
          >
            Sign In with Cognito
          </button>
        </div>
      </div>
    );
  }

  // Show authenticated app
  return (
    <div className="app-container" style={{ display: 'flex', height: '100vh', position: 'relative' }}>
      {/* User Header */}
      <UserHeader 
        user={currentUser}
        onLogout={handleLogout}
      />
      
      {/* Session Sidebar with integrated Admin */}
      <SessionSidebar
        currentUserId={authService.getUserId()}
        onAdminSectionChange={handleAdminSectionChange}
        isVisible={true}
      />
      
      {/* Main Content Area */}
      <div style={{ marginLeft: '280px', marginTop: '60px', minHeight: 'calc(100vh - 60px)', overflow: 'auto' }}>
        {renderMainContent()}
      </div>
    </div>
  );
};

// Main App component with Router wrapper
const App = () => {
  return (
    <Router>
      <AppContent />
    </Router>
  );
};

export default App;