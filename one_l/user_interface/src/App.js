/**
 * Main App Component for One-L Application
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate, useNavigate, useParams, useLocation } from 'react-router-dom';
import FileUpload from './components/FileUpload';
import VendorSubmission from './components/VendorSubmission';
import SessionSidebar from './components/SessionSidebar';
import AdminDashboard from './components/AdminDashboard';
import MetricsDashboard from './components/MetricsDashboard';
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
      
      // Handle different response structures (wrapped in body or direct)
      let responseData = response;
      if (response.body) {
        try {
          responseData = typeof response.body === 'string' ? JSON.parse(response.body) : response.body;
        } catch (e) {
          console.error('Error parsing response body in SessionView:', e);
          responseData = response;
        }
      }
      
      if (responseData.success && responseData.sessions) {
        const foundSession = responseData.sessions.find(s => s.session_id === sessionId);
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
        setError(responseData.error || 'Failed to load sessions');
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
  
  const stageOrder = [
    { key: 'kb_sync', label: 'Knowledge Base Sync' },
    { key: 'document_review', label: 'AI Analysis' },
    { key: 'generating', label: 'Result Generation' }
  ];

  const stageMessages = {
    kb_sync: 'Syncing knowledge base. Please stand by.',
    document_review: 'Reviewing documents with AI. Please stand by.',
    generating: 'Generating redlines. Please stand by.'
  };

  const statusToStageMap = {
    analyzing: 'document_review',
    generating_redline: 'generating',
    kb_sync: 'kb_sync'
  };

  // Session-specific storage: store uploadedFiles and redlinedDocuments per session
  // Load from localStorage on mount to persist across page reloads
  const getSessionDataStorageKey = () => {
    const userId = authService.getUserId();
    return userId ? `one_l_session_data_${userId}` : null;
  };

  const loadSessionDataFromStorage = () => {
    const storageKey = getSessionDataStorageKey();
    if (!storageKey) return {};
    try {
      const stored = localStorage.getItem(storageKey);
      if (stored) {
        return JSON.parse(stored);
      }
    } catch (error) {
      console.error('Error loading session data from localStorage:', error);
    }
    return {};
  };

  const saveSessionDataToStorage = useCallback((data) => {
    const storageKey = getSessionDataStorageKey();
    if (!storageKey) return;
    try {
      localStorage.setItem(storageKey, JSON.stringify(data));
    } catch (error) {
      console.error('Error saving session data to localStorage:', error);
    }
  }, []); // Empty deps - getSessionDataStorageKey uses authService which is stable

  // Initialize sessionDataRef from localStorage on mount
  const sessionDataRef = useRef(loadSessionDataFromStorage());
  const previousSessionIdRef = useRef(null);
  const highestStageIndexRef = useRef(-1);

  const getJobSessionMapStorageKey = () => {
    const userId = authService.getUserId();
    return userId ? `one_l_job_session_map_${userId}` : null;
  };

  const loadJobSessionMapFromStorage = () => {
    const storageKey = getJobSessionMapStorageKey();
    if (!storageKey) return {};
    try {
      const stored = localStorage.getItem(storageKey);
      if (stored) {
        return JSON.parse(stored);
      }
    } catch (error) {
      console.error('Error loading job session map from localStorage:', error);
    }
    return {};
  };

  const saveJobSessionMapToStorage = useCallback((data) => {
    const storageKey = getJobSessionMapStorageKey();
    if (!storageKey) return;
    try {
      localStorage.setItem(storageKey, JSON.stringify(data));
    } catch (error) {
      console.error('Error saving job session map to localStorage:', error);
    }
  }, []);

  const jobSessionMapRef = useRef(loadJobSessionMapFromStorage());
  const activeJobsRef = useRef({});

  const cloneUploadedFiles = (files = []) => {
    if (!Array.isArray(files)) {
      return [];
    }
    return files.map(file => ({
      ...file,
      s3_key: file?.s3_key,
      filename: file?.filename,
      unique_filename: file?.unique_filename,
      bucket_name: file?.bucket_name,
      type: file?.type
    }));
  };

  const cloneRedlinedDocuments = (docs = []) => {
    if (!Array.isArray(docs)) {
      return [];
    }
    return docs.map(doc => ({
      ...doc,
      originalFile: doc?.originalFile ? { ...doc.originalFile } : undefined,
      redlinedDocument: doc?.redlinedDocument,
      analysis: doc?.analysis,
      success: doc?.success,
      processing: doc?.processing,
      jobId: doc?.jobId,
      status: doc?.status,
      progress: doc?.progress,
      message: doc?.message,
    }));
  };

  // NOTE: Cleanup logic has been moved to the backend.
  // Backend automatically cancels old jobs when a new one starts and returns only the most recent active job per session.
  // Frontend just displays what the backend returns - no decision-making needed.

  const persistSessionState = useCallback((sessionId, partialState = {}) => {
    if (!sessionId) {
      return;
    }

    if (!sessionDataRef.current[sessionId]) {
      sessionDataRef.current[sessionId] = {
        uploadedFiles: [],
        redlinedDocuments: [],
        generating: false,
        processingStage: '',
        completedStages: [],
        workflowMessage: '',
        workflowMessageType: '',
        termsProfile: 'it'
      };
    }

    const currentState = sessionDataRef.current[sessionId];
    const nextState = {
      ...currentState
    };

    if (partialState.uploadedFiles !== undefined) {
      nextState.uploadedFiles = cloneUploadedFiles(partialState.uploadedFiles);
    }

    if (partialState.redlinedDocuments !== undefined) {
      nextState.redlinedDocuments = cloneRedlinedDocuments(partialState.redlinedDocuments);
    }

    if (partialState.completedStages !== undefined) {
      nextState.completedStages = Array.isArray(partialState.completedStages)
        ? [...partialState.completedStages]
        : [];
    }

    const scalarKeys = [
      'generating',
      'processingStage',
      'workflowMessage',
      'workflowMessageType',
      'hasWebSocketUpdates',
      'lastWebSocketUpdate',
      'termsProfile'
    ];

    scalarKeys.forEach(key => {
      if (partialState[key] !== undefined) {
        nextState[key] = partialState[key];
      }
    });

    sessionDataRef.current[sessionId] = nextState;
    saveSessionDataToStorage(sessionDataRef.current);
  }, [saveSessionDataToStorage]);

  const updateGlobalProcessingFlag = useCallback((sessionId, isProcessing) => {
    if (!sessionId) {
      return;
    }

    if (!window.processingSessionFlags) {
      window.processingSessionFlags = {};
    }

    if (isProcessing) {
      window.processingSessionFlags[sessionId] = {
        updatedAt: Date.now()
      };
    } else {
      delete window.processingSessionFlags[sessionId];
    }
  }, []);
  
  // Workflow state for this session
  const [uploadedFiles, setUploadedFiles] = useState([]);
  const [generating, setGenerating] = useState(false);
  const [workflowMessage, setWorkflowMessage] = useState('');
  const [workflowMessageType, setWorkflowMessageType] = useState('');
  const [processingStage, setProcessingStage] = useState(''); // 'kb_sync', 'document_review', 'generating'
  const [completedStages, setCompletedStages] = useState([]); // array of completed stage keys
  const [redlinedDocuments, setRedlinedDocuments] = useState([]);
  const [sessionResults, setSessionResults] = useState([]);
  const [loadingResults, setLoadingResults] = useState(false);
  const [termsProfile, setTermsProfile] = useState('it');
  const [termsProfileError, setTermsProfileError] = useState('');
  
  // ← NEW KB SYNC STATE
  // eslint-disable-next-line no-unused-vars
  const [kbSyncStatus, setKbSyncStatus] = useState('unknown'); // 'syncing', 'ready', 'unknown'
  // eslint-disable-next-line no-unused-vars
  const [kbSyncProgress, setKbSyncProgress] = useState(0);
  // eslint-disable-next-line no-unused-vars
  const [kbSyncMessage, setKbSyncMessage] = useState('');
  
  // Track initial session state to avoid issues with location.state being cleared
  const initialIsNewSessionRef = useRef(null);
  const currentSessionIdRef = useRef(null);
  
  // Initialize ref on first render and when session changes
  if (session?.session_id !== currentSessionIdRef.current) {
    currentSessionIdRef.current = session?.session_id;
    // Initialize with current location.state value
    initialIsNewSessionRef.current = location.state?.session?.session_id === session?.session_id;
  }
  
  // Determine if this is a new session (came from navigation state) or existing session (clicked from sidebar)
  // Use ref to persist the initial state, as location.state can be cleared
  const isNewSession = initialIsNewSessionRef.current ?? false;
  const normalizedTermsProfile = (termsProfile || 'it').toLowerCase();
  const termsProfileOptions = [
    {
      value: 'general',
      label: 'General Terms & Conditions',
      description: 'Statewide contract standards for broad procurement needs.',
    },
    {
      value: 'it',
      label: 'IT Terms & Conditions',
      description: 'Technology-focused standards for software and systems.',
    }
  ];
  const activeTermsProfileOption = termsProfileOptions.find(option => option.value === normalizedTermsProfile) || termsProfileOptions[0];

  const handleTermsProfileSelection = (nextValue) => {
    const normalized = (nextValue || '').toLowerCase();
    if (normalized === normalizedTermsProfile) {
      return;
    }
    setTermsProfile(normalized === 'general' ? 'general' : 'it');
    setTermsProfileError('');
  };

  // Keep session data ref in sync with current state (for current session)
  // Only sync if we're not in the middle of switching sessions
  useEffect(() => {
    // Only sync if:
    // 1. We have a session
    // 2. The ref matches the current session (means we're not switching)
    // 3. The session exists in sessionDataRef (means it's been initialized)
    if (session?.session_id && 
        previousSessionIdRef.current === session.session_id &&
        sessionDataRef.current[session.session_id]) {
      persistSessionState(session.session_id, {
        uploadedFiles,
        redlinedDocuments,
        generating,
        processingStage,
        completedStages,
        workflowMessage,
        workflowMessageType,
        termsProfile
      });
    }
  }, [session?.session_id, uploadedFiles, redlinedDocuments, generating, processingStage, completedStages, workflowMessage, workflowMessageType, termsProfile, persistSessionState]);


  // Reset processing state and load session results when session changes
  useEffect(() => {
    if (session?.session_id) {
      const currentSessionId = session.session_id;
      const previousSessionId = previousSessionIdRef.current;
      
      // Clear any existing progress interval when switching sessions
      if (window.progressInterval) {
        clearInterval(window.progressInterval);
        window.progressInterval = null;
      }
      
      // Save previous session's data BEFORE switching (if we had a previous session)
      if (previousSessionId && previousSessionId !== currentSessionId) {
        // Backend handles cleanup - just save current state
        persistSessionState(previousSessionId, {
          uploadedFiles,
          redlinedDocuments,
          generating,
          processingStage,
          completedStages,
          workflowMessage,
          workflowMessageType
        });
      }
      
      // Check if this is a new session (not in sessionDataRef yet)
      // New sessions should start with empty state
      const isNewSession = !sessionDataRef.current[currentSessionId];
      
      // Load this session's data (or initialize empty if new session)
      const sessionData = sessionDataRef.current[currentSessionId] || {
        uploadedFiles: [],
        redlinedDocuments: [],
        generating: false,
        processingStage: '',
        completedStages: [],
        workflowMessage: '',
        workflowMessageType: '',
        termsProfile: 'it'
      };
      
      // If this is a new session, explicitly initialize it as empty in the ref and set state to empty FIRST
      // This must happen before updating previousSessionIdRef to prevent sync effect from saving old data
      if (isNewSession) {
        sessionDataRef.current[currentSessionId] = {
          uploadedFiles: [],
          redlinedDocuments: [],
          generating: false,
          processingStage: '',
          completedStages: [],
          workflowMessage: '',
          workflowMessageType: '',
          termsProfile: 'it'
        };
        // Save to localStorage for new session initialization
        saveSessionDataToStorage(sessionDataRef.current);
        // Force state to empty for new sessions IMMEDIATELY
        setUploadedFiles([]);
        setRedlinedDocuments([]);
        setGenerating(false);
        setProcessingStage('');
        setCompletedStages([]);
        setWorkflowMessage('');
        setWorkflowMessageType('');
        setTermsProfile('it');
        setTermsProfileError('');
        // Update ref AFTER clearing state to prevent sync from running with old data
        previousSessionIdRef.current = currentSessionId;
      } else {
        // Restore session-specific data for existing sessions
        // Use functional updates to ensure we don't lose data during async operations
        // Deep copy to avoid reference issues and ensure proper restoration
        setUploadedFiles(() => {
          if (!sessionData.uploadedFiles || sessionData.uploadedFiles.length === 0) {
            return [];
          }
          // Deep copy each file object to ensure proper restoration
          return sessionData.uploadedFiles.map(file => ({
            ...file,
            s3_key: file.s3_key,
            filename: file.filename,
            unique_filename: file.unique_filename,
            bucket_name: file.bucket_name,
            type: file.type
          }));
        });
        setRedlinedDocuments(() => {
          if (!sessionData.redlinedDocuments || sessionData.redlinedDocuments.length === 0) {
            return [];
          }
          
          // Backend handles cleanup - just restore what's in localStorage
          // Backend will return only the most recent active job when we call loadSessionResults
          return sessionData.redlinedDocuments.map(doc => ({
            ...doc,
            originalFile: doc.originalFile ? { ...doc.originalFile } : undefined,
            redlinedDocument: doc.redlinedDocument,
            analysis: doc.analysis,
            success: doc.success,
            processing: doc.processing,
            jobId: doc.jobId,
            status: doc.status,
            progress: doc.progress
          }));
        });
        const restoredGenerating = sessionData.generating === true;
        const restoredCompletedStages = Array.isArray(sessionData.completedStages)
          ? sessionData.completedStages
          : [];
        const fallbackStage = restoredCompletedStages.length > 0
          ? restoredCompletedStages[restoredCompletedStages.length - 1]
          : 'kb_sync';
        const restoredStage = restoredGenerating
          ? (sessionData.processingStage || fallbackStage)
          : '';
        const restoredMessage = sessionData.workflowMessage || (
          restoredGenerating && restoredStage
            ? stageMessages[restoredStage] || ''
            : ''
        );
        const restoredMessageType = sessionData.workflowMessageType || (
          restoredGenerating
            ? (restoredMessage ? 'progress' : '')
            : (restoredMessage ? 'success' : '')
        );

        setGenerating(restoredGenerating);
        setProcessingStage(restoredGenerating ? restoredStage : '');
        setCompletedStages(restoredCompletedStages);
        setWorkflowMessage(restoredMessage || '');
        setWorkflowMessageType(restoredMessageType || '');
        setTermsProfile(sessionData.termsProfile || 'it');
        setTermsProfileError('');
        // Update ref AFTER restoring state for existing sessions
        previousSessionIdRef.current = currentSessionId;
      }
      
      // Load session results and setup WebSocket for the new session
      // Pass the restored session data so loadSessionResults can merge properly
      loadSessionResults(sessionData.redlinedDocuments);
      setupWebSocket();
    }
    
    // Cleanup WebSocket and progress on unmount
    return () => {
      cleanupWebSocket();
      // Clean up progress interval when component unmounts
      // Note: This will clear the progress indicator, but processing continues in background
      // The WebSocket will still receive completion notifications if the user returns to this session
      if (window.progressInterval) {
        clearInterval(window.progressInterval);
        window.progressInterval = null;
      }
    };
  }, [session?.session_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadSessionResults = async (restoredRedlinedDocs = []) => {
    const currentSessionId = session?.session_id;
    let sessionData = currentSessionId
      ? sessionDataRef.current[currentSessionId]
      : undefined;

    try {
      setLoadingResults(true);
      const userId = authService.getUserId();
      
      // Check if this session has recent WebSocket updates that should be prioritized
      const hasRecentWebSocketUpdates = sessionData?.hasWebSocketUpdates && 
        sessionData?.lastWebSocketUpdate &&
        (Date.now() - sessionData.lastWebSocketUpdate) < 60000; // Within last 60 seconds
      
      if (hasRecentWebSocketUpdates) {
        console.log(`Session ${session.session_id} has recent WebSocket updates. Prioritizing localStorage data.`);
      }
      
      // Always try to load results - the backend will return empty if none exist
      const response = await sessionAPI.getSessionResults(session.session_id, userId);
      
      // Handle response structure (wrapped in body or direct)
      let responseData = response;
      if (response && response.body) {
        try {
          responseData = typeof response.body === 'string' ? JSON.parse(response.body) : response.body;
        } catch (e) {
          console.error('Error parsing session results response body:', e);
          responseData = response;
        }
      }
      
      if (responseData.success && responseData.results) {
        setSessionResults(responseData.results);
        
        // Convert sessionResults to redlinedDocuments format for display
        // This allows existing sessions to show their redline documents
        const redlinedDocsFromResults = responseData.results
          .filter(result => result.redlined_document_s3_key && 
                           typeof result.redlined_document_s3_key === 'string' && 
                           result.redlined_document_s3_key.trim() !== '')
          .map(result => ({
            originalFile: {
              filename: result.document_name || 'Unknown Document',
              s3_key: result.document_s3_key || ''
            },
            redlinedDocument: result.redlined_document_s3_key.trim(),
            analysis: result.analysis_id,
            success: true,
            processing: false,
            status: 'completed',
            progress: 100
          }));
        
        // Merge with restored session data and any in-progress documents
        let latestRedlinedDocs = [];
        setRedlinedDocuments(prev => {
          // Use restored session data as baseline if prev is empty (just restored)
          const baseline = prev.length === 0 && restoredRedlinedDocs.length > 0 
            ? restoredRedlinedDocs 
            : prev;
          
          // Backend returns only the most recent active job - just use what's in baseline
          const inProgress = baseline.filter(doc => doc.processing || doc.status === 'processing');
          
          // Create a map of existing documents by analysis_id, redlinedDocument key, or jobId
          const existingDocsMap = new Map();
          baseline.forEach(doc => {
            // Use jobId as primary key if available, otherwise use analysis_id or redlinedDocument
            const key = doc.jobId || doc.analysis || doc.redlinedDocument;
            if (key) {
              existingDocsMap.set(key, doc);
            }
          });
          
          const buildOriginalKey = originalFile => {
            if (!originalFile) return null;
            return `${originalFile.s3_key || ''}|${(originalFile.filename || '').toLowerCase()}`;
          };

          // Merge completed documents from database with existing ones
          // Preserve existing document structure if it has all required fields
          const mergedCompleted = redlinedDocsFromResults.map(doc => {
            // Try to find existing document by multiple keys
            // First try direct map lookup
            let existing = existingDocsMap.get(doc.analysis) || 
                          existingDocsMap.get(doc.redlinedDocument);
            
            // If not found, search by analysis_id (WebSocket docs might have jobId as key but analysis_id matches)
            if (!existing && doc.analysis) {
              for (const value of existingDocsMap.values()) {
                if (value.analysis === doc.analysis || value.jobId === doc.analysis) {
                  existing = value;
                  break;
                }
              }
            }

            // If still not found, attempt to match by original file identifiers
            if (!existing && doc.originalFile) {
              const docOriginalKey = buildOriginalKey(doc.originalFile);
              if (docOriginalKey) {
                existing = baseline.find(item => buildOriginalKey(item.originalFile) === docOriginalKey);
              }
            }
            
            // If existing document has all required fields and is complete, merge to preserve structure
            // IMPORTANT: Preserve jobId if it exists (from WebSocket updates)
            if (existing && (existing.redlinedDocument || existing.processing)) {
              // Merge: keep existing structure but update with any new data from DB
              return {
                ...existing,
                ...doc,
                // Ensure these critical fields are preserved from existing (WebSocket) state
                redlinedDocument: existing.redlinedDocument || doc.redlinedDocument,
                success: existing.success !== undefined ? existing.success : (doc.success !== undefined ? doc.success : true),
                originalFile: existing.originalFile || doc.originalFile,
                jobId: existing.jobId || doc.jobId, // Preserve jobId from WebSocket
                status: existing.status || doc.status || 'completed',
                progress: existing.progress !== undefined ? existing.progress : (doc.progress !== undefined ? doc.progress : 100),
                processing: existing.processing || false // Preserve processing state
              };
            }
            
            // New document from DB
            return doc;
          });
          
          // Track which existing documents were matched by DB results
          const matchedKeys = new Set();
          mergedCompleted.forEach(doc => {
            const key = doc.jobId || doc.analysis || doc.redlinedDocument;
            if (key) matchedKeys.add(key);
            const originalKey = buildOriginalKey(doc.originalFile);
            if (originalKey) matchedKeys.add(originalKey);
          });
          
          // Filter out duplicates from merged completed docs
          const seen = new Set();
          const uniqueCompleted = mergedCompleted.filter(doc => {
            const key = doc.jobId || doc.analysis || doc.redlinedDocument;
            if (key) {
              if (seen.has(key)) return false;
              seen.add(key);
            }
            const originalKey = buildOriginalKey(doc.originalFile);
            if (originalKey) {
              if (seen.has(originalKey)) return false;
              seen.add(originalKey);
            }
            return true;
          });
          
          // Keep existing completed documents that weren't matched by DB (e.g., WebSocket docs not yet saved)
          const unmatchedExisting = baseline.filter(doc => {
            // Skip in-progress (already handled above)
            if (doc.processing || doc.status === 'processing') return false;
            // Keep documents that weren't matched by DB results
            const key = doc.jobId || doc.analysis || doc.redlinedDocument;
            const originalKey = buildOriginalKey(doc.originalFile);
            if (key && matchedKeys.has(key)) return false;
            if (originalKey && matchedKeys.has(originalKey)) return false;
            return key || originalKey;
          });
          
          // If this session has recent WebSocket updates, prioritize those over DB results
          // This ensures that fresh WebSocket data isn't lost during the DB sync delay
          if (hasRecentWebSocketUpdates && unmatchedExisting.length > 0) {
            console.log(`Preserving ${unmatchedExisting.length} WebSocket-updated document(s) for session ${session.session_id}`);
          }
          
          latestRedlinedDocs = [...inProgress, ...uniqueCompleted, ...unmatchedExisting];
          return latestRedlinedDocs;
        });

        if (session?.session_id) {
          const hasProcessingDocs = Array.isArray(latestRedlinedDocs) && latestRedlinedDocs.some(doc =>
            doc?.processing === true ||
            doc?.status === 'processing' ||
            (typeof doc?.progress === 'number' && doc.progress < 100)
          );

          // Check for no conflicts case when processing is complete
          let completionMessage;
          let completionMessageType;
          if (hasProcessingDocs) {
            completionMessage = sessionData?.workflowMessage || stageMessages.generating || 'Processing documents. Please stand by.';
            completionMessageType = 'progress';
          } else {
            // Processing is complete - check if there are no conflicts
            const completedDocs = latestRedlinedDocs.filter(doc => !doc.processing && (doc.status === 'completed' || doc.success === true));
            const successfulDocs = completedDocs.filter(doc => doc.success === true && doc.redlinedDocument);
            const noConflictsDocs = completedDocs.filter(doc => doc.success === true && !doc.redlinedDocument);
            
            if (noConflictsDocs.length > 0 && successfulDocs.length === 0) {
              // All documents completed but no conflicts found
              if (noConflictsDocs.length === 1) {
                completionMessage = 'Document analysis completed. No conflicts were detected, so no redlined document was generated.';
              } else {
                completionMessage = `Document analysis completed. No conflicts were detected in any documents, so no redlined documents were generated.`;
              }
              completionMessageType = 'success';
            } else {
              // Use existing workflowMessage if available, otherwise default success message
              completionMessage = sessionData?.workflowMessage || 'Document processing completed successfully!';
              completionMessageType = 'success';
            }
          }

          persistSessionState(session.session_id, {
            redlinedDocuments: latestRedlinedDocs,
            generating: hasProcessingDocs,
            processingStage: hasProcessingDocs ? (sessionData?.processingStage || 'generating') : '',
            completedStages: hasProcessingDocs
              ? (sessionData?.completedStages || [])
              : stageOrder.map(item => item.key),
            workflowMessage: completionMessage,
            workflowMessageType: completionMessageType
          });

          updateGlobalProcessingFlag(session.session_id, hasProcessingDocs);
        }
      } else {
        // Session might not have results yet
        // BUT: Don't clear redlinedDocuments if they exist from WebSocket updates
        // Only clear sessionResults, preserve any WebSocket-updated documents
        setSessionResults([]);
        // Preserve restored session data - don't clear if we have restored documents
        let latestRedlinedDocs = [];
        setRedlinedDocuments(prev => {
          // If we have restored session data, use it as baseline
          const baseline = prev.length === 0 && restoredRedlinedDocs.length > 0 
            ? restoredRedlinedDocs 
            : prev;
          // Always preserve existing documents - they might be from session restore or WebSocket
          latestRedlinedDocs = baseline;
          return latestRedlinedDocs;
        });

        if (session?.session_id) {
          const hasProcessingDocs = Array.isArray(latestRedlinedDocs) && latestRedlinedDocs.some(doc =>
            doc?.processing === true ||
            doc?.status === 'processing' ||
            (typeof doc?.progress === 'number' && doc.progress < 100)
          );

          persistSessionState(session.session_id, {
            redlinedDocuments: latestRedlinedDocs,
            generating: hasProcessingDocs,
            processingStage: hasProcessingDocs ? (sessionData?.processingStage || 'generating') : '',
            completedStages: hasProcessingDocs
              ? (sessionData?.completedStages || [])
              : stageOrder.map(item => item.key),
            workflowMessage: hasProcessingDocs
              ? (sessionData?.workflowMessage || stageMessages.generating || 'Processing documents. Please stand by.')
              : (sessionData?.workflowMessage || ''),
            workflowMessageType: hasProcessingDocs
              ? (sessionData?.workflowMessageType || 'progress')
              : (sessionData?.workflowMessageType || (latestRedlinedDocs.length > 0 ? 'success' : ''))
          });

          updateGlobalProcessingFlag(session.session_id, hasProcessingDocs);
        }
      }
    } catch (error) {
      // Don't log as error for new sessions - they won't have results yet
      if (session?.has_results) {
        console.error('Error loading session results:', error);
      }
      setSessionResults([]);
      // Preserve restored session data on error
      let latestRedlinedDocs = [];
      setRedlinedDocuments(prev => {
        // If we have restored session data, use it as baseline
        const baseline = prev.length === 0 && restoredRedlinedDocs.length > 0 
          ? restoredRedlinedDocs 
          : prev;
        latestRedlinedDocs = baseline;
        return latestRedlinedDocs;
      });

      if (session?.session_id) {
        const hasProcessingDocs = Array.isArray(latestRedlinedDocs) && latestRedlinedDocs.some(doc =>
          doc?.processing === true ||
          doc?.status === 'processing' ||
          (typeof doc?.progress === 'number' && doc.progress < 100)
        );

        persistSessionState(session.session_id, {
          redlinedDocuments: latestRedlinedDocs,
          generating: hasProcessingDocs,
          processingStage: hasProcessingDocs ? (sessionData?.processingStage || 'generating') : '',
          completedStages: hasProcessingDocs
            ? (sessionData?.completedStages || [])
            : stageOrder.map(item => item.key),
          workflowMessage: hasProcessingDocs
            ? (sessionData?.workflowMessage || stageMessages.generating || 'Processing documents. Please stand by.')
            : (sessionData?.workflowMessage || ''),
          workflowMessageType: hasProcessingDocs
            ? (sessionData?.workflowMessageType || 'progress')
            : (sessionData?.workflowMessageType || '')
        });

        updateGlobalProcessingFlag(session.session_id, hasProcessingDocs);
      }
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
    const mappedSessionId = jobSessionMapRef.current[job_id] || session_id || activeJobsRef.current[job_id]?.sessionId;

    if (!mappedSessionId) {
      return;
    }

    // Update UI with progress if this session is currently visible
    if (mappedSessionId === session?.session_id) {
      const inferredStage = data.stage || statusToStageMap[data.status];
      if (inferredStage) {
        setProcessingPhase(inferredStage, data.message);
      } else if (data.message) {
        setWorkflowMessage(data.message);
        setWorkflowMessageType('progress');
      }
      
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

    if (activeJobsRef.current[job_id]) {
      activeJobsRef.current[job_id].lastProgress = Date.now();
    }
  };

  const handleJobCompleted = async (message) => {
    const { job_id, session_id, data } = message;
    const mappedSessionId = jobSessionMapRef.current[job_id] || session_id || activeJobsRef.current[job_id]?.sessionId;

    if (!mappedSessionId) {
      console.warn('Received job_completed without session mapping', { job_id, session_id, data });
      return;
    }
    
    const isCurrentSession = mappedSessionId === session?.session_id;
    
    const reconcileDocuments = (docs = []) => {
      let matched = false;
      // Backend handles cleanup - just update the completing job
      const updatedDocs = docs.map(doc => {
        if (!matched && (doc.jobId === job_id || (data.analysis_id && doc.analysis === data.analysis_id))) {
          matched = true;
          const redlinedSuccess = data.redlined_document && data.redlined_document.success;
          const redlinedDoc = data.redlined_document?.redlined_document;
          const hasNoConflicts = data.redlined_document?.no_conflicts === true;
          const hasRedline = redlinedSuccess && redlinedDoc && !hasNoConflicts;
          
          // Check for Lambda timeout errors from backend
          const errorMessage = data.redlined_document?.error || data.error || '';
          const isTimeoutError = errorMessage.toLowerCase().includes('timeout') || 
                                 errorMessage.toLowerCase().includes('timed out') ||
                                 errorMessage.toLowerCase().includes('task timed out') ||
                                 (data.status === 'failed' && errorMessage.toLowerCase().includes('lambda'));
          
          const finalError = redlinedSuccess 
            ? undefined 
            : (isTimeoutError 
                ? 'Redline failed to generate due to timeout. The document review process exceeded the maximum processing time. Please try again with a smaller document or contact support.'
                : errorMessage || 'Failed to generate redlined document');
          
          const finalMessage = redlinedSuccess 
            ? (hasNoConflicts || !hasRedline ? 'No conflicts detected' : 'Document processing completed')
            : (isTimeoutError 
                ? 'Redline generation timed out'
                : 'Document processing failed');
          
          return {
            ...doc,
            status: redlinedSuccess ? 'completed' : 'failed',
            progress: 100,
            success: redlinedSuccess,
            redlinedDocument: hasNoConflicts ? undefined : (redlinedDoc || doc.redlinedDocument),
            analysis: data.analysis_id || doc.analysis,
            processing: false,
            error: finalError,
            message: finalMessage
          };
        }
        return doc;
      });
      if (!matched && data.redlined_document && data.redlined_document.success) {
        const jobMeta = activeJobsRef.current[job_id];
        const hasNoConflicts = data.redlined_document.no_conflicts === true || 
                               (!data.redlined_document.redlined_document && data.redlined_document.success);
        updatedDocs.push({
          originalFile: jobMeta?.vendorFile ? { ...jobMeta.vendorFile } : { filename: `Document for job ${job_id}` },
          redlinedDocument: data.redlined_document.redlined_document,
          analysis: data.analysis_id,
          success: true,
          processing: false,
          jobId: job_id,
          status: 'completed',
          progress: 100,
          message: hasNoConflicts ? 'No conflicts detected' : 'Document processing completed'
        });
      }
      const stillProcessing = updatedDocs.some(doc => doc.processing === true || doc.status === 'processing');
      return { updatedDocs, stillProcessing };
    };
    
    const persistSessionCompletion = (targetSessionId) => {
      if (!sessionDataRef.current[targetSessionId]) {
        sessionDataRef.current[targetSessionId] = {
          uploadedFiles: [],
          redlinedDocuments: [],
          generating: false,
          processingStage: '',
          completedStages: [],
          workflowMessage: '',
          workflowMessageType: ''
        };
      }
      const entry = sessionDataRef.current[targetSessionId];
      const { updatedDocs, stillProcessing } = reconcileDocuments(entry.redlinedDocuments);
      entry.redlinedDocuments = updatedDocs;
      entry.generating = stillProcessing;

      if (stillProcessing) {
        entry.processingStage = entry.processingStage || 'document_review';
        entry.completedStages = Array.isArray(entry.completedStages) ? entry.completedStages : [];
        entry.workflowMessage = entry.workflowMessage || stageMessages[entry.processingStage] || 'Processing documents. Please stand by.';
        entry.workflowMessageType = entry.workflowMessageType || 'progress';
      } else {
        entry.processingStage = '';
        entry.completedStages = stageOrder.map(item => item.key);
        
        // Determine completion message based on document states
        const completedDocs = updatedDocs.filter(doc => !doc.processing && doc.status === 'completed');
        const failedDocs = updatedDocs.filter(doc => doc.status === 'failed' || (doc.success === false && !doc.processing));
        const successfulDocs = completedDocs.filter(doc => doc.success === true && doc.redlinedDocument);
        const noConflictsDocs = completedDocs.filter(doc => doc.success === true && !doc.redlinedDocument);
        
        let completionMessage = 'Document processing completed successfully!';
        let completionType = 'success';
        
        if (failedDocs.length > 0) {
          // Some documents failed
          if (failedDocs.length === updatedDocs.length) {
            completionMessage = 'Document processing failed. Please check your documents and try again.';
            completionType = 'error';
          } else {
            completionMessage = `Processing completed. ${failedDocs.length} document(s) failed to generate redlines.`;
            completionType = 'error';
          }
        } else if (noConflictsDocs.length > 0 && successfulDocs.length === 0) {
          // All documents completed but no conflicts found (no redlines generated)
          if (noConflictsDocs.length === 1) {
            completionMessage = 'Document analysis completed. No conflicts were detected, so no redlined document was generated.';
          } else {
            completionMessage = `Document analysis completed. No conflicts were detected in any documents, so no redlined documents were generated.`;
          }
          completionType = 'success';
        } else if (successfulDocs.length > 0) {
          // At least one successful redline generated
          if (successfulDocs.length === updatedDocs.length) {
            completionMessage = `Successfully generated ${successfulDocs.length} redlined document(s)!`;
          } else {
            completionMessage = `Successfully generated ${successfulDocs.length} redlined document(s). ${noConflictsDocs.length} document(s) had no conflicts.`;
          }
          completionType = 'success';
        }
        
        entry.workflowMessage = completionMessage;
        entry.workflowMessageType = completionType;
      }

      // Mark this session as having WebSocket updates that need to be preserved
      entry.hasWebSocketUpdates = true;
      entry.lastWebSocketUpdate = Date.now();
      saveSessionDataToStorage(sessionDataRef.current);
      updateGlobalProcessingFlag(targetSessionId, stillProcessing);
      return { 
        stillProcessing, 
        completionMessage: entry.workflowMessage, 
        completionType: entry.workflowMessageType 
      };
    };
    
    if (isCurrentSession) {
      if (window.progressInterval) {
        clearInterval(window.progressInterval);
        window.progressInterval = null;
      }
      
      let stillProcessingAfterUpdate = false;
      setRedlinedDocuments(prev => {
        const { updatedDocs, stillProcessing } = reconcileDocuments(prev);
        stillProcessingAfterUpdate = stillProcessing;
        return updatedDocs;
      });
      
      const { stillProcessing, completionMessage, completionType } = persistSessionCompletion(mappedSessionId);
      stillProcessingAfterUpdate = stillProcessingAfterUpdate || stillProcessing;
      if (!stillProcessingAfterUpdate) {
        markProcessingComplete(completionMessage, completionType);
        setGenerating(false);
      } else {
        setProcessingPhase('generating', stageMessages.generating);
      }
      
      setTimeout(async () => {
        try {
          await loadSessionResults();
        } catch (error) {
          console.warn('Failed to reload session results after job completion:', error);
        }
      }, 2000);
    } else {
      // For background sessions, persist the completion
      persistSessionCompletion(mappedSessionId);
      console.log(`Background session ${mappedSessionId} received completion for job ${job_id}. Data saved to localStorage for later retrieval.`);
    }

    if (jobSessionMapRef.current[job_id]) {
      delete jobSessionMapRef.current[job_id];
      saveJobSessionMapToStorage(jobSessionMapRef.current);
    }
    if (activeJobsRef.current[job_id]) {
      delete activeJobsRef.current[job_id];
    }
    if (window.currentProcessingJob && (window.currentProcessingJob.jobId === job_id || window.currentProcessingJob.sessionId === mappedSessionId)) {
      window.currentProcessingJob = null;
    }
  };

  const handleSessionUpdate = (message) => {

    // Handle session updates (e.g., title changes, status updates)
  };

  const handleWebSocketError = (message) => {

    setWorkflowMessage(`WebSocket error: ${message.message}`);
    setWorkflowMessageType('error');
  };

  // Polling function for real-time job status updates
  const pollJobStatus = async (jobId, sessionIdAtStart, filename, isCurrentSession) => {
    const maxAttempts = 120; // 10 minutes with 5-second intervals
    let attempts = 0;
    
    // Map backend stages to frontend stages
    const backendToFrontendStage = {
      'starting': 'kb_sync',
      'initialized': 'kb_sync',
      'splitting': 'kb_sync',
      'processing_chunks': 'document_review',
      'merging_results': 'document_review',
      'retrieving_context': 'document_review',
      'generating_analysis': 'document_review',
      'identifying_conflicts': 'generating',
      'generating_redlines': 'generating',
      'assembling_document': 'generating',
      'finalizing': 'generating',
      'completed': 'generating',
      'failed': ''
    };
    
    while (attempts < maxAttempts) {
      try {
        const statusResponse = await agentAPI.getJobStatus(jobId);
        
        if (statusResponse.success) {
          // Backend returns: { success: true, job_id, stage, progress, label, status, session_id, document_s3_key, ... }
          // Progress is already a number (int) from backend (converted from DynamoDB Decimal)
          const actualStatus = statusResponse.status;
          const actualStage = statusResponse.stage;
          
          // Ensure progress is a valid number between 0-100
          let actualProgress = 0;
          if (statusResponse.progress !== undefined && statusResponse.progress !== null) {
            if (typeof statusResponse.progress === 'number') {
              actualProgress = Math.max(0, Math.min(100, statusResponse.progress)); // Clamp between 0-100
            } else {
              const parsed = parseInt(statusResponse.progress, 10);
              actualProgress = isNaN(parsed) ? 0 : Math.max(0, Math.min(100, parsed));
            }
          }
          
          const actualLabel = statusResponse.label;
          const actualError = statusResponse.error_message || statusResponse.error;
          const result = statusResponse.result;
          
          console.log(`Polling job ${jobId}: status=${actualStatus}, stage=${actualStage}, progress=${actualProgress}, progressType=${typeof statusResponse.progress}, rawProgress=${statusResponse.progress}, finalProgress=${actualProgress}`);
          
          // Update UI with real-time progress
          if (isCurrentSession()) {
            const frontendStage = backendToFrontendStage[actualStage] || 'document_review';
            
            // Update processing phase with label only (no description)
            if (actualStatus === 'processing') {
              setProcessingPhase(frontendStage, actualLabel);
              
              // Update the redlined documents with progress
              setRedlinedDocuments(prev => {
                const foundDoc = prev.find(doc => doc.jobId === jobId);
                console.log(`Looking for doc with jobId=${jobId}, found:`, foundDoc ? `yes (current progress=${foundDoc.progress})` : 'no');
                console.log(`All docs:`, prev.map(d => ({ jobId: d.jobId, progress: d.progress, status: d.status })));
                
                const updated = prev.map(doc => {
                  if (doc.jobId === jobId) {
                    const updatedDoc = {
                      ...doc,
                      progress: actualProgress,
                      status: 'processing',
                      message: `${actualLabel} (${actualProgress}%)`,
                      processing: true  // Ensure processing flag is set
                    };
                    console.log(`Updating doc ${jobId} progress from ${doc.progress} to ${actualProgress}%`);
                    return updatedDoc;
                  }
                  return doc;
                });
                
                const afterUpdate = updated.find(doc => doc.jobId === jobId);
                console.log(`After update, doc progress:`, afterUpdate?.progress);
                
                return updated;
              });
            }
          }
          
          // Check for failure FIRST (before completion check)
          if (actualStatus === 'failed') {
            console.log(`Job ${jobId} failed: ${actualError}`);
            
            // Clean up job tracking
            if (jobSessionMapRef.current[jobId]) {
              delete jobSessionMapRef.current[jobId];
              saveJobSessionMapToStorage(jobSessionMapRef.current);
            }
            if (activeJobsRef.current[jobId]) {
              delete activeJobsRef.current[jobId];
            }
            if (window.currentProcessingJob && window.currentProcessingJob.jobId === jobId) {
              window.currentProcessingJob = null;
            }
            
            // Trigger session sidebar refresh to show failed status
            if (window.triggerSessionSidebarRefresh) {
              window.triggerSessionSidebarRefresh();
            }
            
            // Update UI immediately to show error
            if (isCurrentSession()) {
              setRedlinedDocuments(prev => {
                const updated = prev.map(doc => {
                  if (doc.jobId === jobId) {
                    return {
                      ...doc,
                      status: 'failed',
                      progress: 0,
                      processing: false,
                      message: actualError || 'Processing failed',
                      success: false
                    };
                  }
                  return doc;
                });
                
                // Clean up: remove failed jobs without results after a delay (let user see error first)
                // But remove immediately if there's a new processing job
                const hasNewProcessingJob = updated.some(doc => 
                  doc.processing && doc.jobId !== jobId
                );
                
                if (hasNewProcessingJob) {
                  // Remove failed job immediately if there's a new processing job
                  return updated.filter(doc => doc.jobId !== jobId || doc.redlinedDocument || doc.analysis);
                }
                
                return updated;
              });
            }
            
            // Stop polling immediately
            return {
              success: false,
              processing: false,
              error: actualError || 'Processing failed'
            };
          }
          
          // Check for completion
          // If status is completed, stop polling even if result is not yet available
          // (Step Functions may have succeeded but DynamoDB update is delayed)
          if (actualStatus === 'completed') {
            // Clean up job tracking
            if (jobSessionMapRef.current[jobId]) {
              delete jobSessionMapRef.current[jobId];
              saveJobSessionMapToStorage(jobSessionMapRef.current);
            }
            if (activeJobsRef.current[jobId]) {
              delete activeJobsRef.current[jobId];
            }
            if (window.currentProcessingJob && window.currentProcessingJob.jobId === jobId) {
              window.currentProcessingJob = null;
            }
            
            // Trigger session sidebar refresh to show completed status
            if (window.triggerSessionSidebarRefresh) {
              window.triggerSessionSidebarRefresh();
            }
            
            // Update UI to mark job as completed
            if (isCurrentSession()) {
              setRedlinedDocuments(prev => prev.map(doc => {
                if (doc.jobId === jobId) {
                  return {
                    ...doc,
                    status: 'completed',
                    progress: 100,
                    processing: false,
                    success: result ? (result.has_redlines || result.conflicts_found === 0) : true
                  };
                }
                return doc;
              }));
            }
            
            // If we have result data, return it
            if (result) {
              return {
                success: true,
                processing: false,
                redlined_document: result.redlined_document,
                analysis: result.analysis,
                has_redlines: result.has_redlines,
                conflicts_found: result.conflicts_found
              };
            } else {
              // Status is completed but result not available yet - stop polling
              // The result will be available when user refreshes or checks later
              return {
                success: true,
                processing: false,
                message: 'Processing completed. Results will be available shortly.'
              };
            }
          }
        } else {
          // API returned success: false
          const errorMsg = statusResponse.error || 'Failed to get job status';
          console.error('Job status API error:', errorMsg);
          
          // Update UI to show error
          if (isCurrentSession()) {
            setRedlinedDocuments(prev => prev.map(doc => {
              if (doc.jobId === jobId) {
                return {
                  ...doc,
                  status: 'failed',
                  progress: 100,
                  processing: false,
                  message: errorMsg,
                  success: false
                };
              }
              return doc;
            }));
          }
          
          // Continue polling in case it's a transient error
        }
        
        // Wait before next poll (5 seconds)
        await new Promise(resolve => setTimeout(resolve, 5000));
        attempts++;
        
      } catch (pollError) {
        console.warn('Polling error:', pollError);
        // Continue polling even on error
        await new Promise(resolve => setTimeout(resolve, 5000));
        attempts++;
      }
    }
    
    // Timeout - but job may still be running
    return {
      success: false,
      processing: true,
      error: 'Processing is taking longer than expected. The job will continue in the background.',
      job_id: jobId
    };
  };

  // Unified stage management without artificial percentage tracking
  const resetProcessingStages = (options = {}) => {
    setProcessingStage('');
    setCompletedStages([]);
    highestStageIndexRef.current = -1;
    
    if (session?.session_id) {
      const payload = {
        processingStage: '',
        completedStages: []
      };

      if (options.keepGeneratingState === true) {
        payload.generating = true;
      } else if (options.keepGeneratingState === false) {
        payload.generating = false;
      } else {
        payload.generating = false;
      }

      if (!options.skipWorkflowReset) {
        payload.workflowMessage = '';
        payload.workflowMessageType = '';
      }

      persistSessionState(session.session_id, payload);
      if (options.keepGeneratingState !== true) {
        updateGlobalProcessingFlag(session.session_id, false);
      }
    }
  };

  const setProcessingPhase = (stage, message) => {
    if (!stage) {
      resetProcessingStages({ skipWorkflowReset: true });
      if (message) {
        setWorkflowMessage(message);
        setWorkflowMessageType('progress');
      } else {
        setWorkflowMessage('');
        setWorkflowMessageType('');
      }
      if (session?.session_id) {
        persistSessionState(session.session_id, {
          workflowMessage: message || '',
          workflowMessageType: message ? 'progress' : '',
          generating: false
        });
        updateGlobalProcessingFlag(session.session_id, false);
      }
      return;
    }

    const stageIndex = stageOrder.findIndex(item => item.key === stage);
    if (stageIndex === -1) {
      const fallbackMessage = message || '';
      setWorkflowMessage(fallbackMessage);
      setWorkflowMessageType(fallbackMessage ? 'progress' : '');
      return;
    }

    const currentActiveIndex = processingStage
      ? stageOrder.findIndex(item => item.key === processingStage)
      : -1;
    const highestReachedIndex = Math.max(
      highestStageIndexRef.current,
      currentActiveIndex
    );

    if (highestReachedIndex >= 0 && stageIndex < highestReachedIndex && stage !== processingStage) {
      if (message) {
        setWorkflowMessage(message);
        setWorkflowMessageType('progress');
      }
      return;
    }

    let updatedCompleted = [];
    setCompletedStages(prev => {
      const next = new Set(prev);
      stageOrder.forEach((item, idx) => {
        if (idx < stageIndex) {
          next.add(item.key);
        }
      });
      updatedCompleted = Array.from(next);
      return updatedCompleted;
    });

    setProcessingStage(stage);
    const resolvedMessage = message || stageMessages[stage] || '';
    setWorkflowMessage(resolvedMessage);
    setWorkflowMessageType(resolvedMessage ? 'progress' : '');
    if (stageIndex > highestStageIndexRef.current) {
      highestStageIndexRef.current = stageIndex;
    }

    if (session?.session_id) {
      persistSessionState(session.session_id, {
        processingStage: stage,
        completedStages: updatedCompleted,
        workflowMessage: resolvedMessage,
        workflowMessageType: resolvedMessage ? 'progress' : '',
        generating: true
      });
      updateGlobalProcessingFlag(session.session_id, true);
    }
  };

  const markProcessingComplete = (message, messageType = 'success') => {
    setProcessingStage('');
    setCompletedStages(stageOrder.map(item => item.key));
    const resolvedMessage = message || 'Document processing completed successfully!';
    setWorkflowMessage(resolvedMessage);
    setWorkflowMessageType(messageType);
    highestStageIndexRef.current = stageOrder.length - 1;

    if (session?.session_id) {
      persistSessionState(session.session_id, {
        processingStage: '',
        completedStages: stageOrder.map(item => item.key),
        workflowMessage: resolvedMessage,
        workflowMessageType: messageType,
        generating: false
      });
      updateGlobalProcessingFlag(session.session_id, false);
    }
  };

  const handleGenerateRedline = async () => {
    const sessionIdAtStart = session?.session_id;
    const userIdAtStart = session?.user_id || authService.getUserId();
    const termsProfileForRun = normalizedTermsProfile;
    if (!sessionIdAtStart || !userIdAtStart) {
      setWorkflowMessage('Unable to start processing because the session or user could not be identified. Please refresh and try again.');
      setWorkflowMessageType('error');
      return;
    }
    const isCurrentSession = () => currentSessionIdRef.current === sessionIdAtStart;
    
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
    
    // Check if General Terms & Conditions is selected (not supported yet)
    if (normalizedTermsProfile === 'general') {
      setWorkflowMessage('Support for General Terms & Conditions is coming soon. Please select IT Terms & Conditions to generate redlined documents.');
      setWorkflowMessageType('error');
      setTermsProfileError('Support for General Terms & Conditions is coming soon.');
      return;
    }
    
    setTermsProfileError('');
    setGenerating(true);
    resetProcessingStages({ keepGeneratingState: true, skipWorkflowReset: true });
    setProcessingPhase('kb_sync', stageMessages.kb_sync);
    persistSessionState(sessionIdAtStart, {
      uploadedFiles,
      generating: true,
      processingStage: 'kb_sync',
      completedStages: [],
      workflowMessage: stageMessages.kb_sync,
      workflowMessageType: 'progress',
      termsProfile: termsProfileForRun
    });
    updateGlobalProcessingFlag(sessionIdAtStart, true);
    let hasProcessingResults = false;
    try {
      const redlineResults = [];
      
      // Process each vendor file
      for (const vendorFile of vendorFiles) {
        if (isCurrentSession()) {
          setProcessingPhase('document_review', `Reviewing ${vendorFile.filename} with AI. Please stand by.`);
        }
        
        try {
          const reviewResponse = await agentAPI.reviewDocument(
            vendorFile.s3_key, 
            'agent_processing',
            sessionIdAtStart,
            userIdAtStart,
            { termsProfile: termsProfileForRun }
          );
          
          // Check if processing is asynchronous
          let processingEntry = null;

          if (reviewResponse.processing && reviewResponse.job_id) {
            if (isCurrentSession()) {
              setWorkflowMessage(`Processing ${vendorFile.filename} in background...`);
            }
            
            const jobId = reviewResponse.job_id;
            
            // Subscribe to WebSocket notifications for this job
            try {
              webSocketService.subscribeToJob(jobId, sessionIdAtStart);

              
              jobSessionMapRef.current[jobId] = sessionIdAtStart;
              saveJobSessionMapToStorage(jobSessionMapRef.current);
              activeJobsRef.current[jobId] = {
                sessionId: sessionIdAtStart,
                vendorFile,
                startedAt: Date.now()
              };
              window.currentProcessingJob = {
                jobId: jobId,
                sessionId: sessionIdAtStart
              };

              // Backend automatically cancels old jobs when a new one starts
              // Frontend just adds the new job entry
              processingEntry = {
                originalFile: vendorFile,
                jobId: jobId,
                status: 'processing',
                progress: 0,  // Initial progress, will be updated by polling
                message: 'Starting document analysis...',
                processing: true,
                success: false,
                termsProfile: termsProfileForRun
              };
              
              console.log(`Created processing entry for jobId=${jobId} with initial progress=0`);
              redlineResults.push({ ...processingEntry });

              // Trigger session sidebar refresh to show new job
              if (window.triggerSessionSidebarRefresh) {
                window.triggerSessionSidebarRefresh();
              }

              if (isCurrentSession()) {
                setRedlinedDocuments(prev => {
                  // Backend handles cleanup - just add new job
                  // Remove any existing processing jobs (backend should have cancelled them, but handle race condition)
                  const withoutOldProcessing = prev.filter(doc => 
                    !(doc.processing || doc.status === 'processing') || doc.jobId === jobId
                  );
                  const nextDocs = [...withoutOldProcessing, processingEntry];
                  
                  persistSessionState(sessionIdAtStart, {
                    redlinedDocuments: nextDocs,
                    generating: true,
                    termsProfile: termsProfileForRun
                  });
                  updateGlobalProcessingFlag(sessionIdAtStart, true);
                  return nextDocs;
                });
              } else {
                // For background sessions
                const existingDocs = cloneRedlinedDocuments(
                  sessionDataRef.current?.[sessionIdAtStart]?.redlinedDocuments || []
                );
                const withoutOldProcessing = existingDocs.filter(doc => 
                  !(doc.processing || doc.status === 'processing') || doc.jobId === jobId
                );
                const nextDocs = [...withoutOldProcessing, processingEntry];
                
                persistSessionState(sessionIdAtStart, {
                  redlinedDocuments: nextDocs,
                  generating: true,
                  termsProfile: termsProfileForRun
                });
                updateGlobalProcessingFlag(sessionIdAtStart, true);
              }
              
              // Track new job mapping
              jobSessionMapRef.current[jobId] = sessionIdAtStart;
              saveJobSessionMapToStorage(jobSessionMapRef.current);
              
            } catch (error) {

            }
            
            // Poll for completion with real-time progress updates
            let finalResult = null;
            try {
              finalResult = await pollJobStatus(
                reviewResponse.job_id,
                sessionIdAtStart,
                vendorFile.filename,
                isCurrentSession
              );
            } catch (pollError) {
              console.warn('pollJobStatus failed', {
                jobId: reviewResponse.job_id,
                filename: vendorFile.filename,
                error: pollError?.message || pollError
              });
            }

            if (finalResult?.processing === false) {
              const transformedEntry = {
                ...processingEntry,
                processing: false,
                status: finalResult.success ? 'completed' : 'failed',
                progress: 100,
                success: finalResult.success,
                redlinedDocument: finalResult.success
                  ? finalResult.redlined_document?.redlined_document
                  : undefined,
                analysis: finalResult.success ? finalResult.analysis : undefined,
                error: finalResult.success ? undefined : finalResult.error,
                message: finalResult.success
                  ? 'Document processing completed'
                  : (finalResult.error || 'Processing failed'),
                termsProfile: termsProfileForRun
              };

              // Replace the last pushed processing entry with the final result
              redlineResults[redlineResults.length - 1] = transformedEntry;

              if (isCurrentSession()) {
                setRedlinedDocuments(prev => {
                  const nextDocs = prev.map(doc => {
                    if (processingEntry && doc.jobId === processingEntry.jobId) {
                      return transformedEntry;
                    }
                    return doc;
                  });
                  persistSessionState(sessionIdAtStart, {
                    redlinedDocuments: nextDocs,
                    generating: finalResult.success ? false : nextDocs.some(doc => doc.processing)
                  });
                  updateGlobalProcessingFlag(
                    sessionIdAtStart,
                    nextDocs.some(doc => doc.processing)
                  );
                  return nextDocs;
                });
              } else {
                const existingDocs = cloneRedlinedDocuments(
                  sessionDataRef.current?.[sessionIdAtStart]?.redlinedDocuments || []
                );
                const nextDocs = existingDocs.map(doc => {
                  if (processingEntry && doc.jobId === processingEntry.jobId) {
                    return transformedEntry;
                  }
                  return doc;
                });
                persistSessionState(sessionIdAtStart, {
                  redlinedDocuments: nextDocs,
                  generating: nextDocs.some(doc => doc.processing),
                  termsProfile: termsProfileForRun
                });
                updateGlobalProcessingFlag(
                  sessionIdAtStart,
                  nextDocs.some(doc => doc.processing)
                );
              }
            }
          } else if (reviewResponse.processing === false && reviewResponse.redlined_document && reviewResponse.redlined_document.success) {
          if (isCurrentSession()) {
            setProcessingPhase('generating', `Generating redlined document for ${vendorFile.filename}. Please stand by.`);
          }
            // Check if this is a "no conflicts" case (success but no redlined document)
            const hasNoConflicts = reviewResponse.redlined_document.no_conflicts === true || 
                                   (!reviewResponse.redlined_document.redlined_document && reviewResponse.redlined_document.success);
            redlineResults.push({
              originalFile: vendorFile,
              redlinedDocument: reviewResponse.redlined_document.redlined_document,
              analysis: reviewResponse.analysis,
              success: true,
              status: 'completed',
              processing: false,
              message: hasNoConflicts ? 'No conflicts detected' : 'Document processing completed',
              termsProfile: termsProfileForRun
            });
          } else if (reviewResponse.processing) {
            redlineResults.push({
              originalFile: vendorFile,
              processing: true,
              jobId: `job_${vendorFile.s3_key.replace(/[^a-zA-Z0-9]/g, '_')}_${Date.now()}`,
              success: false,
              message: reviewResponse.message || 'Processing in background...',
              termsProfile: termsProfileForRun
            });
          } else {
            // Check if this is actually a "no conflicts" case that was returned as an error
            const errorMessage = reviewResponse.redlined_document?.error || reviewResponse.error || '';
            const isNoConflictsCase = errorMessage.toLowerCase().includes('no conflicts found') ||
                                      reviewResponse.redlined_document?.no_conflicts === true;
            
            if (isNoConflictsCase) {
              // Treat as success with no conflicts
              redlineResults.push({
                originalFile: vendorFile,
                analysis: reviewResponse.analysis,
                success: true,
                status: 'completed',
                processing: false,
                message: 'No conflicts detected',
                termsProfile: termsProfileForRun
              });
            } else {
              // Actual error case
              redlineResults.push({
                originalFile: vendorFile,
                error: errorMessage || 'Unknown error',
                success: false,
                termsProfile: termsProfileForRun
              });
            }
          }
        } catch (error) {
          if (error.message.includes('timeout') || error.message.includes('504') || error.message.includes('502') || 
              error.message.includes('CORS error') || error.message.includes('continue in background')) {
            
            // Don't log timeout/CORS errors as errors - they're expected for long processing
            

            
            // DON'T add to redlineResults - just use the unified progress UI
            // Store the job info for WebSocket tracking without showing old UI
            // Note: We can't get the real job ID from the backend due to timeout,
            // but the session-level subscription will catch the completion notification
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
      
      const successfulResults = redlineResults.filter(r => r.success && r.redlinedDocument);
      const noConflictsResults = redlineResults.filter(r => r.success && !r.redlinedDocument);
      const failedResults = redlineResults.filter(r => !r.success && !r.processing);
      const processingResults = redlineResults.filter(r => r.processing);
      hasProcessingResults = processingResults.length > 0;
      
      if (isCurrentSession()) {
        setRedlinedDocuments(redlineResults);
      }

      persistSessionState(sessionIdAtStart, {
        redlinedDocuments: redlineResults,
        generating: hasProcessingResults,
        termsProfile: termsProfileForRun
      });
      updateGlobalProcessingFlag(sessionIdAtStart, hasProcessingResults);
      
      if (successfulResults.length > 0 || noConflictsResults.length > 0) {
        let message = '';
        if (successfulResults.length > 0 && noConflictsResults.length === 0) {
          // All successful with redlines
          message = `Successfully generated ${successfulResults.length} redlined document(s)!`;
          if (processingResults.length > 0) {
            message += ` ${processingResults.length} document(s) are still processing in background.`;
          }
          if (failedResults.length > 0) {
            message += ` ${failedResults.length} failed.`;
          }
          message += ' Scroll down to download completed documents.';
        } else if (noConflictsResults.length > 0 && successfulResults.length === 0) {
          // All no conflicts
          if (noConflictsResults.length === 1) {
            message = 'Document analysis completed. No conflicts were detected, so no redlined document was generated.';
          } else {
            message = `Document analysis completed. No conflicts were detected in any documents, so no redlined documents were generated.`;
          }
        } else {
          // Mixed: some with redlines, some no conflicts
          message = `Successfully generated ${successfulResults.length} redlined document(s)!`;
          if (noConflictsResults.length > 0) {
            message += ` ${noConflictsResults.length} document(s) had no conflicts.`;
          }
          if (processingResults.length > 0) {
            message += ` ${processingResults.length} document(s) are still processing in background.`;
          }
          if (failedResults.length > 0) {
            message += ` ${failedResults.length} failed.`;
          }
          if (successfulResults.length > 0) {
            message += ' Scroll down to download completed documents.';
          }
        }
        if (isCurrentSession()) {
          if (processingResults.length === 0) {
            const completionType = failedResults.length > 0 ? 'error' : 'success';
            markProcessingComplete(message, completionType);
          } else {
            setProcessingPhase('generating', message);
          }
        }
      } else if (processingResults.length > 0) {
        if (isCurrentSession()) {
          setProcessingPhase(
            'generating',
            `${processingResults.length} document(s) are processing in background due to complexity. Please wait for completion.`
          );
        }
      } else if (failedResults.length > 0) {
        if (isCurrentSession()) {
          resetProcessingStages();
          setWorkflowMessage('All redline generation attempts failed. Please check your documents and try again.');
          setWorkflowMessageType('error');
        }
      } else {
        // No results yet - processing is starting, show processing message
        if (isCurrentSession()) {
          setProcessingPhase('kb_sync', 'Starting document processing... Please wait for completion.');
        }
      }
      
    } catch (error) {
      console.error('Error generating redlined documents:', error);
      if (window.progressInterval) {
        clearInterval(window.progressInterval);
        window.progressInterval = null;
      }
      const errorMessage = typeof error?.message === 'string' ? error.message.toLowerCase() : '';
      if (errorMessage.includes('knowledge base') || errorMessage.includes('general terms')) {
        if (errorMessage.includes('general')) {
          setTermsProfileError('Support for General Terms & Conditions is coming soon.');
          const generalTermsMessage = 'Support for General Terms & Conditions is coming soon. Please select IT Terms & Conditions to generate redlined documents.';
          if (isCurrentSession()) {
            setWorkflowMessage(generalTermsMessage);
            setWorkflowMessageType('error');
            resetProcessingStages();
          } else {
            persistSessionState(sessionIdAtStart, {
              workflowMessage: generalTermsMessage,
              workflowMessageType: 'error',
              processingStage: '',
              completedStages: [],
              generating: false,
              termsProfile: termsProfileForRun
            });
          }
        } else {
          setTermsProfileError(error.message);
          if (isCurrentSession()) {
            setWorkflowMessage(`Failed to generate redlined documents: ${error.message}`);
            setWorkflowMessageType('error');
            resetProcessingStages();
          } else {
            persistSessionState(sessionIdAtStart, {
              workflowMessage: `Failed to generate redlined documents: ${error.message}`,
              workflowMessageType: 'error',
              processingStage: '',
              completedStages: [],
              generating: false,
              termsProfile: termsProfileForRun
            });
          }
        }
      } else if (isCurrentSession()) {
        setWorkflowMessage(`Failed to generate redlined documents: ${error.message}`);
        setWorkflowMessageType('error');
        resetProcessingStages();
      } else {
        persistSessionState(sessionIdAtStart, {
          workflowMessage: `Failed to generate redlined documents: ${error.message}`,
          workflowMessageType: 'error',
          processingStage: '',
          completedStages: [],
          generating: false,
          termsProfile: termsProfileForRun
        });
        updateGlobalProcessingFlag(sessionIdAtStart, false);
      }
      hasProcessingResults = false;
    } finally {
      const sessionEntry = sessionDataRef.current[sessionIdAtStart];
      const stillProcessing = hasProcessingResults || (sessionEntry?.redlinedDocuments?.some(doc => doc.processing) ?? false);
      if (isCurrentSession()) {
        if (!stillProcessing) {
          setGenerating(false);
        }
      }

      if (stillProcessing) {
        persistSessionState(sessionIdAtStart, {
          generating: true,
          processingStage: sessionEntry?.processingStage || 'document_review',
          completedStages: Array.isArray(sessionEntry?.completedStages)
            ? sessionEntry.completedStages
            : [],
          termsProfile: termsProfileForRun
        });
      } else {
        persistSessionState(sessionIdAtStart, {
          generating: false,
          processingStage: '',
          completedStages: stageOrder.map(item => item.key),
          termsProfile: termsProfileForRun
        });
      }
      updateGlobalProcessingFlag(sessionIdAtStart, stillProcessing);
    }
  };

  const handleDownloadRedlined = async (redlineResult) => {
    // Validate redlinedDocument exists and is a valid string
    if (!redlineResult.redlinedDocument || 
        typeof redlineResult.redlinedDocument !== 'string' || 
        redlineResult.redlinedDocument.trim() === '') {
      setWorkflowMessage('No redlined document available for download.');
      setWorkflowMessageType('error');
      return;
    }
    
    try {
      // Extract original file extension to preserve it
      const originalFilename = redlineResult.originalFile?.filename || 'document';
      const filenameWithoutExt = originalFilename.replace(/\.[^/.]+$/, '');
      const fileExtension = originalFilename.split('.').pop() || 'docx';
      
      const downloadResult = await agentAPI.downloadFile(
        redlineResult.redlinedDocument.trim(), 
        'agent_processing',
        `${filenameWithoutExt}_REDLINED.${fileExtension}`
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

  // If this is an existing session (not new) AND user hasn't uploaded files, show only the results table
  // Always show the workspace if user has uploaded files, even if there are existing results
  if (!isNewSession && sessionResults.length > 0 && uploadedFiles.length === 0) {
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
      {/* Processing Overlay */}
      {generating && (() => {
        // Find the current processing job - prefer the one matching currentProcessingJob, then most recent
        const currentJobId = window.currentProcessingJob?.jobId;
        let processingDoc = null;
        
        if (currentJobId) {
          // Try to find by current jobId first
          processingDoc = redlinedDocuments.find(d => d.jobId === currentJobId);
        }
        
        // If not found, find any processing document (fallback)
        if (!processingDoc) {
          processingDoc = redlinedDocuments.find(d => d.processing || d.status === 'processing');
        }
        
        const currentProgress = typeof processingDoc?.progress === 'number' ? processingDoc.progress : 0;
        const currentMessage = processingDoc?.message || workflowMessage || 'Starting document review...';
        
        // Debug logging
        if (processingDoc) {
          console.log(`Popup: Found processing doc with jobId=${processingDoc.jobId}, currentJobId=${currentJobId}, progress=${processingDoc.progress}, progressType=${typeof processingDoc.progress}, currentProgress=${currentProgress}`);
        } else {
          console.log(`Popup: No processing doc found. currentJobId=${currentJobId}, All docs:`, redlinedDocuments.map(d => ({ jobId: d.jobId, progress: d.progress, status: d.status, processing: d.processing })));
        }
        
        return (
          <div className="processing-overlay">
            <div className="processing-modal">
              <div className="processing-spinner-container">
                <div className="processing-spinner"></div>
              </div>
              <h2 className="processing-title">Processing Document</h2>
              <div className="processing-stages">
                {stageOrder.map((stage, index) => {
                  const isCompleted = completedStages.includes(stage.key);
                  const isActive = processingStage === stage.key;
                  return (
                    <div key={stage.key} className="processing-stage">
                      <div className={`processing-stage-dot ${isCompleted ? 'completed' : isActive ? 'active' : 'pending'}`}>
                        {isCompleted ? '✓' : index + 1}
                      </div>
                      <span className="processing-stage-label">{stage.label}</span>
                    </div>
                  );
                })}
              </div>
              {/* Linear Progress Bar */}
              <div style={{ marginTop: '20px', marginBottom: '12px' }}>
                <div style={{
                  width: '100%',
                  height: '8px',
                  backgroundColor: '#e2e8f0',
                  borderRadius: '4px',
                  overflow: 'hidden',
                  position: 'relative'
                }}>
                  <div 
                    style={{ 
                      width: `${Math.max(2, currentProgress)}%`,
                      height: '100%',
                      backgroundColor: '#2563eb',
                      borderRadius: '4px',
                      transition: 'width 0.3s ease',
                      position: 'absolute',
                      top: 0,
                      left: 0
                    }}
                  ></div>
                </div>
                <div style={{ 
                  display: 'flex', 
                  justifyContent: 'space-between', 
                  alignItems: 'center',
                  marginTop: '8px'
                }}>
                  <p style={{ fontSize: '13px', color: '#64748b', margin: 0 }}>
                    {currentMessage}
                  </p>
                  <p style={{ fontSize: '13px', color: '#2563eb', fontWeight: '600', margin: 0 }}>
                    {currentProgress}%
                  </p>
                </div>
              </div>
              <p className="processing-tip">
                You can navigate away. Results will be saved automatically.
              </p>
              <button
                onClick={() => setGenerating(false)}
                style={{
                  marginTop: '16px',
                  padding: '8px 20px',
                  backgroundColor: 'transparent',
                  color: '#64748b',
                  border: '1px solid #334155',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  fontSize: '13px',
                  transition: 'all 0.2s'
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.backgroundColor = '#1e293b';
                  e.currentTarget.style.color = '#94a3b8';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.backgroundColor = 'transparent';
                  e.currentTarget.style.color = '#64748b';
                }}
              >
                Dismiss
              </button>
            </div>
          </div>
        );
      })()}

      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <h1>Contract Review</h1>
            <p>AI-powered analysis of vendor submissions against reference documents</p>
          </div>
          {session && (
            <div style={{ 
              fontSize: '13px', 
              color: '#64748b',
              textAlign: 'right'
            }}>
              <div style={{ fontWeight: '500', color: '#1e293b' }}>{session.title}</div>
              <div style={{ fontSize: '12px' }}>Active Session</div>
            </div>
          )}
        </div>
      </div>
      
      <div className="upload-sections" style={{ opacity: generating ? 0.5 : 1, pointerEvents: generating ? 'none' : 'auto' }}>
        <VendorSubmission 
          onFilesUploaded={handleFilesUploaded}
          previouslyUploadedFiles={uploadedFiles.filter(f => f.type === 'vendor_submission')}
          sessionContext={session}
          disabled={generating}
        />
        
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
          sessionContext={session}
          previouslyUploadedFiles={uploadedFiles.filter(f => f.type === 'reference_document')}
          disabled={generating}
        />
      </div>
      


      {/* Document Review Workflow */}
      <div className="card">
          <h2>Review Settings</h2>
          <p style={{ marginBottom: '16px' }}>Select contract standards and generate analysis.</p>
        <div
          style={{
            margin: '16px 0',
            padding: '18px 20px',
            background: '#eef2f7',
            borderRadius: '12px',
            border: '1px solid #d1dae7',
            boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.6)'
          }}
        >
          <div
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              justifyContent: 'space-between',
              gap: '12px',
              marginBottom: '16px'
            }}
          >
            <div style={{ maxWidth: '520px' }}>
              <div style={{ fontWeight: 600, fontSize: '16px', color: '#0b1f33' }}>
                Select Terms &amp; Conditions Profile
              </div>
              <div style={{ fontSize: '13px', color: '#4b5a6b', marginTop: '4px', lineHeight: 1.5 }}>
                Choose the contract standard the AI should follow before generating redlines.
              </div>
            </div>
            <div
              style={{
                fontSize: '12px',
                color: '#37518f',
                background: '#dce7ff',
                padding: '6px 10px',
                borderRadius: '20px',
                fontWeight: 600,
                alignSelf: 'flex-start'
              }}
            >
              Current selection: {activeTermsProfileOption.label}
            </div>
          </div>
          <div
            style={{
              display: 'grid',
              gap: '12px',
              gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))'
            }}
          >
            {termsProfileOptions.map(option => {
              const isActive = normalizedTermsProfile === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => handleTermsProfileSelection(option.value)}
                  disabled={generating}
                  aria-pressed={isActive}
                  style={{
                    position: 'relative',
                    padding: '16px 18px',
                    borderRadius: '10px',
                    border: isActive ? '2px solid #2563eb' : '1px solid #ccd6e3',
                    background: isActive ? '#2563eb' : '#ffffff',
                    color: isActive ? '#f8fafc' : '#1a2c44',
                    textAlign: 'left',
                    cursor: generating ? 'not-allowed' : 'pointer',
                    transition: 'all 0.2s ease',
                    boxShadow: isActive
                      ? '0 10px 25px -12px rgba(37, 99, 235, 0.65)'
                      : '0 1px 3px rgba(15, 23, 42, 0.08)',
                    opacity: generating ? 0.72 : 1
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'flex-start',
                      gap: '12px'
                    }}
                  >
                    <div>
                      <div style={{ fontSize: '15px', fontWeight: 600, lineHeight: 1.3 }}>
                        {option.label}
                      </div>
                      <div
                        style={{
                          fontSize: '13px',
                          marginTop: '6px',
                          color: isActive ? 'rgba(241,245,249,0.9)' : '#4b5a6b',
                          lineHeight: 1.5
                        }}
                      >
                        {option.description}
                      </div>
                    </div>
                    {isActive && (
                      <div
                        style={{
                          flexShrink: 0,
                          background: '#1d4ed8',
                          color: '#f1f5f9',
                          fontSize: '11px',
                          fontWeight: 600,
                          padding: '4px 8px',
                          borderRadius: '999px',
                          textTransform: 'uppercase',
                          letterSpacing: '0.08em',
                          boxShadow: '0 2px 0 rgba(15,23,42,0.18)'
                        }}
                      >
                        Selected
                      </div>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
          {termsProfileError && (
            <div className="alert alert-error" style={{ marginTop: '14px' }}>
              {termsProfileError}
            </div>
          )}
        </div>
        

        
        {/* Generate Button */}
        <div style={{ marginTop: '20px' }}>
          <button
            onClick={handleGenerateRedline}
            disabled={!canGenerateRedline || generating}
            className="btn"
            style={{
              width: '100%',
              padding: '12px 24px',
              fontSize: '14px',
              opacity: (!canGenerateRedline || generating) ? 0.6 : 1
            }}
          >
            {generating ? 'Processing...' : 'Generate Redlined Document'}
          </button>
          {!canGenerateRedline && !generating && (
            <p style={{ 
              marginTop: '8px', 
              fontSize: '12px', 
              color: '#64748b',
              textAlign: 'center'
            }}>
              Upload both vendor submission and reference documents to enable analysis
            </p>
          )}
        </div>
        
        {/* Unified Status UI */}
        {(generating || processingStage || (redlinedDocuments.filter(doc => doc.success && !doc.processing).length > 0)) && (() => {
          const hasCompletedResults = redlinedDocuments.filter(doc => doc.success && !doc.processing).length > 0;
          const isCompleted = hasCompletedResults && !generating && !processingStage;
          const effectiveCompletedStages = isCompleted ? stageOrder.map(stage => stage.key) : completedStages;
          const completedStageSet = new Set(effectiveCompletedStages);
          const activeStageKey = isCompleted ? '' : processingStage;
          const activeStage = stageOrder.find(stage => stage.key === activeStageKey);
          const fallbackMessage = activeStage ? stageMessages[activeStage.key] : '';
          const displayMessage = isCompleted
            ? (workflowMessage || 'Document processing completed successfully!')
            : (workflowMessage || fallbackMessage || 'Please stand by while we process your documents.');
          const showSpinner = !isCompleted;
          
          // Get actual progress from processing document
          // Find the current processing job - prefer the one matching currentProcessingJob, then most recent
          const currentJobId = window.currentProcessingJob?.jobId;
          let processingDoc = null;
          
          if (currentJobId) {
            // Try to find by current jobId first
            processingDoc = redlinedDocuments.find(d => d.jobId === currentJobId);
          }
          
          // If not found, find any processing document (fallback)
          if (!processingDoc) {
            processingDoc = redlinedDocuments.find(d => d.processing || d.status === 'processing');
          }
          
          const currentProgress = typeof processingDoc?.progress === 'number' ? processingDoc.progress : 0;
          
          // Debug logging
          if (processingDoc) {
            console.log(`Body: Found processing doc with jobId=${processingDoc.jobId}, currentJobId=${currentJobId}, progress=${processingDoc.progress}, progressType=${typeof processingDoc.progress}, currentProgress=${currentProgress}`);
          } else {
            console.log(`Body: No processing doc found. currentJobId=${currentJobId}, All docs:`, redlinedDocuments.map(d => ({ jobId: d.jobId, progress: d.progress, status: d.status, processing: d.processing })));
          }

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
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '16px', gap: '12px' }}>
                {stageOrder.map(stage => {
                  const status = completedStageSet.has(stage.key)
                    ? 'completed'
                    : (stage.key === activeStageKey ? 'current' : 'pending');
                  const dotColor = status === 'completed' ? '#28a745' : (status === 'current' ? '#007bff' : 'transparent');
                  const borderColor = status === 'pending' ? '#ced4da' : 'transparent';
                  const textColor = status === 'pending' ? '#6c757d' : '#007bff';
                  return (
                    <div key={stage.key} style={{ textAlign: 'center', flex: 1, color: textColor }}>
                      <div style={{
                        width: '18px',
                        height: '18px',
                        borderRadius: '50%',
                        margin: '0 auto 6px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        backgroundColor: dotColor,
                        border: `2px solid ${borderColor}`,
                        color: '#fff',
                        fontSize: '11px'
                      }}>
                        {status === 'completed' ? '✓' : ''}
                      </div>
                      <small>{stage.label}</small>
                    </div>
                  );
                })}
              </div>

              {/* Linear Progress Bar */}
              {!isCompleted && (
                <div style={{ marginBottom: '16px' }}>
                  <div style={{
                    width: '100%',
                    height: '8px',
                    backgroundColor: '#e2e8f0',
                    borderRadius: '4px',
                    overflow: 'hidden',
                    position: 'relative'
                  }}>
                    <div 
                      style={{ 
                        width: `${Math.max(2, currentProgress)}%`,
                        height: '100%',
                        backgroundColor: '#2563eb',
                        borderRadius: '4px',
                        transition: 'width 0.3s ease',
                        position: 'absolute',
                        top: 0,
                        left: 0
                      }}
                    ></div>
                  </div>
                  <div style={{ 
                    display: 'flex', 
                    justifyContent: 'space-between', 
                    alignItems: 'center',
                    marginTop: '8px'
                  }}>
                    <span style={{ fontSize: '13px', color: '#64748b' }}>
                      {displayMessage}
                    </span>
                    <span style={{ fontSize: '13px', color: '#2563eb', fontWeight: '600' }}>
                      {currentProgress}%
                    </span>
                  </div>
                </div>
              )}

              {/* Current Status */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                {showSpinner ? (
                  <div style={{
                    width: '16px',
                    height: '16px',
                    border: '2px solid #007bff',
                    borderTop: '2px solid transparent',
                    borderRadius: '50%',
                    animation: 'spin 1s linear infinite'
                  }}></div>
                ) : (
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
                  {isCompleted ? displayMessage : (displayMessage.split('(')[0].trim() || displayMessage)}
                  {!isCompleted && activeStage ? ` (${activeStage.label})` : ''}
                </span>
              </div>
            </div>
          );
        })()}
        
        {/* Status Messages */}
        {workflowMessage && workflowMessageType !== 'progress' && !generating && !processingStage && redlinedDocuments.filter(doc => doc.success && !doc.processing).length === 0 && (
          workflowMessageType === 'error' ? (
            <div className="alert-error-improved">
              <div className="error-header">
                <h4 className="error-title">Processing Failed</h4>
              </div>
              <p className="error-message">
                {workflowMessage.includes('failed') 
                  ? 'Unable to process the document. This may occur with complex layouts or scanned files. Try a different format or document.'
                  : workflowMessage}
              </p>
              <div className="error-actions">
                <button 
                  className="btn-retry" 
                  onClick={() => {
                    setWorkflowMessage('');
                    setWorkflowMessageType('');
                  }}
                >
                  Try Again
                </button>
                <button 
                  className="btn-dismiss"
                  onClick={() => {
                    setWorkflowMessage('');
                    setWorkflowMessageType('');
                  }}
                >
                  Dismiss
                </button>
              </div>
            </div>
          ) : (
            <div className="alert-success-improved">
              <div className="success-header">
                <div className="success-content">
                  <h4>{workflowMessage}</h4>
                  <p>Document processed successfully.</p>
                </div>
              </div>
            </div>
          )
        )}
        
        {/* Redlined Documents Results - Show completed and failed documents */}
        {(() => {
          const completedDocs = redlinedDocuments.filter(doc => !doc.processing && (doc.success || doc.status === 'failed'));
          return completedDocs.length > 0 && (
            <div style={{ marginTop: '20px' }}>
              <h3>Document Processing Results</h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                {completedDocs.map((result, index) => (
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
                      {result.redlinedDocument ? (
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
                      ) : (
                        <div style={{ 
                          padding: '8px', 
                          background: '#f8d7da', 
                          borderRadius: '4px',
                          color: '#721c24',
                          fontSize: '14px'
                        }}>
                          ✓ No conflicts found in this document
                        </div>
                      )}
                      
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
                    <div style={{ 
                      background: '#fef2f2',
                      borderRadius: '6px',
                      padding: '12px',
                      border: '1px solid #fecaca'
                    }}>
                      <div style={{ 
                        fontWeight: '500', 
                        color: '#991b1b', 
                        marginBottom: '4px',
                        fontSize: '13px'
                      }}>
                        Processing Failed
                      </div>
                      <div style={{ 
                        fontSize: '12px', 
                        color: '#7f1d1d',
                        lineHeight: '1.5'
                      }}>
                        {result.error || result.message || 'An error occurred while processing this document.'}
                      </div>
                    </div>
                  )}
                </div>
                ))}
              </div>
            </div>
          );
        })()}
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
      
      // Handle different response structures (wrapped in body or direct)
      let responseData = response;
      if (response && response.body) {
        try {
          responseData = typeof response.body === 'string' ? JSON.parse(response.body) : response.body;
        } catch (e) {
          console.error('Error parsing response body in AutoSessionRedirect:', e);
          responseData = response;
        }
      }
      
      if (responseData.success && responseData.session?.session_id) {
        navigate(`/${responseData.session.session_id}`, { 
          replace: true, 
          state: { session: responseData.session } 
        });
      } else {
        throw new Error(responseData.message || responseData.error || 'Failed to create session');
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
  
  // Sidebar state
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  
  // Authentication state
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [currentUser, setCurrentUser] = useState(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [isAdmin, setIsAdmin] = useState(authService.isUserAdmin());

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
          setIsAdmin(authService.isUserAdmin());

        }
        else {
          setIsAuthenticated(false);
          setCurrentUser(null);
          setIsAdmin(false);
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
    setIsAdmin(false);
    
    // Logout from Cognito (this will redirect)
    authService.logout();
  };

  // Simplified session handlers - no longer needed with direct navigation
  // All workflow logic moved to SessionWorkspace component

  // Handle admin section navigation
  const handleAdminSectionChange = async (section) => {
    if (!isAdmin) {
      navigate('/');
      return;
    }
    if (section === 'admin') {
      navigate('/admin/knowledgebase');
    } else if (section === 'metrics') {
      navigate('/admin/metrics');
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
          isAdmin ? (
            <div className="main-content">
              <AdminDashboard activeTab={activeTab} onTabChange={setActiveTab} />
            </div>
          ) : (
            <Navigate to="/" replace />
          )
        } />
        <Route path="/admin/metrics" element={
          isAdmin ? (
            <div className="main-content">
              <MetricsDashboard />
            </div>
          ) : (
            <Navigate to="/" replace />
          )
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
          <p style={{ color: '#666', marginBottom: '24px', fontSize: '18px', fontWeight: '500' }}>
            AI-powered intelligent review of vendor submissions
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
    <div 
      className="app-container" 
      style={{ display: 'flex', height: '100vh', position: 'relative' }}
      data-sidebar-collapsed={sidebarCollapsed}
    >
      {/* User Header */}
      <UserHeader 
        user={currentUser}
        onLogout={handleLogout}
      />
      
      {/* Session Sidebar with integrated Admin */}
      <SessionSidebar
        currentUserId={authService.getUserId()}
        onAdminSectionChange={handleAdminSectionChange}
        isAdmin={isAdmin}
        isVisible={true}
        onCollapsedChange={setSidebarCollapsed}
      />
      
      {/* Main Content Area */}
      {renderMainContent()}
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