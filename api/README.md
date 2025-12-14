# LightShowPi Neo - FastAPI Backend

Modern REST API backend for LightShowPi Neo, providing web UI, scheduling, analytics, and remote control capabilities.

## ⚠️ Important: This is OPTIONAL

The FastAPI backend is **completely optional**. LightShowPi Neo works perfectly fine without it using traditional CLI, cron, and button control methods. Enable the API only if you want:

- Web-based control and monitoring
- Advanced scheduling
- Analytics tracking
- Multi-client management
- Cloud-based access (optional)

## 🎯 Current Status

### ✅ Phase 1: Core Infrastructure (COMPLETED)

**Database Layer:**
- SQLite schema with 8 tables
- User authentication with bcrypt
- Client registration system
- Schedule management
- Playlist tracking
- Analytics (song plays, skips)
- System events logging

**Authentication:**
- Secure password hashing with bcrypt
- JWT token-based auth
- Optional IP whitelisting per user
- 24-hour token expiration

**Configuration:**
- Backward-compatible config extension
- New `[api]` section in defaults.cfg
- `enabled` flag for easy toggle
- Cloud mode support (optional)

**FastAPI App:**
- Main app structure
- CORS middleware
- Lifecycle management
- Health check endpoint
- Login endpoint
- Auto-generated API docs

### 🚧 Phase 2: Core Functionality (IN PROGRESS)

**Subprocess Manager:**
- Manage synchronized_lights.py as subprocess
- Start/stop/restart control
- Output monitoring
- State synchronization

**Core API Endpoints:**
- `POST /api/lightshow/start` - Start the show
- `POST /api/lightshow/stop` - Stop with pause/stop modes
- `POST /api/lightshow/skip` - Skip to next song
- `GET /api/lightshow/status` - Get current status
- `WS /api/lightshow/websocket` - Real-time updates

**Scheduling System:**
- APScheduler integration
- Configurable start/stop times
- Days of week support
- Auto-start on reboot logic
- Schedule override modes (pause vs stop)

### 📅 Phase 3: Multi-Client Support (PLANNED)

**Client Management:**
- Client registration API
- Client key generation
- Client status tracking
- Individual client control
- Test mode per client

**Endpoints:**
- `GET /api/clients` - List all clients
- `POST /api/clients` - Register new client
- `GET /api/clients/{id}/status` - Client status
- `POST /api/clients/{id}/test/channel/{n}` - Test mode

### 📅 Phase 4: Playlist & Song Management (PLANNED)

**Playlist Management:**
- CRUD operations for playlists
- Activate/deactivate playlists
- Song metadata tracking
- File-based playlist sync

**Song Upload:**
- Multi-part file upload
- 100MB file size limit
- Format validation (MP3, FLAC, WAV, etc.)
- Optional FFT pre-generation
- Disk space warnings

**Endpoints:**
- `GET /api/playlists` - List playlists
- `POST /api/playlists` - Create playlist
- `POST /api/songs/upload` - Upload song
- `DELETE /api/songs/{id}` - Delete song

### 📅 Phase 5: Analytics & Monitoring (PLANNED)

**Analytics Tracking:**
- Song play counts
- Skip statistics
- Completion rates
- Time-based patterns
- Per-client analytics

**Dashboard:**
- Most played songs
- Most skipped songs
- Highest completion rates
- System uptime
- Error frequency

**Endpoints:**
- `GET /api/analytics/plays` - Play statistics
- `GET /api/analytics/skips` - Skip statistics
- `GET /api/analytics/popular` - Popular songs

### 📅 Phase 6: Web UI (PLANNED)

**Frontend:**
- React-based web UI
- Real-time status updates via WebSocket
- Admin authentication
- Lightshow controls
- Schedule management
- Playlist editor
- Song upload interface
- Test mode controls
- Analytics dashboard
- System logs viewer

### 📅 Phase 7: Cloud Mode (PLANNED)

**Cloud Relay:**
- WebSocket client on Pi
- Connects to external web app
- Bi-directional command/status
- Auto-reconnect logic
- Offline mode support

## 📁 Directory Structure

```
api/
├── README.md                 # This file
├── main.py                   # FastAPI application
├── __init__.py
├── core/                     # Core infrastructure
│   ├── __init__.py
│   ├── database.py           # SQLite database manager
│   ├── config.py             # Configuration loader
│   └── auth.py               # Authentication & JWT
├── models/                   # Pydantic models
│   ├── __init__.py
│   └── schemas.py            # Request/response models
├── routers/                  # API route handlers
│   ├── __init__.py
│   ├── lightshow.py          # (TODO) Lightshow control
│   ├── schedule.py           # (TODO) Schedule management
│   ├── clients.py            # (TODO) Client management
│   ├── playlists.py          # (TODO) Playlist CRUD
│   ├── songs.py              # (TODO) Song upload/management
│   └── analytics.py          # (TODO) Analytics endpoints
└── services/                 # Business logic
    ├── __init__.py
    ├── subprocess_manager.py # (TODO) Process management
    ├── scheduler.py          # (TODO) APScheduler wrapper
    └── analytics_tracker.py  # (TODO) Analytics collection
```

## 🚀 Getting Started

### Prerequisites

```bash
# Install dependencies
pip install -r requirements.txt
```

### Configuration

1. **Enable API mode** in `config/overrides.cfg`:

```ini
[api]
enabled = True
host = 0.0.0.0
port = 8000
```

2. **Set admin password** (optional):

```bash
export ADMIN_PASSWORD="your_secure_password_here"
```

3. **Initialize database**:

```bash
python -m api.core.database
```

### Running the API

**Development mode:**

```bash
python -m api.main
```

**Production mode** (with systemd - coming soon):

```bash
sudo systemctl start lightshowpi-api
```

### Access the API

- **API Docs:** http://localhost:8000/docs
- **Health Check:** http://localhost:8000/api/health
- **Login:** POST to http://localhost:8000/api/auth/login

## 📖 API Documentation

### Authentication

All authenticated endpoints require a JWT token in the Authorization header:

```bash
# Login
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "your_password"}'

# Response
{
  "access_token": "eyJ0eXAiOiJKV1...",
  "token_type": "bearer"
}

# Use token in subsequent requests
curl -X GET http://localhost:8000/api/lightshow/status \
  -H "Authorization: Bearer eyJ0eXAiOiJKV1..."
```

### Health Check

```bash
GET /api/health

# Response
{
  "api_version": "1.0.0",
  "uptime": 3600.5,
  "lightshow_state": "idle",
  "database_ok": true,
  "clients_online": 2
}
```

## 🔒 Security

### Password Hashing
- Bcrypt with auto-generated salt
- Minimum 8 characters
- Stored as hash (never plain text)

### JWT Tokens
- HS256 algorithm
- 24-hour expiration
- Includes user ID and username

### IP Whitelisting (Optional)
- Per-user IP whitelist
- Configured in database
- Checked on authentication

### Recommendations
1. Change default admin password immediately
2. Use HTTPS in production (reverse proxy)
3. Enable IP whitelisting for admin users
4. Rotate JWT secret key periodically
5. Use strong passwords (12+ characters)

## 🧪 Testing

```bash
# Run all tests
pytest api/

# Run with coverage
pytest --cov=api --cov-report=html api/

# Test specific module
pytest api/core/test_auth.py
```

## 📝 Configuration Reference

### [api] Section

```ini
[api]
# Enable/disable API backend
enabled = False

# Server configuration
host = 0.0.0.0      # 0.0.0.0 = all interfaces, 127.0.0.1 = localhost only
port = 8000         # API port

# Database
db_path =           # Leave blank for default (data/lightshowpi.db)

# Cloud mode (optional)
cloud_url =         # External web app URL
cloud_key =         # Authentication key
```

### [client] Section (for Client Pis)

```ini
[client]
# API-based client registration (when API is enabled)
client_id =         # Generated by server web UI
client_key =        # Generated by server web UI
```

## 🛠️ Development

### Adding New Endpoints

1. Create router in `api/routers/`:

```python
from fastapi import APIRouter, Depends
from api.core.auth import require_admin

router = APIRouter(prefix="/api/feature", tags=["Feature"])

@router.get("/")
async def list_items(user = Depends(require_admin)):
    return {"items": []}
```

2. Include router in `api/main.py`:

```python
from api.routers import feature

app.include_router(feature.router)
```

### Database Migrations

For schema changes, update `api/core/database.py` and run:

```bash
python -m api.core.database
```

## 🐛 Troubleshooting

### API won't start

**Check configuration:**
```bash
python -c "from api.core.config import get_api_config; c = get_api_config(); print(f'Enabled: {c.enabled}, Port: {c.port}')"
```

**Check if port is in use:**
```bash
lsof -i :8000
```

### Database errors

**Reset database:**
```bash
rm data/lightshowpi.db
python -m api.core.database
```

### Authentication fails

**Reset admin password:**
```bash
# TODO: Create password reset script
```

## 📚 Resources

- **FastAPI Docs:** https://fastapi.tiangolo.com/
- **Pydantic Docs:** https://docs.pydantic.dev/
- **APScheduler Docs:** https://apscheduler.readthedocs.io/
- **JWT.io:** https://jwt.io/

## 🤝 Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md) for guidelines on contributing to LightShowPi Neo.

## 📄 License

BSD 2-Clause License - see [LICENSE](../LICENSE)

---

**Questions?** Open a [discussion](https://github.com/[yourusername]/lightshowpi-neo/discussions) or contact us at [youremail@example.com](mailto:youremail@example.com).
