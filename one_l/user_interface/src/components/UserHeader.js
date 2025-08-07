import React, { useState } from 'react';

const UserHeader = ({ user, onLogout }) => {
  const [showDropdown, setShowDropdown] = useState(false);

  const getInitials = (name) => {
    if (!name) return 'U';
    return name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);
  };

  const getUserDisplayName = () => {
    if (user.name && user.name.trim()) return user.name;
    if (user.givenName || user.familyName) {
      return `${user.givenName || ''} ${user.familyName || ''}`.trim();
    }
    if (user.email) return user.email.split('@')[0];
    return user.username || 'User';
  };

  return (
    <div style={{
      position: 'fixed',
      top: '0',
      left: '0',
      right: '0',
      height: '60px',
      backgroundColor: '#ffffff',
      borderBottom: '1px solid #e5e5e5',
      zIndex: 1000,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'flex-end',
      paddingRight: '16px'
    }}>
      <div style={{ position: 'relative' }}>
        {/* User Avatar */}
        <button
          onClick={() => setShowDropdown(!showDropdown)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '8px 12px',
            backgroundColor: '#f8f9fa',
            border: '1px solid #dee2e6',
            borderRadius: '20px',
            cursor: 'pointer',
            fontSize: '14px',
            color: '#333',
            transition: 'background-color 0.2s'
          }}
          onMouseEnter={(e) => {
            e.target.style.backgroundColor = '#e9ecef';
          }}
          onMouseLeave={(e) => {
            e.target.style.backgroundColor = '#f8f9fa';
          }}
        >
          {/* Avatar Circle */}
          <div style={{
            width: '32px',
            height: '32px',
            borderRadius: '50%',
            backgroundColor: '#0066cc',
            color: 'white',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '12px',
            fontWeight: '600'
          }}>
            {getInitials(getUserDisplayName())}
          </div>
          
          {/* User Name */}
          <span style={{ 
            fontWeight: '500',
            maxWidth: '150px',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap'
          }}>
            {getUserDisplayName()}
          </span>
          
          {/* Dropdown Arrow */}
          <span style={{
            fontSize: '12px',
            transform: showDropdown ? 'rotate(180deg)' : 'rotate(0deg)',
            transition: 'transform 0.2s'
          }}>
            ▼
          </span>
        </button>

        {/* Dropdown Menu */}
        {showDropdown && (
          <div style={{
            position: 'absolute',
            top: '100%',
            right: '0',
            marginTop: '4px',
            backgroundColor: 'white',
            border: '1px solid #dee2e6',
            borderRadius: '8px',
            boxShadow: '0 4px 6px rgba(0, 0, 0, 0.1)',
            minWidth: '200px',
            zIndex: 1001
          }}>
            {/* User Info Section */}
            <div style={{
              padding: '12px 16px',
              borderBottom: '1px solid #f0f0f0'
            }}>
              <div style={{
                fontSize: '14px',
                fontWeight: '600',
                color: '#333',
                marginBottom: '4px'
              }}>
                {getUserDisplayName()}
              </div>
              <div style={{
                fontSize: '12px',
                color: '#666'
              }}>
                {user.email}
              </div>
              {user.emailVerified && (
                <div style={{
                  fontSize: '11px',
                  color: '#28a745',
                  marginTop: '2px'
                }}>
                  ✓ Email verified
                </div>
              )}
            </div>

            {/* Menu Items */}
            <div style={{ padding: '8px 0' }}>
              <button
                onClick={() => {
                  setShowDropdown(false);
                  onLogout();
                }}
                style={{
                  width: '100%',
                  padding: '8px 16px',
                  backgroundColor: 'transparent',
                  border: 'none',
                  textAlign: 'left',
                  fontSize: '14px',
                  color: '#dc3545',
                  cursor: 'pointer',
                  transition: 'background-color 0.2s'
                }}
                onMouseEnter={(e) => {
                  e.target.style.backgroundColor = '#f8f9fa';
                }}
                onMouseLeave={(e) => {
                  e.target.style.backgroundColor = 'transparent';
                }}
              >
                Sign Out
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Click outside to close dropdown */}
      {showDropdown && (
        <div
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            zIndex: 999
          }}
          onClick={() => setShowDropdown(false)}
        />
      )}
    </div>
  );
};

export default UserHeader;