/**
 * Main App Component for One-L Application
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
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
    { key: 'document_review', label: 'Document Review' },
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
      timeoutError: doc?.timeoutError
    }));
  };

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
        workflowMessageType: ''
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
      'lastWebSocketUpdate'
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
        workflowMessageType
      });
    }
  }, [session?.session_id, uploadedFiles, redlinedDocuments, generating, processingStage, completedStages, workflowMessage, workflowMessageType, saveSessionDataToStorage]);

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
        workflowMessageType: ''
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
          workflowMessageType: ''
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
          // Deep copy each document to ensure proper restoration
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
    try {
      setLoadingResults(true);
      const userId = authService.getUserId();
      
      // Check if this session has recent WebSocket updates that should be prioritized
      const sessionData = sessionDataRef.current[session.session_id];
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
          .filter(result => result.redlined_document_s3_key)
          .map(result => ({
            originalFile: {
              filename: result.document_name,
              s3_key: result.document_s3_key
            },
            redlinedDocument: result.redlined_document_s3_key,
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
          // Keep any in-progress documents from baseline
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

          const completionMessage = hasProcessingDocs
            ? (sessionData?.workflowMessage || stageMessages.generating || 'Processing documents. Please stand by.')
            : 'Document processing completed successfully!';

          const completionMessageType = hasProcessingDocs ? 'progress' : 'success';

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
    
    // Update UI with progress
    if (session_id === session?.session_id) {
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
  };

  const handleJobCompleted = async (message) => {
    const { job_id, session_id, data } = message;
    const isCurrentSession = session_id === session?.session_id;
    
    const reconcileDocuments = (docs = []) => {
      let matched = false;
      const updatedDocs = docs.map(doc => {
        if (!matched && (doc.jobId === job_id || (data.analysis_id && doc.analysis === data.analysis_id))) {
          matched = true;
          const redlinedSuccess = data.redlined_document && data.redlined_document.success;
          const redlinedDoc = data.redlined_document?.redlined_document;
          return {
            ...doc,
            status: 'completed',
            progress: 100,
            success: redlinedSuccess,
            redlinedDocument: redlinedDoc || doc.redlinedDocument,
            analysis: data.analysis_id || doc.analysis,
            processing: false,
            message: 'Document processing completed'
          };
        }
        return doc;
      });
      if (!matched && data.redlined_document && data.redlined_document.success) {
        updatedDocs.push({
          originalFile: window.currentProcessingJob || { filename: `Document for job ${job_id}` },
          redlinedDocument: data.redlined_document.redlined_document,
          analysis: data.analysis_id,
          success: true,
          processing: false,
          jobId: job_id,
          status: 'completed',
          progress: 100
        });
      }
      const stillProcessing = updatedDocs.some(doc => doc.processing === true || doc.status === 'processing');
      return { updatedDocs, stillProcessing };
    };
    
    const persistSessionCompletion = () => {
      if (!sessionDataRef.current[session_id]) {
        sessionDataRef.current[session_id] = {
          uploadedFiles: [],
          redlinedDocuments: [],
          generating: false,
          processingStage: '',
          completedStages: [],
          workflowMessage: '',
          workflowMessageType: ''
        };
      }
      const entry = sessionDataRef.current[session_id];
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
        entry.workflowMessage = 'Document processing completed successfully!';
        entry.workflowMessageType = 'success';
      }

      // Mark this session as having WebSocket updates that need to be preserved
      entry.hasWebSocketUpdates = true;
      entry.lastWebSocketUpdate = Date.now();
      saveSessionDataToStorage(sessionDataRef.current);
    updateGlobalProcessingFlag(session_id, stillProcessing);
      return { stillProcessing };
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
      
      window.currentProcessingJob = null;
      
      const { stillProcessing } = persistSessionCompletion();
      stillProcessingAfterUpdate = stillProcessingAfterUpdate || stillProcessing;
      if (!stillProcessingAfterUpdate) {
        markProcessingComplete();
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
      persistSessionCompletion();
      console.log(`Background session ${session_id} received completion for job ${job_id}. Data saved to localStorage for later retrieval.`);
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
    
    setGenerating(true);
    resetProcessingStages({ keepGeneratingState: true, skipWorkflowReset: true });
    setProcessingPhase('kb_sync', stageMessages.kb_sync);
    persistSessionState(sessionIdAtStart, {
      uploadedFiles,
      generating: true,
      processingStage: 'kb_sync',
      completedStages: [],
      workflowMessage: stageMessages.kb_sync,
      workflowMessageType: 'progress'
    });
    updateGlobalProcessingFlag(sessionIdAtStart, true);
    window.currentProcessingJob = {
      ...(window.currentProcessingJob || {}),
      sessionId: sessionIdAtStart,
      filename: vendorFiles[0]?.filename || '',
      startedAt: Date.now(),
      pendingDocuments: vendorFiles.map(file => file.filename)
    };
    
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
            userIdAtStart
          );
          
          // Check if processing is asynchronous
          if (reviewResponse.processing && reviewResponse.job_id) {
            if (isCurrentSession()) {
              setWorkflowMessage(`Processing ${vendorFile.filename} in background...`);
            }
            
            // Subscribe to WebSocket notifications for this job
            try {
              webSocketService.subscribeToJob(reviewResponse.job_id, sessionIdAtStart);

              
              // Add job to tracking with initial progress
              const processingEntry = {
                originalFile: vendorFile,
                jobId: reviewResponse.job_id,
                status: 'processing',
                progress: 0,
                message: 'Starting document analysis...',
                processing: true
              };

              if (isCurrentSession()) {
                setRedlinedDocuments(prev => {
                  const nextDocs = [...prev, processingEntry];
                  persistSessionState(sessionIdAtStart, {
                    redlinedDocuments: nextDocs,
                    generating: true
                  });
                  updateGlobalProcessingFlag(sessionIdAtStart, true);
                  return nextDocs;
                });
              } else {
                const existingDocs = cloneRedlinedDocuments(
                  sessionDataRef.current?.[sessionIdAtStart]?.redlinedDocuments || []
                );
                const nextDocs = [...existingDocs, processingEntry];
                persistSessionState(sessionIdAtStart, {
                  redlinedDocuments: nextDocs,
                  generating: true
                });
                updateGlobalProcessingFlag(sessionIdAtStart, true);
              }
              
            } catch (error) {

            }
            
            // Poll for completion (WebSocket is primary, polling is fallback)
            const finalResult = await pollJobStatus(
              reviewResponse.job_id, 
              userIdAtStart, 
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
          if (isCurrentSession()) {
            setProcessingPhase('generating', `Generating redlined document for ${vendorFile.filename}. Please stand by.`);
          }
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
              sessionId: sessionIdAtStart,
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
      hasProcessingResults = processingResults.length > 0;
      
      if (isCurrentSession()) {
        setRedlinedDocuments(redlineResults);
      }

      persistSessionState(sessionIdAtStart, {
        redlinedDocuments: redlineResults,
        generating: hasProcessingResults
      });
      updateGlobalProcessingFlag(sessionIdAtStart, hasProcessingResults);
      
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
          generating: false
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
            : []
        });
      } else {
        persistSessionState(sessionIdAtStart, {
          generating: false,
          processingStage: '',
          completedStages: stageOrder.map(item => item.key)
        });
      }
      updateGlobalProcessingFlag(sessionIdAtStart, stillProcessing);
    }
  };

  const handleDownloadRedlined = async (redlineResult) => {
    if (!redlineResult.redlinedDocument) {
      setWorkflowMessage('No redlined document available for download.');
      setWorkflowMessageType('error');
      return;
    }
    
    try {
      // Extract original file extension to preserve it
      const originalFilename = redlineResult.originalFile.filename;
      const filenameWithoutExt = originalFilename.replace(/\.[^/.]+$/, '');
      const fileExtension = originalFilename.split('.').pop();
      
      const downloadResult = await agentAPI.downloadFile(
        redlineResult.redlinedDocument, 
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
      <div className="card">
        <h1>One L</h1>
        <p>AI-powered intelligent review of vendor submissions</p>
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
        <VendorSubmission 
          onFilesUploaded={handleFilesUploaded}
          previouslyUploadedFiles={uploadedFiles.filter(f => f.type === 'vendor_submission')} // Show previously uploaded vendor file
          sessionContext={session} // Pass session context to clear selectedFiles on session change
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
          sessionContext={session} //  Pass session context for session-based storage
          previouslyUploadedFiles={uploadedFiles.filter(f => f.type === 'reference_document')} // Show previously uploaded reference documents
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
            ? 'Document processing completed successfully!'
            : (workflowMessage || fallbackMessage || 'Please stand by while we process your documents.');
          const showSpinner = !isCompleted;

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
                  {displayMessage}
                  {!isCompleted && activeStage ? ` (${activeStage.label})` : ''}
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
        {(() => {
          const completedDocs = redlinedDocuments.filter(doc => doc.success && !doc.processing);
          return completedDocs.length > 0 && (
            <div style={{ marginTop: '20px' }}>
              <h3>Generated Redlined Documents</h3>
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