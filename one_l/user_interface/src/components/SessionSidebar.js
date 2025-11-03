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
  useEffect(() => {
    if (currentUserId && isVisible && sessionId) {
      loadSessions();
    }
  }, [sessionId]); // eslint-disable-line react-hooks/exhaustive-deps

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
      if (response.body) {
        try {
          responseData = typeof response.body === 'string' ? JSON.parse(response.body) : response.body;
        } catch (e) {
          console.error('Error parsing response body:', e);
          responseData = response;
        }
      }
      
      // Handle HTTP errors (like 500) that might be in the response
      if (response.statusCode && response.statusCode >= 400) {
        console.error('HTTP error loading sessions:', response.statusCode, responseData);
        // Don't clear existing sessions on error - keep what we have
        return;
      }
      
      if (responseData.success) {
        setSessions(responseData.sessions || []);
      } else {
        console.error('Failed to load sessions:', responseData.error || 'Unknown error');
        // Don't clear existing sessions on error - keep what we have
      }
    } catch (error) {
      // Handle 500 errors and other network errors gracefully
      if (error.message && error.message.includes('500')) {
        console.warn('Server error loading sessions (500). Sessions may be temporarily unavailable.');
        // Don't clear existing sessions - keep what we have displayed
      } else {
        console.error('Error loading sessions:', error);
        console.error('Error details:', {
          message: error.message,
          stack: error.stack,
          name: error.name
        });
      }
      // Don't clear existing sessions on error - keep what we have
    } finally {
      setLoading(false);
    }
  };

  const handleNewSession = async () => {
    try {
      // Check if there's active processing in the current session
      const hasActiveProcessing = window.progressInterval !== null && window.progressInterval !== undefined;
      
      if (hasActiveProcessing) {
        const confirmed = window.confirm(
          'You have an active document processing job. Creating a new session will navigate away and pause the progress indicator (processing will continue in background).\n\n' +
          'Do you want to continue?'
        );
        if (!confirmed) {
          return;
        }
      }
      
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
            await loadSessions();
          } catch (error) {
            // Silently fail - we already have the session in the list
            console.warn('Failed to refresh sessions list after creation:', error);
          }
        }, 500);
        
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