"""
LightShowPi Neo - FastAPI Backend

Main application file for the optional FastAPI backend that provides
web UI, scheduling, analytics, and remote control capabilities.

This backend is OPTIONAL - LightShowPi can run without it using the
traditional CLI/cron/button control methods.
"""

import os
import sys
import time
import logging
from typing import List

from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

# Add py directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'py'))

from api.core.database import init_database
from api.core.config import APIConfig, get_api_config
from api.core.frontend import add_frontend_routes, index_response, resolve_dist_dir
from api.core.auth import (
    AuthManager, configure_secret_key, dev_mode, ensure_admin_user, get_current_user,
    password_problem,
)
from api.core.rate_limit import RateLimiter, client_ip
from api.models.schemas import (
    UserLogin, PasswordChange, Token, SystemHealth, LightshowState
)
from api.routers import lightshow, schedules, buttons, test_mode, public
from api.services.lightshow_manager import LightshowManager
from api.services.scheduler import SchedulerService
from api.services.button_manager import ButtonManagerService
from api.services.cloud_publisher import start_cloud_publisher
from api.services.test_mode_manager import TestModeManager, set_test_mode_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)

# Global instances
db = None
auth_manager = None
api_config = None
lightshow_manager = None
scheduler_service = None
button_manager = None
test_mode_manager = None
cloud_publisher = None

# API version
API_VERSION = "0.9.1"

# Failed logins (and wrong current passwords) allowed per client IP per minute
LOGIN_FAILURES_PER_MINUTE = 5
login_limiter = RateLimiter(max_events=LOGIN_FAILURES_PER_MINUTE, window_s=60.0)

# Vite dev server origins, allowed only with LSP_DEV=1
DEV_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifecycle manager for FastAPI app.

    Handles startup and shutdown tasks.
    """
    global db, auth_manager, api_config, lightshow_manager, scheduler_service, button_manager
    global test_mode_manager, cloud_publisher

    # Startup
    log.info("Starting LightShowPi Neo API...")
    app.state.start_time = time.time()

    # Refuse to start without a persistent JWT key (LSP_DEV=1 allows a random one)
    configure_secret_key()

    # Load configuration
    api_config = get_api_config()

    if not api_config.enabled:
        log.warning("API is not enabled in configuration. Set api.enabled=true to use the API.")
        log.warning("LightShowPi will continue to work in traditional mode.")

    # Initialize database
    db = init_database(api_config.db_path if api_config.db_path else None)
    auth_manager = AuthManager(db)

    # First run: create admin from $ADMIN_PASSWORD (there is no default password)
    ensure_admin_user(auth_manager, db)

    # Initialize lightshow manager
    lightshow_manager = LightshowManager(api_config)
    lightshow.set_manager(lightshow_manager)
    lightshow.set_database(db)
    public.set_config(api_config)

    # Initialize button manager from the [buttons] config section
    button_config = {
        'button_manager': {
            'enabled': api_config._get_bool('buttons', 'enabled', False),
            'audio_timeout': api_config._get_int('buttons', 'audio_auto_shutoff', 300),
            'cooldown': api_config._get_int('buttons', 'button_cooldown', 5)
        }
    }
    button_manager = ButtonManagerService(button_config, lightshow_manager)
    buttons.set_button_manager(button_manager)
    log.info("Button manager service initialized "
             f"(enabled={button_config['button_manager']['enabled']})")

    # Initialize test mode (exclusive with the lightshow)
    test_mode_manager = TestModeManager(
        timeout_minutes=api_config.test_mode_timeout_minutes,
        lightshow_manager=lightshow_manager,
    )
    set_test_mode_manager(test_mode_manager)
    lightshow_manager.set_test_mode_manager(test_mode_manager)
    log.info("Test mode manager initialized "
             f"(timeout={api_config.test_mode_timeout_minutes} min)")

    # Initialize and start scheduler
    scheduler_service = SchedulerService(db, lightshow_manager,
                                         test_mode_manager=test_mode_manager)
    schedules.set_scheduler(scheduler_service)
    scheduler_service.start()
    log.info("Scheduler service started")

    # Push the public show data to the cloud page ([api] cloud_url; blank = off)
    cloud_publisher = start_cloud_publisher(api_config, lambda: scheduler_service)

    log.info(f"LightShowPi Neo API {API_VERSION} started successfully")

    yield

    # Shutdown
    log.info("Shutting down LightShowPi Neo API...")

    if cloud_publisher:
        cloud_publisher.stop()
        cloud_publisher = None

    # Stop scheduler
    if scheduler_service:
        scheduler_service.stop()
        log.info("Scheduler service stopped")

    # Exit test mode (lights off, GPIO released) before stopping the show
    if test_mode_manager:
        test_mode_manager.shutdown()
        set_test_mode_manager(None)

    # Stop lightshow if running
    if lightshow_manager:
        lightshow_manager.stop(graceful=True)


# Create FastAPI app
app = FastAPI(
    title="LightShowPi Neo API",
    description="Modern API for controlling LightShowPi synchronized Christmas lights",
    version=API_VERSION,
    lifespan=lifespan
)

def load_cors_origins() -> List[str]:
    """
    Origins allowed to call the API from another site ([api] cors_origins).

    The web UI is served from the same origin as the API (or proxied to it by
    Vite), so it needs no CORS at all; the default is an empty list. LSP_DEV=1
    adds the Vite dev server origins. Auth is a bearer token, not a cookie, so
    credentials are never allowed cross-origin.
    """
    origins: List[str] = []
    try:
        origins = APIConfig().cors_origins
    except Exception as e:
        log.warning(f"Could not read [api] cors_origins, allowing none: {e}")
    if dev_mode():
        origins = origins + [o for o in DEV_ORIGINS if o not in origins]
    return origins


# CORS is read at import: Starlette builds the middleware stack before the
# lifespan runs, so it can't come from the config loaded at startup.
cors_origins = load_cors_origins()
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

# Include routers
app.include_router(lightshow.router)
app.include_router(schedules.router)
app.include_router(buttons.router)
app.include_router(test_mode.router)
app.include_router(public.router)


# Dependency to get database
def get_db():
    """Dependency for getting database instance."""
    global db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not initialized"
        )
    return db


# Dependency to get auth manager
def get_auth_manager(database = Depends(get_db)):
    """Dependency for getting auth manager."""
    return AuthManager(database)


# ============================================================================
# Authentication Endpoints
# ============================================================================

def _too_many_attempts(ip: str) -> HTTPException:
    retry = int(login_limiter.retry_after(ip)) + 1
    log.warning(f"Too many failed login attempts from {ip}; retry in {retry}s")
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many failed attempts. Try again later.",
        headers={"Retry-After": str(retry)},
    )


@app.post("/api/auth/login", response_model=Token, tags=["Authentication"])
async def login(
    credentials: UserLogin,
    request: Request,
    auth_mgr: AuthManager = Depends(get_auth_manager)
):
    """
    Authenticate and receive a JWT token.

    **Security:**
    - Passwords are hashed with bcrypt
    - JWT tokens expire after 24 hours
    - Optional IP whitelisting per user
    - 5 failed attempts per minute per client IP, then 429 until the window passes
    """
    ip = client_ip(request)
    if login_limiter.is_limited(ip):
        raise _too_many_attempts(ip)

    user = auth_mgr.authenticate_user(
        credentials.username,
        credentials.password,
        client_ip=ip,
    )

    if not user:
        login_limiter.record(ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Create access token
    access_token = auth_mgr.create_access_token(
        data={"sub": user['username'], "user_id": user['id']}
    )

    return {"access_token": access_token, "token_type": "bearer"}


@app.post("/api/auth/password", status_code=status.HTTP_204_NO_CONTENT,
          tags=["Authentication"])
async def change_password(
    body: PasswordChange,
    request: Request,
    current_user: dict = Depends(get_current_user),
    auth_mgr: AuthManager = Depends(get_auth_manager)
):
    """
    Change the logged-in user's password.

    Requires the current password. Existing tokens stay valid until they expire
    (24 hours). If the button manager logs in with this account, update
    /etc/lightshowpi/buttons.env too.
    """
    ip = client_ip(request)
    if login_limiter.is_limited(ip):
        raise _too_many_attempts(ip)

    username = current_user["username"]
    if not auth_mgr.authenticate_user(username, body.current_password, client_ip=ip):
        login_limiter.record(ip)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Current password is incorrect")

    problem = password_problem(body.new_password)
    if problem is None and body.new_password == body.current_password:
        problem = "New password must differ from the current one"
    if problem:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=problem)

    if not auth_mgr.set_password(username, body.new_password):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")


# ============================================================================
# Health Check Endpoint
# ============================================================================

def count_clients_online(database) -> int:
    """Number of registered clients whose status is 'online'."""
    if database is None:
        return 0
    try:
        with database.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM clients WHERE status = 'online'")
            return cursor.fetchone()[0]
    except Exception as e:
        log.warning(f"Could not count online clients: {e}")
        return 0


@app.get("/api/health", response_model=SystemHealth, tags=["System"])
async def health_check():
    """
    Get system health status.

    This endpoint does not require authentication and can be used
    for monitoring and health checks.
    """
    start_time = getattr(app.state, 'start_time', None) or time.time()

    lightshow_state = LightshowState.IDLE
    if lightshow_manager is not None:
        try:
            lightshow_state = LightshowState(lightshow_manager.get_status()["state"])
        except Exception as e:
            log.warning(f"Could not get lightshow state: {e}")
            lightshow_state = LightshowState.ERROR

    return {
        "api_version": API_VERSION,
        "uptime": time.time() - start_time,
        "lightshow_state": lightshow_state,
        "database_ok": db is not None,
        "clients_online": count_clients_online(db)
    }


# ============================================================================
# Root Endpoint
# ============================================================================

@app.get("/", tags=["Info"])
async def root():
    """
    The web UI when a build is served, otherwise API information.
    """
    if FRONTEND_INDEX is not None:
        return index_response(FRONTEND_INDEX)
    return {
        "name": "LightShowPi Neo API",
        "version": API_VERSION,
        "docs": "/docs",
        "health": "/api/health"
    }


def load_web_dist() -> str:
    """``[api] web_dist`` (blank means ``<repo>/web/dist``)."""
    try:
        return APIConfig().web_dist
    except Exception as e:
        log.warning(f"Could not read [api] web_dist, using the default: {e}")
        return ""


# Registered last: its catch-all route must not shadow any API route. Read at
# import for the same reason as CORS (routes exist before the lifespan runs).
FRONTEND_INDEX = add_frontend_routes(app, resolve_dist_dir(load_web_dist()))


if __name__ == "__main__":
    import uvicorn

    # Load config to get port
    config = get_api_config()

    if not config.enabled:
        print("ERROR: API is not enabled in configuration")
        print("Set api.enabled=true in config/overrides.cfg to use the API")
        sys.exit(1)

    print(f"Starting LightShowPi Neo API on {config.host}:{config.port}")
    print(f"Docs available at: http://{config.host}:{config.port}/docs")

    uvicorn.run(
        "api.main:app",
        host=config.host,
        port=config.port,
        reload=dev_mode(),  # auto-reload on code changes only with LSP_DEV=1
        log_level="info"
    )
