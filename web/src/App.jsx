import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Home from './components/Home';
import AdminApp from './components/AdminApp';
import { ADMIN_URL } from './config';
import './App.css';

/**
 * True when the admin UI lives on another host (the cloud build sets
 * VITE_ADMIN_URL=https://<admin-host>/admin). Then /admin here only redirects.
 */
function adminIsElsewhere() {
  try {
    return new URL(ADMIN_URL, window.location.href).origin !== window.location.origin;
  } catch {
    return false;
  }
}

function AdminRoute() {
  if (adminIsElsewhere()) {
    window.location.replace(ADMIN_URL);
    return <div className="app-loading">Redirecting...</div>;
  }
  return <AdminApp />;
}

/**
 * Routes: / is the public show page (no login), /admin/* the admin UI.
 * The API's SPA fallback (api/core/frontend.py) serves index.html for both.
 */
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/public" element={<Home />} />
        <Route path="/admin/*" element={<AdminRoute />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
