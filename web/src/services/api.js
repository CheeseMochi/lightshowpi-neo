/**
 * API service for LightShowPi Neo
 *
 * Handles all communication with the FastAPI backend
 */

const API_BASE = '/api';

class ApiError extends Error {
  constructor(message, status, details) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.details = details;
  }
}

/**
 * Get auth token from localStorage
 */
function getAuthToken() {
  return localStorage.getItem('authToken');
}

/**
 * Set auth token in localStorage
 */
function setAuthToken(token) {
  localStorage.setItem('authToken', token);
}

/**
 * Clear auth token from localStorage
 */
function clearAuthToken() {
  localStorage.removeItem('authToken');
}

/**
 * Fired when the server rejects our token (expired, or the API's JWT key
 * changed). App.jsx listens and returns to the login screen.
 */
export const AUTH_EXPIRED_EVENT = 'lsp:auth-expired';

/**
 * Make authenticated API request
 */
async function apiRequest(endpoint, options = {}) {
  const token = getAuthToken();

  const headers = {
    'Content-Type': 'application/json',
    ...options.headers
  };

  if (token && !options.skipAuth) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const config = {
    ...options,
    headers
  };

  try {
    const response = await fetch(`${API_BASE}${endpoint}`, config);

    // Handle non-2xx responses
    if (response.status === 401 && !options.skipAuth) {
      clearAuthToken();
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
    }

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new ApiError(
        error.detail || `HTTP ${response.status}`,
        response.status,
        error
      );
    }

    // Handle 204 No Content
    if (response.status === 204) {
      return null;
    }

    return await response.json();
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    throw new ApiError('Network error', 0, { message: error.message });
  }
}

// =============================================================================
// Authentication API
// =============================================================================

export const auth = {
  async login(username, password) {
    const data = await apiRequest('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
      skipAuth: true
    });

    if (data.access_token) {
      setAuthToken(data.access_token);
    }

    return data;
  },

  logout() {
    clearAuthToken();
  },

  async changePassword(currentPassword, newPassword) {
    return apiRequest('/auth/password', {
      method: 'POST',
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword
      })
    });
  },

  isAuthenticated() {
    return !!getAuthToken();
  }
};

// =============================================================================
// Lightshow Control API
// =============================================================================

export const lightshow = {
  async getStatus() {
    return apiRequest('/lightshow/status');
  },

  async start(resumeSchedule = true, mode = null, playlist = null) {
    return apiRequest('/lightshow/start', {
      method: 'POST',
      body: JSON.stringify({
        resume_schedule: resumeSchedule,
        mode: mode,
        playlist: playlist
      })
    });
  },

  async stop(mode = 'pause') {
    return apiRequest('/lightshow/stop', {
      method: 'POST',
      body: JSON.stringify({ mode })
    });
  },

  async skip() {
    return apiRequest('/lightshow/skip', {
      method: 'POST'
    });
  }
};

// =============================================================================
// Schedule API
// =============================================================================

export const schedules = {
  async list(enabledOnly = false) {
    const params = enabledOnly ? '?enabled_only=true' : '';
    return apiRequest(`/schedules${params}`);
  },

  async get(id) {
    return apiRequest(`/schedules/${id}`);
  },

  async create(schedule) {
    return apiRequest('/schedules', {
      method: 'POST',
      body: JSON.stringify(schedule)
    });
  },

  async update(id, schedule) {
    return apiRequest(`/schedules/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(schedule)
    });
  },

  async delete(id) {
    return apiRequest(`/schedules/${id}`, {
      method: 'DELETE'
    });
  },

  async getUpcoming(limit = 10) {
    return apiRequest(`/schedules/upcoming/events?limit=${limit}`);
  }
};

// =============================================================================
// Button Manager API
// =============================================================================

export const buttons = {
  async getStatus() {
    return apiRequest('/buttons/status');
  },

  async getHealth() {
    return apiRequest('/buttons/health');
  },

  async skip() {
    return apiRequest('/buttons/skip', {
      method: 'POST'
    });
  },

  async repeatToggle() {
    return apiRequest('/buttons/repeat/toggle', {
      method: 'POST'
    });
  },

  async audioToggle() {
    return apiRequest('/buttons/audio/toggle', {
      method: 'POST'
    });
  }
};

// =============================================================================
// Config editor API (admin)
// =============================================================================

const cfgPath = (name) => `/config/files/${encodeURIComponent(name)}`;

export const configFiles = {
  async list() {
    return apiRequest('/config/files');
  },

  async get(name) {
    return apiRequest(cfgPath(name));
  },

  async save(name, content, baseSha256) {
    return apiRequest(cfgPath(name), {
      method: 'PUT',
      body: JSON.stringify({ content, base_sha256: baseSha256 })
    });
  },

  async validate(name, content, baseSha256) {
    return apiRequest(`${cfgPath(name)}/validate`, {
      method: 'POST',
      body: JSON.stringify({ content, base_sha256: baseSha256 })
    });
  },

  async backups(name) {
    return apiRequest(`${cfgPath(name)}/backups`);
  },

  async getBackup(name, backupId) {
    return apiRequest(`${cfgPath(name)}/backups/${encodeURIComponent(backupId)}`);
  },

  async restore(name, backupId) {
    return apiRequest(`${cfgPath(name)}/backups/${encodeURIComponent(backupId)}/restore`, {
      method: 'POST'
    });
  },

  async effective() {
    return apiRequest('/config/effective');
  }
};

// =============================================================================
// Devices API (admin)
// =============================================================================

export const devices = {
  async status() {
    return apiRequest('/devices/status');
  },

  async create(device) {
    return apiRequest('/devices', {
      method: 'POST',
      body: JSON.stringify(device)
    });
  },

  async update(id, changes) {
    return apiRequest(`/devices/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(changes)
    });
  },

  async delete(id) {
    return apiRequest(`/devices/${id}`, { method: 'DELETE' });
  },

  async sync() {
    return apiRequest('/devices/sync', { method: 'POST' });
  },

  async probe() {
    return apiRequest('/devices/probe', { method: 'POST' });
  }
};

// =============================================================================
// Test mode API (relay test buttons)
// =============================================================================

export const testMode = {
  async status() {
    return apiRequest('/test/status');
  },

  async enter() {
    return apiRequest('/test/enter', { method: 'POST' });
  },

  async exit() {
    return apiRequest('/test/exit', { method: 'POST' });
  },

  async channel(channel, state) {
    return apiRequest('/test/channel', {
      method: 'POST',
      body: JSON.stringify({ channel, state })
    });
  }
};

// =============================================================================
// System API
// =============================================================================

export const system = {
  async getHealth() {
    return apiRequest('/health', { skipAuth: true });
  }
};

export { ApiError };
