import { useState, useEffect } from 'react';
import { Routes, Route } from 'react-router-dom';
import Login from './Login';
import Dashboard from './Dashboard';
import { auth, AUTH_EXPIRED_EVENT } from '../services/api';

/**
 * The admin shell, mounted at /admin/*.
 *
 * It owns the login state: signed out shows Login, signed in shows the admin
 * routes. Add admin pages as <Route>s below (paths are relative to /admin).
 */
export default function AdminApp() {
  const [authenticated, setAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Check if user is already authenticated
    setAuthenticated(auth.isAuthenticated());
    setLoading(false);

    // Any 401 from the API (expired or revoked token) returns to the login screen
    const onExpired = () => setAuthenticated(false);
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, []);

  const handleLogin = () => {
    setAuthenticated(true);
  };

  const handleLogout = () => {
    auth.logout();
    setAuthenticated(false);
  };

  if (loading) {
    return <div className="app-loading">Loading...</div>;
  }

  return (
    <div className="app">
      {authenticated ? (
        <Routes>
          <Route path="*" element={<Dashboard onLogout={handleLogout} />} />
        </Routes>
      ) : (
        <Login onLogin={handleLogin} />
      )}
    </div>
  );
}
