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
  const [editingSession, setEditingSession] = useState(null);
  const [editTitle, setEditTitle] = useState('');
  const [adminExpanded, setAdminExpanded] = useState(false);

  // Load user sessions
  useEffect(() => {
    if (currentUserId && isVisible) {
      loadSessions();
    }
  }, [currentUserId, isVisible]); // eslint-disable-line react-hooks/exhaustive-deps

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
      // Only load sessions that have processed documents (results)
      const response = await sessionAPI.getUserSessions(currentUserId, true);
      if (response.success) {
        setSessions(response.sessions || []);
        console.log(`Loaded ${response.sessions?.length || 0} sessions with results`);
      }
    } catch (error) {
      console.error('Error loading sessions:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleNewSession = async () => {
    try {
      setLoading(true);
      const response = await sessionAPI.createSession(currentUserId);
      if (response.success) {
        // Navigate to the new session with new URL structure
        navigate(`/${response.session.session_id}`, { 
          state: { session: response.session } 
        });
      }
    } catch (error) {
      console.error('Error creating session:', error);
    } finally {
      setLoading(false);
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
          disabled={loading}
          style={{
            width: '100%',
            padding: '12px',
            backgroundColor: '#1f1f1f',
            color: '#ffffff',
            border: '1px solid #333',
            borderRadius: '6px',
            cursor: loading ? 'not-allowed' : 'pointer',
            fontSize: '14px',
            fontWeight: '500',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '8px',
            transition: 'background-color 0.2s'
          }}
          onMouseEnter={(e) => {
            if (!loading) e.target.style.backgroundColor = '#333';
          }}
          onMouseLeave={(e) => {
            if (!loading) e.target.style.backgroundColor = '#1f1f1f';
          }}
        >
          <span style={{ fontSize: '16px' }}>+</span>
          {loading ? 'Creating...' : 'New Session'}
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