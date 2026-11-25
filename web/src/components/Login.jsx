import { useEffect, useRef, useState } from 'react';
import { auth } from '../services/api';
import '../styles/Login.css';

const TURNSTILE_SRC = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';

let turnstilePromise = null;

/** Load Cloudflare's Turnstile script once; resolves with window.turnstile. */
function loadTurnstile() {
  if (window.turnstile) {
    return Promise.resolve(window.turnstile);
  }
  if (!turnstilePromise) {
    turnstilePromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = TURNSTILE_SRC;
      script.async = true;
      script.onload = () => (window.turnstile
        ? resolve(window.turnstile)
        : reject(new Error('Turnstile did not initialise')));
      script.onerror = () => reject(new Error('Turnstile script failed to load'));
      document.head.appendChild(script);
    }).catch((err) => {
      turnstilePromise = null; // allow a retry on the next mount
      throw err;
    });
  }
  return turnstilePromise;
}

export default function Login({ onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  // Turnstile (CAPTCHA): only shown for logins through the Cloudflare tunnel
  const [captcha, setCaptcha] = useState({ required: false, siteKey: null });
  const [captchaToken, setCaptchaToken] = useState('');
  const [captchaError, setCaptchaError] = useState('');
  const widgetEl = useRef(null);
  const widgetId = useRef(null);

  useEffect(() => {
    let cancelled = false;
    auth.getConfig()
      .then((cfg) => {
        if (!cancelled && cfg && cfg.required && cfg.turnstile_site_key) {
          setCaptcha({ required: true, siteKey: cfg.turnstile_site_key });
        }
      })
      // If this fails, show the plain form; the server still enforces the CAPTCHA
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!captcha.required) return undefined;
    let cancelled = false;
    loadTurnstile()
      .then((turnstile) => {
        if (cancelled || !widgetEl.current || widgetId.current !== null) return;
        widgetId.current = turnstile.render(widgetEl.current, {
          sitekey: captcha.siteKey,
          theme: 'dark',
          callback: (token) => { setCaptchaToken(token); setCaptchaError(''); },
          'expired-callback': () => setCaptchaToken(''),
          'error-callback': () => {
            setCaptchaToken('');
            setCaptchaError('The CAPTCHA hit an error. Reload the page to try again.');
          },
        });
      })
      .catch(() => {
        if (!cancelled) {
          setCaptchaError('Could not load the CAPTCHA from Cloudflare. Check your connection '
            + 'or disable content blockers for this page, then reload.');
        }
      });
    return () => {
      cancelled = true;
      if (widgetId.current !== null && window.turnstile) {
        window.turnstile.remove(widgetId.current);
      }
      widgetId.current = null;
    };
  }, [captcha.required, captcha.siteKey]);

  const resetCaptcha = () => {
    // Turnstile tokens are single use: get a fresh one after any failed attempt
    setCaptchaToken('');
    if (widgetId.current !== null && window.turnstile) {
      window.turnstile.reset(widgetId.current);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      await auth.login(username, password, captcha.required ? captchaToken : null);
      onLogin();
    } catch (err) {
      setError(err.message || 'Login failed');
      if (captcha.required) resetCaptcha();
    } finally {
      setLoading(false);
    }
  };

  const waitingForCaptcha = captcha.required && !captchaToken;

  return (
    <div className="login-container">
      <div className="login-box">
        <div className="login-header">
          <h1>🎄 LightShowPi Neo</h1>
          <p>Control Your Christmas Lights</p>
        </div>

        <form onSubmit={handleSubmit} className="login-form">
          {error && (
            <div className="error-message">
              {error}
            </div>
          )}

          <div className="form-group">
            <label htmlFor="username">Username</label>
            <input
              id="username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Enter username"
              required
              autoFocus
            />
          </div>

          <div className="form-group">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter password"
              required
            />
          </div>

          {captcha.required && (
            <div className="login-captcha">
              <div ref={widgetEl} />
              {captchaError && (
                <div className="error-message">{captchaError}</div>
              )}
            </div>
          )}

          <button
            type="submit"
            className="btn btn-primary"
            disabled={loading || waitingForCaptcha}
            title={waitingForCaptcha ? 'Complete the CAPTCHA first' : undefined}
          >
            {loading ? 'Logging in...' : 'Log In'}
          </button>
        </form>

      </div>
    </div>
  );
}
