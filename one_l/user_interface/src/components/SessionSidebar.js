import React, { useState, useEffect } from 'react';
import { sessionAPI } from '../services/api';
import { useNavigate, useParams } from 'react-router-dom';

const SessionSidebar = ({ 
  currentUserId, 
  isVisible = true,
  onAdminSectionChange,
  onRefreshRequest // Add callback to allow parent to trigger refresh
}) => {
  const navigate = useNavigate();
  const { sessionId } = useParams();
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [creatingSession, setCreatingSession] = useState(false);
  const [editingSession, setEditingSession] = useState(null);
  const [editTitle, setEditTitle] = useState('');
  const [adminExpanded, setAdminExpanded] = useState(false);

  // Load user sessions
  useEffect(() => {
    if (currentUserId && isVisible) {
      loadSessions();
    }
  }, [currentUserId, isVisible]); // eslint-disable-line react-hooks/exhaustive-deps

  // Refresh sessions when sessionId changes (user navigates to different session)
  // This ensures the first session created by AutoSessionRedirect appears in the sidebar
  useEffect(() => {
    if (currentUserId && isVisible && sessionId) {
      // Load sessions immediately
      loadSessions();
      
      // Retry to handle DynamoDB eventual consistency
      // This is especially important for the first session created on app load
      // Retry multiple times to ensure the session appears, but stop on server errors
      let retryCount = 0;
      const maxRetries = 5; // Increased retries for first session (handles eventual consistency)
      let consecutiveServerErrors = 0;
      
      const retryInterval = setInterval(async () => {
        retryCount++;
        
        // Stop if we've hit max retries
        if (retryCount >= maxRetries) {
          clearInterval(retryInterval);
          return;
        }
        
        // Try loading sessions
        const result = await loadSessions();
        
        // If we get a server error (500), stop retrying - it's a backend issue, not eventual consistency
        if (result && result.isServerError) {
          consecutiveServerErrors++;
          // Stop after 2 consecutive server errors
          if (consecutiveServerErrors >= 2) {
            console.warn('Stopping session retry due to persistent server errors');
            clearInterval(retryInterval);
            return;
          }
        } else if (result && result.success) {
          // Reset error count on success
          consecutiveServerErrors = 0;
        }
      }, 1500); // Retry every 1.5 seconds
      
      return () => clearInterval(retryInterval);
    }
  }, [sessionId, currentUserId, isVisible]); // eslint-disable-line react-hooks/exhaustive-deps

  // Refresh sessions periodically to catch newly completed sessions
  useEffect(() => {
    if (!currentUserId || !isVisible) return;
    
    const refreshInterval = setInterval(() => {
      loadSessions();
    }, 30000); // Refresh every 30 seconds

    return () => clearInterval(refreshInterval);
  }, [currentUserId, isVisible]); // eslint-disable-line react-hooks/exhaustive-deps

  // Listen for refresh requests from parent
  useEffect(() => {
    if (onRefreshRequest) {
      loadSessions();
    }
  }, [onRefreshRequest]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadSessions = async () => {
    try {
      setLoading(true);
      // Load ALL sessions (including new ones without results) so they appear in sidebar
      const response = await sessionAPI.getUserSessions(currentUserId, false);
      
      // Handle different response structures (wrapped in body or direct)
      let responseData = response;
      if (response && response.body) {
        try {
          responseData = typeof response.body === 'string' ? JSON.parse(response.body) : response.body;
        } catch (e) {
          console.error('Error parsing response body:', e);
          responseData = response;
        }
      }
      
      // Handle HTTP errors (like 500) that might be in the response
      if (response && response.statusCode && response.statusCode >= 400) {
        console.error('HTTP error loading sessions:', response.statusCode, responseData);
        // Don't clear existing sessions on error - keep what we have
        // Return error indicator so retry logic can stop
        return { error: true, statusCode: response.statusCode };
      }
      
      if (responseData && responseData.success) {
        setSessions(responseData.sessions || []);
        return { success: true };
      } else {
        console.error('Failed to load sessions:', responseData?.error || 'Unknown error');
        // Don't clear existing sessions on error - keep what we have
        return { error: true };
      }
    } catch (error) {
      // Handle 500 errors and other network errors gracefully
      if (error.message && error.message.includes('500')) {
        console.warn('Server error loading sessions (500). Sessions may be temporarily unavailable.');
        // Don't clear existing sessions - keep what we have displayed
        // Return error indicator so retry logic can handle it
        return { error: true, isServerError: true, statusCode: 500 };
      } else {
        console.error('Error loading sessions:', error);
        console.error('Error details:', {
          message: error.message,
          stack: error.stack,
          name: error.name
        });
        return { error: true };
      }
      // Don't clear existing sessions on error - keep what we have
    } finally {
      setLoading(false);
    }
  };

  const getParallelSessionWarning = (action) => (
    'A redline is currently running.\n' +
    `If you ${action}, the progress indicator will be lost and results may not appear properly.\n\n` +
    'We recommend waiting for the current redline to complete.\n\n' +
    'Continue anyway?'
  );

  const getActiveProcessingStatus = () => {
    const progressIntervalActive = window.progressInterval !== null && window.progressInterval !== undefined;

    let currentJobActive = false;
    if (window.currentProcessingJob) {
      if (!sessionId) {
        currentJobActive = true;
      } else {
        const jobSessionId = window.currentProcessingJob.sessionId;
        currentJobActive = !jobSessionId || jobSessionId === sessionId;
      }
    }

    const inspectEntry = (entry) => {
      if (!entry) return false;
      const isGenerating = entry.generating === true;
      const hasProcessingStage = Boolean(entry.processingStage && entry.processingStage !== '');
      const hasProcessingDocs = Array.isArray(entry.redlinedDocuments) &&
        entry.redlinedDocuments.some(doc =>
          doc.processing === true ||
          doc.status === 'processing' ||
          (typeof doc.progress === 'number' && doc.progress !== undefined && doc.progress < 100)
        );
      return isGenerating || hasProcessingStage || hasProcessingDocs;
    };

    let sessionData = null;
    let storageProcessing = false;
    if (currentUserId) {
      try {
        const storageKey = `one_l_session_data_${currentUserId}`;
        const stored = localStorage.getItem(storageKey);
        if (stored) {
          sessionData = JSON.parse(stored);

          if (sessionId && sessionData?.[sessionId]) {
            storageProcessing = inspectEntry(sessionData[sessionId]);
          } else if (!sessionId && sessionData) {
            storageProcessing = Object.values(sessionData).some(inspectEntry);
          }
        }
      } catch (error) {
        console.error('Error checking processing status:', error);
      }
    }

    let globalProcessing = false;
    if (window.processingSessionFlags) {
      const flags = Object.entries(window.processingSessionFlags);
      for (const [flagSessionId, details] of flags) {
        const entry = sessionData?.[flagSessionId];
        const stillProcessing = inspectEntry(entry);
        // Expire stale flags older than 5 minutes even if we can't confirm processing
        const tooOld = details?.updatedAt && (Date.now() - details.updatedAt) > 5 * 60 * 1000;

        if (!stillProcessing || tooOld) {
          delete window.processingSessionFlags[flagSessionId];
          continue;
        }

        if (!sessionId || flagSessionId === sessionId) {
          globalProcessing = true;
        }
      }
    }

    const isProcessing = progressIntervalActive || currentJobActive || storageProcessing || globalProcessing;

    return {
      hasProgressInterval: progressIntervalActive,
      hasActiveJob: currentJobActive,
      hasStorageProcessing: storageProcessing,
      hasGlobalProcessing: globalProcessing,
      isProcessing
    };
  };

  const handleNewSession = async () => {
    const processingStatus = getActiveProcessingStatus();

    if (processingStatus.isProcessing) {
      const proceedWithParallelWarning = window.confirm(
        getParallelSessionWarning('create a new session')
      );

      if (!proceedWithParallelWarning) {
        return;
      }
    }

    try {
      setCreatingSession(true);
      const response = await sessionAPI.createSession(currentUserId);
      
      // Handle different response structures (wrapped in body or direct)
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
        // Add the new session to the list immediately (optimistic update)
        const newSession = responseData.session;
        setSessions(prevSessions => {
          // Check if session already exists to avoid duplicates
          const exists = prevSessions.some(s => s.session_id === newSession.session_id);
          if (exists) {
            return prevSessions;
          }
          // Add new session at the beginning
          return [newSession, ...prevSessions];
        });
        
        // Refresh sessions list from server after a short delay to handle eventual consistency
        // Use silent refresh to avoid errors interrupting the flow
        setTimeout(async () => {
          try {
            const result = await loadSessions();
            // If we get a server error, don't keep retrying - the optimistic update is already in place
            if (result && result.isServerError) {
              console.warn('Server error refreshing sessions after creation. Session is already in sidebar via optimistic update.');
            }
          } catch (error) {
            // Silently fail - we already have the session in the list via optimistic update
            console.warn('Failed to refresh sessions list after creation:', error);
          }
        }, 1000);
        
        // Navigate to the new session with new URL structure
        navigate(`/${newSession.session_id}`, { 
          state: { session: newSession } 
        });
      } else {
        console.error('Failed to create session:', responseData.error || 'Unknown error');
        alert('Failed to create new session. Please try again.');
      }
    } catch (error) {
      console.error('Error creating new session:', error);
      console.error('Error details:', {
        message: error.message,
        stack: error.stack,
        name: error.name
      });
      alert(`Error creating new session: ${error.message || 'Unknown error'}`);
    } finally {
      setCreatingSession(false);
    }
  };

  const handleSessionSelect = (session) => {
    if (session.session_id === sessionId) {
      return;
    }

    // Check if there's active processing in the current session
    const processingStatus = getActiveProcessingStatus();
    
    // Show warning if there's active processing
    if (processingStatus.isProcessing) {
      const confirmed = window.confirm(
        getParallelSessionWarning('switch sessions')
      );
      if (!confirmed) {
        return; // Don't navigate if user cancels
      }
    }
    
    // If we get here, either no processing or user confirmed
    if (processingStatus.hasProgressInterval) {
      // Clear the progress interval when switching away
      if (window.progressInterval) {
        clearInterval(window.progressInterval);
        window.progressInterval = null;
      }
    }
    
    navigate(`/${session.session_id}`, { 
      state: { session: session } 
    });
  };

  const handleEditTitle = (session) => {
    setEditingSession(session.session_id);
    setEditTitle(session.title);
  };

  const handleSaveTitle = async (sessionId) => {
    try {
      await sessionAPI.updateSessionTitle(sessionId, currentUserId, editTitle);
      await loadSessions(); // Reload sessions
      setEditingSession(null);
    } catch (error) {
      console.error('Error updating session title:', error);
    }
  };

  const handleDeleteSession = async (sessionToDelete) => {
    if (window.confirm('Are you sure you want to delete this session? All files will be permanently removed.')) {
      try {
        await sessionAPI.deleteSession(sessionToDelete, currentUserId);
        await loadSessions(); // Reload sessions
        
        // If the current session was deleted, navigate to home
        if (sessionId === sessionToDelete) {
          navigate('/');
        }
      } catch (error) {
        console.error('Error deleting session:', error);
      }
    }
  };

  const formatDate = (dateString) => {
    if (!dateString) return '';
    try {
      const date = new Date(dateString);
      return date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric'
      });
    } catch {
      return '';
    }
  };

  if (!isVisible) return null;

  return (
    <div style={{
      position: 'fixed',
      top: '60px',
      left: '0',
      width: '280px',
      height: 'calc(100vh - 60px)',
      backgroundColor: '#171717',
      color: '#ffffff',
      borderRight: '1px solid #333',
      display: 'flex',
      flexDirection: 'column',
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
    }}>
      {/* Header */}
      <div style={{
        padding: '16px',
        borderBottom: '1px solid #333'
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
        padding: '8px'
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
          sessions.map((session) => (
            <div
              key={session.session_id}
              style={{
                marginBottom: '4px',
                borderRadius: '6px',
                backgroundColor: sessionId === session.session_id ? '#333' : 'transparent',
                border: sessionId === session.session_id ? '1px solid #555' : '1px solid transparent',
                transition: 'background-color 0.2s'
              }}
              onMouseEnter={(e) => {
                if (sessionId !== session.session_id) {
                  e.currentTarget.style.backgroundColor = '#1f1f1f';
                }
              }}
              onMouseLeave={(e) => {
                if (sessionId !== session.session_id) {
                  e.currentTarget.style.backgroundColor = 'transparent';
                }
              }}
            >
              <div
                onClick={() => handleSessionSelect(session)}
                style={{
                  padding: '12px',
                  cursor: 'pointer',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '4px'
                }}
              >
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
                    whiteSpace: 'nowrap'
                  }}>
                    {session.title}
                  </div>
                )}
                
                <div style={{
                  fontSize: '12px',
                  color: '#888',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center'
                }}>
                  <span>{formatDate(session.updated_at || session.created_at)}</span>
                  <div style={{
                    display: 'flex',
                    gap: '4px',
                    opacity: 0,
                    transition: 'opacity 0.2s'
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.opacity = 1;
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.opacity = 0;
                  }}>
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
                        padding: '2px',
                        fontSize: '12px'
                      }}
                      title="Edit title"
                    >
                      Edit
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDeleteSession(session.session_id);
                      }}
                      style={{
                        background: 'none',
                        border: 'none',
                        color: '#888',
                        cursor: 'pointer',
                        padding: '2px',
                        fontSize: '12px'
                      }}
                      title="Delete session"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Admin Section */}
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
        
        {/* Admin Submenu */}
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
                fontSize: '13px',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                borderRadius: '4px',
                transition: 'background-color 0.2s'
              }}
              onMouseEnter={(e) => {
                e.target.style.backgroundColor = '#1f1f1f';
                e.target.style.color = '#ffffff';
              }}
              onMouseLeave={(e) => {
                e.target.style.backgroundColor = 'transparent';
                e.target.style.color = '#cccccc';
              }}
            >
              <span>Knowledge Base</span>
            </button>
          </div>
        )}
      </div>

      {/* Footer */}
      <div style={{
        padding: '12px',
        borderTop: '1px solid #333',
        fontSize: '12px',
        color: '#888',
        textAlign: 'center'
      }}>
        {currentUserId?.slice(0, 8)}...
      </div>
    </div>
  );
};

export default SessionSidebar;