import React, { useState, useEffect, useCallback, useRef } from 'react';
import { sessionAPI, agentAPI } from '../services/api';
import jobPollingService from '../services/jobPolling';
import { useNavigate, useParams } from 'react-router-dom';

// Pure helper functions - defined outside component to avoid recreation on every render
const extractDocumentName = (s3Key) => {
  if (!s3Key) return 'Unknown Document';
  const parts = s3Key.split('/');
  const filename = parts[parts.length - 1];
  // Remove UUID prefix if present (format: uuid_filename.pdf)
  const match = filename.match(/^[a-f0-9-]+_(.+)$/i);
  return match ? match[1] : filename;
};

const SessionSidebar = ({ 
  currentUserId, 
  isVisible = true,
  onAdminSectionChange,
  onRefreshRequest,
  isAdmin = false,
  onCollapsedChange
}) => {
  const navigate = useNavigate();
  const { sessionId } = useParams();
  const [sessions, setSessions] = useState([]);
  const [sessionStatuses, setSessionStatuses] = useState({}); // session_id -> { documents: [], activeJobs: [] }
  const [loading, setLoading] = useState(false);
  const [creatingSession, setCreatingSession] = useState(false);
  const [editingSession, setEditingSession] = useState(null);
  const [editTitle, setEditTitle] = useState('');
  const [adminExpanded, setAdminExpanded] = useState(false);
  const [isCollapsed, setIsCollapsed] = useState(false);
  
  // Notify parent when collapsed state changes
  const toggleCollapsed = () => {
    const newState = !isCollapsed;
    setIsCollapsed(newState);
    if (onCollapsedChange) {
      onCollapsedChange(newState);
    }
  };
  
  useEffect(() => {
    if (sessionId) {
      window.activeSessionId = sessionId;
    }
  }, [sessionId]);

  // Define main functions with useCallback
  const loadSessions = useCallback(async () => {
    try {
      setLoading(true);
      const response = await sessionAPI.getUserSessions(currentUserId, false);
      
      let responseData = response;
      if (response && response.body) {
        try {
          responseData = typeof response.body === 'string' ? JSON.parse(response.body) : response.body;
        } catch (e) {
          console.error('Error parsing response body:', e);
          responseData = response;
        }
      }
      
      if (response && response.statusCode && response.statusCode >= 400) {
        console.error('HTTP error loading sessions:', response.statusCode, responseData);
        return { error: true, statusCode: response.statusCode, isServerError: response.statusCode >= 500 };
      }
      
      if (responseData && responseData.success) {
        // Backend now automatically filters empty sessions, so we can trust the response
        setSessions(responseData.sessions || []);
        return { success: true };
      } else {
        console.error('Failed to load sessions:', responseData?.error || 'Unknown error');
        return { error: true };
      }
    } catch (error) {
      if (error.message && error.message.includes('500')) {
        console.warn('Server error loading sessions (500)');
        return { error: true, isServerError: true };
      }
      console.error('Error loading sessions:', error);
      return { error: true };
    } finally {
      setLoading(false);
    }
  }, [currentUserId]);

  const loadSessionStatuses = useCallback(async () => {
    const statusMap = {};
    
    for (const session of sessions) {
      try {
        // UNIFIED API: Use data already included in session from getUserSessions
        // Backend now includes active_jobs, results, and failed_jobs in the session object
        const results = session.results || [];
        const activeJobsFromAPI = session.active_jobs || [];
        const failedJobsFromAPI = session.failed_jobs || [];
        
        // Extract document info from completed results
        const documents = results.map(result => {
          // Check if redlines were actually generated
          const hasRedlines = !!(result.redlined_document_s3_key || result.redlined_document);
          const conflictsCount = result.conflicts_count || result.conflicts_found || 0;
          const hasAnalysisId = !!result.analysis_id;
          
          // Determine actual status based on completion criteria:
          // 1. Completed: Has redlines OR (no conflicts AND has analysis_id)
          // 2. Failed: Has conflicts but no redlines (redline generation failed)
          // 3. Unknown: No analysis_id (might be processing or error)
          let docStatus = 'unknown';
          if (hasRedlines) {
            // Redlines were generated - definitely completed
            docStatus = 'completed';
          } else if (conflictsCount === 0 && hasAnalysisId) {
            // No conflicts case - valid completion (no redlines needed)
            docStatus = 'completed';
          } else if (conflictsCount > 0 && hasAnalysisId && !hasRedlines) {
            // Has conflicts but no redlines - redline generation failed
            docStatus = 'failed';
          } else if (result.status) {
            // Use status from backend if available
            docStatus = result.status;
          }
          
          return {
            documentName: extractDocumentName(result.document_s3_key || ''),
            status: docStatus,
            hasRedlines: hasRedlines,
            conflictsFound: conflictsCount,
            updatedAt: result.updated_at || result.timestamp,
            jobId: result.analysis_id,
            redlinedDocumentS3Key: result.redlined_document_s3_key || result.redlined_document
          };
        });
        
        // Process active jobs from API response
        // Backend already returns only the most recent active job per session
        // Frontend just displays what backend returns - no cleanup needed
        let activeJobs = activeJobsFromAPI.map(job => ({
          jobId: job.job_id || job.analysis_id,
          status: job.status || 'processing', // Can be 'processing', 'failed', 'completed'
          progress: job.progress || 0,
          stage: job.stage || 'initialized',
          documentS3Key: job.document_s3_key,
          updatedAt: job.updated_at || job.timestamp,
          errorMessage: job.stage_message || job.error_message
        }));
        
        // Process failed jobs from API response
        // These are jobs that failed but are not active anymore
        const failedJobs = failedJobsFromAPI.map(job => ({
          jobId: job.job_id || job.analysis_id,
          status: 'failed',
          progress: job.progress || 0,
          stage: job.stage || 'failed',
          documentS3Key: job.document_s3_key,
          updatedAt: job.updated_at || job.timestamp,
          errorMessage: job.stage_message || job.error_message || 'Processing failed'
        }));
        
        // Convert failed jobs to failed documents for status display
        failedJobs.forEach(failedJob => {
          if (failedJob.documentS3Key) {
            documents.push({
              documentName: extractDocumentName(failedJob.documentS3Key),
              status: 'failed',
              hasRedlines: false,
              conflictsFound: 0,
              updatedAt: failedJob.updatedAt,
              jobId: failedJob.jobId,
              redlinedDocumentS3Key: null
            });
          }
        });
        
        // Backend enforces: only 1 active job per session
        // Remove completed jobs (they should be in documents, not activeJobs)
        activeJobs = activeJobs.filter(job => job.status !== 'completed');
        
        // Also check window state for any jobs that might not be in DynamoDB yet (race condition)
        if (window.processingSessionFlags && window.processingSessionFlags[session.session_id]) {
          const processingDetails = window.processingSessionFlags[session.session_id];
          if (processingDetails.jobId) {
            // Check if this job is already in activeJobs
            const alreadyTracked = activeJobs.some(j => j.jobId === processingDetails.jobId);
            if (!alreadyTracked) {
              // Job not in API response yet, poll it directly
              try {
                const jobStatus = await agentAPI.getJobStatus(processingDetails.jobId);
                if (jobStatus.success) {
                  // API returns job data directly, not wrapped in 'job' field
                  const status = jobStatus.status || 'processing';
                  if (status === 'processing') {
                    activeJobs.push({
                      jobId: processingDetails.jobId,
                      status: 'processing',
                      progress: jobStatus.progress || 0,
                      stage: jobStatus.stage || 'initialized',
                      documentS3Key: jobStatus.document_s3_key
                    });
                  }
                }
              } catch (error) {
                console.warn(`Error checking job status for ${processingDetails.jobId}:`, error);
              }
            }
          }
        }
        
        // Calculate total conflicts count from all completed documents
        const totalConflictsCount = documents
          .filter(doc => doc.status === 'completed')
          .reduce((sum, doc) => sum + (doc.conflictsFound || 0), 0);
        
        statusMap[session.session_id] = {
          documents,
          activeJobs,
          documentCount: documents.length,
          conflictsCount: totalConflictsCount,
          hasResults: documents.length > 0 || activeJobs.length > 0
        };
      } catch (error) {
        console.error(`Error loading status for session ${session.session_id}:`, error);
        statusMap[session.session_id] = {
          documents: [],
          activeJobs: [],
          documentCount: 0,
          conflictsCount: 0,
          hasResults: false
        };
      }
    }
    
    setSessionStatuses(statusMap);
  }, [sessions]);

  // Use ref for loadSessionStatuses to avoid circular dependency
  const loadSessionStatusesRef = useRef(loadSessionStatuses);
  useEffect(() => {
    loadSessionStatusesRef.current = loadSessionStatuses;
  }, [loadSessionStatuses]);

  // Use centralized polling service
  const handleJobStatusUpdate = useCallback((statusResponse) => {
    const { status, stage, progress, session_id, document_s3_key, job_id } = statusResponse;
    
    if (!session_id || !job_id) return;
    
    setSessionStatuses(prev => {
      const updated = { ...prev };
      if (!updated[session_id]) {
        updated[session_id] = { documents: [], activeJobs: [], documentCount: 0, conflictsCount: 0, hasResults: false };
      }
      
      // Update or add job status
      const jobIndex = updated[session_id].activeJobs.findIndex(j => j.jobId === job_id);
      const existingJob = jobIndex >= 0 ? updated[session_id].activeJobs[jobIndex] : null;
      const progressValue = typeof progress === 'number' ? progress : parseInt(progress || 0, 10);
      
      const jobStatus = {
        jobId: job_id,
        status: status || 'processing',
        stage: stage || 'initialized',
        progress: progressValue,
        documentS3Key: existingJob?.documentS3Key || document_s3_key || null
      };
      
      if (jobIndex >= 0) {
        updated[session_id].activeJobs[jobIndex] = jobStatus;
      } else {
        updated[session_id].activeJobs.push(jobStatus);
      }
      
      return updated;
    });
  }, []);
  
  const handleJobComplete = useCallback((statusResponse) => {
    const { session_id, job_id } = statusResponse;
    if (session_id) {
      setSessionStatuses(prev => {
        const updated = { ...prev };
        if (updated[session_id]) {
          // Remove completed job
          updated[session_id].activeJobs = updated[session_id].activeJobs.filter(j => j.jobId !== job_id);
        }
        return updated;
      });
      // Reload session statuses to get updated document list
      setTimeout(() => loadSessionStatusesRef.current(), 1000);
    }
  }, []);
  
  const handleJobError = useCallback((statusResponse) => {
    // Failed jobs remain visible but stop polling
    const { job_id } = statusResponse;
    console.warn(`Job ${job_id} failed:`, statusResponse.error_message || statusResponse.error);
  }, []);

  // Use ref to access latest sessionStatuses without causing re-renders
  const sessionStatusesRef = useRef(sessionStatuses);
  useEffect(() => {
    sessionStatusesRef.current = sessionStatuses;
  }, [sessionStatuses]);

  // Load user sessions
  useEffect(() => {
    if (currentUserId && isVisible) {
      loadSessions();
    }
  }, [currentUserId, isVisible, loadSessions]);

  // Load session statuses (documents and active jobs) for all sessions
  useEffect(() => {
    if (sessions.length > 0 && currentUserId) {
      loadSessionStatuses();
    }
  }, [sessions, currentUserId, loadSessionStatuses]);

  // Expose refresh function to window for external triggers
  useEffect(() => {
    window.triggerSessionSidebarRefresh = () => {
      if (currentUserId) {
        // CRITICAL: Always reload sessions list first (to pick up new/completed sessions)
        // Then reload statuses for all sessions
        loadSessions().then(() => {
          // Small delay to ensure sessions state is updated before loading statuses
          setTimeout(() => {
            if (sessions.length > 0) {
              loadSessionStatuses();
            }
          }, 100);
        }).catch(error => {
          console.error('Error refreshing sessions:', error);
        });
      }
    };
    return () => {
      delete window.triggerSessionSidebarRefresh;
    };
  }, [sessions, currentUserId, loadSessions, loadSessionStatuses]);

  // Use centralized polling service for active jobs
  useEffect(() => {
    if (sessions.length === 0) return;
    
    // Get all processing job IDs from current state
    const processingJobIds = new Set();
    Object.values(sessionStatusesRef.current).forEach(status => {
      if (status.activeJobs && status.activeJobs.length > 0) {
        status.activeJobs.forEach(job => {
          if (job.status === 'processing' && job.jobId) {
            processingJobIds.add(job.jobId);
          }
        });
      }
    });
    
    // Also check window state for active processing jobs
    if (window.processingSessionFlags) {
      Object.values(window.processingSessionFlags).forEach(details => {
        if (details.jobId) {
          processingJobIds.add(details.jobId);
        }
      });
    }
    
    // Start polling for each processing job using centralized service
    const stopFunctions = [];
    processingJobIds.forEach(jobId => {
      const stop = jobPollingService.startPolling(jobId, {
        onUpdate: handleJobStatusUpdate,
        onComplete: handleJobComplete,
        onError: handleJobError
      });
      stopFunctions.push(stop);
    });
    
    // Reload session statuses periodically to catch new jobs
    const reloadInterval = setInterval(() => {
      loadSessionStatusesRef.current();
    }, 10000); // Every 10 seconds
    
    return () => {
      stopFunctions.forEach(stop => stop());
      clearInterval(reloadInterval);
    };
  }, [sessions.length, handleJobStatusUpdate, handleJobComplete, handleJobError]);

  // Refresh sessions when sessionId changes
  useEffect(() => {
    if (currentUserId && isVisible && sessionId) {
      loadSessions();
      
      let retryCount = 0;
      const maxRetries = 5;
      let consecutiveServerErrors = 0;
      
      const retryInterval = setInterval(async () => {
        retryCount++;
        
        if (retryCount >= maxRetries) {
          clearInterval(retryInterval);
          return;
        }
        
        const result = await loadSessions();
        
        if (result && result.isServerError) {
          consecutiveServerErrors++;
          if (consecutiveServerErrors >= 2) {
            clearInterval(retryInterval);
            return;
          }
        } else if (result && result.success) {
          consecutiveServerErrors = 0;
        }
      }, 1500);
      
      return () => clearInterval(retryInterval);
    }
  }, [sessionId, currentUserId, isVisible, loadSessions]);

  // Refresh sessions periodically
  useEffect(() => {
    if (!currentUserId || !isVisible) return;
    
    const refreshInterval = setInterval(() => {
      loadSessions();
    }, 30000);

    return () => clearInterval(refreshInterval);
  }, [currentUserId, isVisible, loadSessions]);

  useEffect(() => {
    if (onRefreshRequest) {
      loadSessions();
    }
  }, [onRefreshRequest, loadSessions]);

  const formatDate = (dateString) => {
    if (!dateString) return '';
    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);
    
    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;
    
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined });
  };

  const getSessionDisplayName = (session) => {
    const status = sessionStatuses[session.session_id];
    if (!status) {
      return session.title || 'New Session';
    }
    
    // Since each session has only 1 job, check activeJobs first
    // Prioritize processing jobs over failed jobs
    if (status.activeJobs && status.activeJobs.length > 0) {
      // Find processing job first, then fallback to failed job
      const processingJob = status.activeJobs.find(job => job.status === 'processing');
      const job = processingJob || status.activeJobs[0]; // Use processing job if exists, otherwise first job
      
      if (job.documentS3Key) {
        const docName = extractDocumentName(job.documentS3Key);
        return docName.length > 30 ? docName.substring(0, 30) + '...' : docName;
      }
    }
    
    // Fallback to completed documents
    if (status.documents && status.documents.length > 0) {
      const firstDoc = status.documents[0];
      if (firstDoc.documentName) {
        return firstDoc.documentName.length > 30 
          ? firstDoc.documentName.substring(0, 30) + '...'
          : firstDoc.documentName;
      }
    }
    
    return session.title || 'New Session';
  };

  const getSessionStatus = (session) => {
    const status = sessionStatuses[session.session_id];
    
    // If status hasn't loaded yet, check session data directly from backend
    if (!status) {
      // Use backend data directly if available
      const backendResults = session.results || [];
      const backendActiveJobs = session.active_jobs || [];
      const backendFailedJobs = session.failed_jobs || [];
      
      // Check active jobs first
      if (backendActiveJobs.length > 0) {
        const processingJob = backendActiveJobs.find(job => job.status === 'processing');
        const failedJob = backendActiveJobs.find(job => job.status === 'failed');
        const job = processingJob || failedJob;
        
        if (job) {
          if (job.status === 'processing') {
            const progressValue = typeof job.progress === 'number' ? job.progress : parseInt(job.progress || 0, 10);
            return { 
              type: 'processing', 
              label: `${progressValue}%`, 
              color: '#3b82f6',
              progress: progressValue
            };
          }
          if (job.status === 'failed') {
            return { type: 'failed', label: 'Failed', color: '#ef4444' };
          }
        }
      }
      
      // Check failed jobs
      if (backendFailedJobs.length > 0) {
        return { type: 'failed', label: 'Failed', color: '#ef4444' };
      }
      
      // Check completed results
      if (backendResults.length > 0) {
        return { type: 'completed', label: 'Complete', color: '#10b981' };
      }
      
      // Check if session has results flag
      if (session.has_results || session.has_content) {
        return { type: 'completed', label: 'Complete', color: '#10b981' };
      }
      
      return { type: 'empty', label: 'Empty', color: '#666' };
    }
    
    // Check activeJobs first (since each session has only 1 job)
    // Prioritize processing jobs over failed jobs
    if (status.activeJobs && status.activeJobs.length > 0) {
      // Find processing job first, then fallback to failed job
      const processingJob = status.activeJobs.find(job => job.status === 'processing');
      const failedJob = status.activeJobs.find(job => job.status === 'failed');
      const job = processingJob || failedJob; // Prioritize processing over failed
      
      if (job) {
        // Check for processing job first (highest priority)
        if (job.status === 'processing') {
          const progressValue = typeof job.progress === 'number' ? job.progress : parseInt(job.progress || 0, 10);
          return { 
            type: 'processing', 
            label: `${progressValue}%`, 
            color: '#3b82f6',
            progress: progressValue
          };
        }
        
        // Check for failed job
        if (job.status === 'failed') {
          return { type: 'failed', label: 'Failed', color: '#ef4444' };
        }
      }
    }
    
    // Check for documents (completed or failed)
    if (status.documents && status.documents.length > 0) {
      // Check for failed documents first
      const failedDocs = status.documents.filter(d => d.status === 'failed');
      if (failedDocs.length > 0) {
        return { type: 'failed', label: 'Failed', color: '#ef4444' };
      }
      
      // Check for completed documents
      const completedDocs = status.documents.filter(d => d.status === 'completed');
      if (completedDocs.length > 0) {
        return { type: 'completed', label: 'Complete', color: '#10b981' };
      }
      
      // If documents exist but status is unknown, still show something
      // (shouldn't happen, but handle gracefully)
      return { type: 'completed', label: 'Complete', color: '#10b981' };
    }
    
    // Only return empty if truly no content
    return { type: 'empty', label: 'Empty', color: '#666' };
  };

  const handleNewSession = async () => {
    try {
      setCreatingSession(true);
      const response = await sessionAPI.createSession(currentUserId);
      
      let responseData = response;
      if (response.body) {
        try {
          responseData = typeof response.body === 'string' ? JSON.parse(response.body) : response.body;
        } catch (e) {
          console.error('Error parsing response body:', e);
          responseData = response;
        }
      }
      
      if (responseData.success && responseData.session) {
        const newSession = responseData.session;
        setSessions(prevSessions => {
          const exists = prevSessions.some(s => s.session_id === newSession.session_id);
          if (exists) return prevSessions;
          return [newSession, ...prevSessions];
        });
        
        setTimeout(async () => {
          try {
            await loadSessions();
          } catch (error) {
            console.warn('Failed to refresh sessions after creation:', error);
          }
        }, 1000);
        
        navigate(`/${newSession.session_id}`, { 
          state: { session: newSession } 
        });
      } else {
        console.error('Failed to create session:', responseData.error || 'Unknown error');
        alert('Failed to create new session. Please try again.');
      }
    } catch (error) {
      console.error('Error creating new session:', error);
      alert(`Error creating new session: ${error.message || 'Unknown error'}`);
    } finally {
      setCreatingSession(false);
    }
  };

  const handleSessionSelect = (session) => {
    if (session.session_id === sessionId) return;
    navigate(`/${session.session_id}`, { 
      state: { session: session } 
    });
  };

  const handleEditTitle = (session) => {
    setEditingSession(session.session_id);
    setEditTitle(session.title || '');
  };

  const handleSaveTitle = async (sessionIdToUpdate) => {
    if (!editTitle.trim()) {
      setEditingSession(null);
      return;
    }
    
    try {
      await sessionAPI.updateSessionTitle(sessionIdToUpdate, currentUserId, editTitle.trim());
      setSessions(prev => prev.map(s => 
        s.session_id === sessionIdToUpdate 
          ? { ...s, title: editTitle.trim() }
          : s
      ));
      setEditingSession(null);
    } catch (error) {
      console.error('Error updating session title:', error);
      alert('Failed to update session title');
    }
  };

  const handleDeleteSession = async (sessionIdToDelete) => {
    if (!window.confirm('Are you sure you want to delete this session? This action cannot be undone.')) {
      return;
    }
    
    try {
      await sessionAPI.deleteSession(sessionIdToDelete, currentUserId);
      setSessions(prev => prev.filter(s => s.session_id !== sessionIdToDelete));
      
      if (sessionIdToDelete === sessionId) {
        navigate('/');
      }
    } catch (error) {
      console.error('Error deleting session:', error);
      alert('Failed to delete session');
    }
  };

  if (!isVisible) return null;

  return (
    <div style={{
      position: 'fixed',
      top: '60px',
      left: '0',
      width: isCollapsed ? '48px' : '320px',
      height: 'calc(100vh - 60px)',
      backgroundColor: '#171717',
      color: '#ffffff',
      borderRight: '1px solid #333',
      display: 'flex',
      flexDirection: 'column',
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
      transition: 'width 0.2s ease',
      overflow: 'hidden',
      zIndex: 100
    }}>
      {/* Collapse Toggle Button */}
      <button
        onClick={toggleCollapsed}
        style={{
          position: 'absolute',
          top: '12px',
          right: isCollapsed ? '10px' : '12px',
          width: '28px',
          height: '28px',
          backgroundColor: '#333',
          color: '#fff',
          border: '1px solid #444',
          borderRadius: '4px',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: '14px',
          zIndex: 10,
          transition: 'background-color 0.2s'
        }}
        onMouseEnter={(e) => e.currentTarget.style.backgroundColor = '#444'}
        onMouseLeave={(e) => e.currentTarget.style.backgroundColor = '#333'}
        title={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      >
        {isCollapsed ? '»' : '«'}
      </button>
      
      {/* Header */}
      <div style={{
        padding: isCollapsed ? '48px 8px 12px' : '16px',
        borderBottom: '1px solid #333',
        opacity: isCollapsed ? 0 : 1,
        visibility: isCollapsed ? 'hidden' : 'visible',
        transition: 'opacity 0.2s'
      }}>
        <button
          onClick={handleNewSession}
          disabled={creatingSession}
          style={{
            width: '100%',
            padding: '12px',
            backgroundColor: '#1f1f1f',
            color: '#ffffff',
            border: '1px solid #333',
            borderRadius: '6px',
            cursor: creatingSession ? 'not-allowed' : 'pointer',
            fontSize: '14px',
            fontWeight: '500',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '8px',
            transition: 'background-color 0.2s'
          }}
          onMouseEnter={(e) => {
            if (!creatingSession) e.target.style.backgroundColor = '#333';
          }}
          onMouseLeave={(e) => {
            if (!creatingSession) e.target.style.backgroundColor = '#1f1f1f';
          }}
        >
          <span style={{ fontSize: '16px' }}>+</span>
          {creatingSession ? 'Creating...' : 'New Session'}
        </button>
      </div>

      {/* Sessions List */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        padding: isCollapsed ? '8px 4px' : '12px',
        opacity: isCollapsed ? 0 : 1,
        visibility: isCollapsed ? 'hidden' : 'visible',
        transition: 'opacity 0.2s'
      }}>
        {loading && sessions.length === 0 ? (
          <div style={{
            padding: '16px',
            textAlign: 'center',
            color: '#888',
            fontSize: '14px'
          }}>
            Loading sessions...
          </div>
        ) : sessions.length === 0 ? (
          <div style={{
            padding: '16px',
            textAlign: 'center',
            color: '#888',
            fontSize: '14px'
          }}>
            No sessions yet
          </div>
        ) : (
          sessions.map((session) => {
            const isActive = sessionId === session.session_id;
            const status = getSessionStatus(session);
            const displayName = getSessionDisplayName(session);
            const sessionData = sessionStatuses[session.session_id];
            
            // Calculate document count and conflicts count
            let documentCount = sessionData?.documentCount || 0;
            let conflictsCount = sessionData?.conflictsCount || 0;
            
            // If status hasn't loaded yet, use backend data directly
            if (!sessionData) {
              const backendResults = session.results || [];
              documentCount = backendResults.length;
              conflictsCount = backendResults
                .filter(result => result.conflicts_count > 0)
                .reduce((sum, result) => sum + (result.conflicts_count || 0), 0);
            }
            
            return (
              <div
                key={session.session_id}
                style={{
                  marginBottom: '8px',
                  borderRadius: '8px',
                  backgroundColor: isActive ? '#2a2a2a' : 'transparent',
                  border: isActive ? '1px solid #444' : '1px solid transparent',
                  transition: 'all 0.2s',
                  cursor: 'pointer'
                }}
                onMouseEnter={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.backgroundColor = '#1f1f1f';
                    e.currentTarget.style.borderColor = '#333';
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.backgroundColor = 'transparent';
                    e.currentTarget.style.borderColor = 'transparent';
                  }
                }}
                onClick={() => handleSessionSelect(session)}
              >
                <div style={{
                  padding: '12px'
                }}>
                  {/* Session Header */}
                  <div style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    justifyContent: 'space-between',
                    gap: '8px',
                    marginBottom: '8px'
                  }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      {editingSession === session.session_id ? (
                        <input
                          type="text"
                          value={editTitle}
                          onChange={(e) => setEditTitle(e.target.value)}
                          onBlur={() => handleSaveTitle(session.session_id)}
                          onKeyPress={(e) => {
                            if (e.key === 'Enter') {
                              handleSaveTitle(session.session_id);
                            }
                          }}
                          onClick={(e) => e.stopPropagation()}
                          style={{
                            backgroundColor: '#1f1f1f',
                            color: '#ffffff',
                            border: '1px solid #555',
                            borderRadius: '4px',
                            padding: '4px 8px',
                            fontSize: '14px',
                            fontWeight: '500',
                            width: '100%'
                          }}
                          autoFocus
                        />
                      ) : (
                        <div style={{
                          fontSize: '14px',
                          fontWeight: '500',
                          color: '#ffffff',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          marginBottom: '4px'
                        }}>
                          {displayName}
                        </div>
                      )}
                      
                      {/* Status Badge, Conflicts Count, and Document Count */}
                      <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        marginTop: '4px',
                        flexWrap: 'wrap'
                      }}>
                        <span style={{
                          fontSize: '11px',
                          fontWeight: '500',
                          color: status.color,
                          backgroundColor: status.color + '20',
                          padding: '2px 6px',
                          borderRadius: '4px',
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '4px'
                        }}>
                          {status.type === 'processing' && (
                            <span style={{
                              width: '6px',
                              height: '6px',
                              borderRadius: '50%',
                              backgroundColor: status.color,
                              animation: 'pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite'
                            }}></span>
                          )}
                          {status.label}
                        </span>
                        
                        {conflictsCount > 0 && status.type === 'completed' && (
                          <span style={{
                            fontSize: '11px',
                            color: '#f59e0b',
                            fontWeight: '500'
                          }}>
                            {conflictsCount} {conflictsCount === 1 ? 'conflict' : 'conflicts'}
                          </span>
                        )}
                        
                        {documentCount > 0 && (
                          <span style={{
                            fontSize: '11px',
                            color: '#888'
                          }}>
                            {documentCount} {documentCount === 1 ? 'doc' : 'docs'}
                          </span>
                        )}
                      </div>
                    </div>
                    
                    {/* Actions */}
                    <div style={{
                      display: 'flex',
                      gap: '4px',
                      opacity: 0.6,
                      transition: 'opacity 0.2s'
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.opacity = 1;
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.opacity = 0.6;
                    }}
                    onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleEditTitle(session);
                        }}
                        style={{
                          background: 'none',
                          border: 'none',
                          color: '#888',
                          cursor: 'pointer',
                          padding: '4px',
                          fontSize: '11px',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center'
                        }}
                        title="Rename session"
                        onMouseEnter={(e) => {
                          e.currentTarget.style.color = '#fff';
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.color = '#888';
                        }}
                      >
                        ✎
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDeleteSession(session.session_id);
                        }}
                        style={{
                          background: 'none',
                          border: 'none',
                          color: '#ef4444',
                          cursor: 'pointer',
                          padding: '4px',
                          fontSize: '14px',
                          fontWeight: 'bold',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          width: '18px',
                          height: '18px'
                        }}
                        title="Delete session"
                        onMouseEnter={(e) => {
                          e.currentTarget.style.color = '#ff6b6b';
                          e.currentTarget.style.backgroundColor = '#ef444420';
                          e.currentTarget.style.borderRadius = '4px';
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.color = '#ef4444';
                          e.currentTarget.style.backgroundColor = 'transparent';
                        }}
                      >
                        ×
                      </button>
                    </div>
                  </div>
                  
                  {/* Processing Progress Bar */}
                  {status.type === 'processing' && status.progress !== undefined && (
                    <div style={{
                      width: '100%',
                      height: '3px',
                      backgroundColor: '#333',
                      borderRadius: '2px',
                      overflow: 'hidden',
                      marginTop: '8px'
                    }}>
                      <div style={{
                        width: `${status.progress}%`,
                        height: '100%',
                        backgroundColor: status.color,
                        transition: 'width 0.3s ease'
                      }}></div>
                    </div>
                  )}
                  
                  {/* Time */}
                  <div style={{
                    fontSize: '11px',
                    color: '#666',
                    marginTop: '6px'
                  }}>
                    {formatDate(session.updated_at || session.created_at)}
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Admin Section */}
      {isAdmin && !isCollapsed && (
        <div style={{
          borderTop: '1px solid #333'
        }}>
          <button
            onClick={() => setAdminExpanded(!adminExpanded)}
            style={{
              width: '100%',
              padding: '12px',
              backgroundColor: 'transparent',
              color: '#ffffff',
              border: 'none',
              cursor: 'pointer',
              fontSize: '14px',
              fontWeight: '500',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              transition: 'background-color 0.2s'
            }}
            onMouseEnter={(e) => {
              e.target.style.backgroundColor = '#1f1f1f';
            }}
            onMouseLeave={(e) => {
              e.target.style.backgroundColor = 'transparent';
            }}
          >
            <span>Admin</span>
            <span style={{
              fontSize: '12px',
              transform: adminExpanded ? 'rotate(180deg)' : 'rotate(0deg)',
              transition: 'transform 0.2s'
            }}>
              ▼
            </span>
          </button>
          
          {adminExpanded && (
            <div style={{ paddingLeft: '12px', paddingBottom: '8px' }}>
              <button
                onClick={() => onAdminSectionChange && onAdminSectionChange('admin')}
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  backgroundColor: 'transparent',
                  color: '#cccccc',
                  border: 'none',
                  cursor: 'pointer',
                  textAlign: 'left',
                  fontSize: '13px'
                }}
                onMouseEnter={(e) => {
                  e.target.style.backgroundColor = '#1f1f1f';
                }}
                onMouseLeave={(e) => {
                  e.target.style.backgroundColor = 'transparent';
                }}
              >
                Knowledge Base
              </button>
              <button
                onClick={() => onAdminSectionChange && onAdminSectionChange('metrics')}
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  backgroundColor: 'transparent',
                  color: '#cccccc',
                  border: 'none',
                  cursor: 'pointer',
                  textAlign: 'left',
                  fontSize: '13px'
                }}
                onMouseEnter={(e) => {
                  e.target.style.backgroundColor = '#1f1f1f';
                }}
                onMouseLeave={(e) => {
                  e.target.style.backgroundColor = 'transparent';
                }}
              >
                Metrics
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default SessionSidebar;
