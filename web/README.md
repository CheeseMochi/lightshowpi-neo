# LightShowPi Neo - Web UI

Modern React-based web interface for controlling LightShowPi.

## Features

- 🔐 **Secure Authentication** - JWT-based login
- 🎛️ **Lightshow Controls** - Start, stop, skip with real-time status
- 📅 **Schedule Management** - Create and manage automated schedules
- 📊 **Live Status** - Real-time updates every 2 seconds
- 🎨 **Clean UI** - Modern, responsive design
- 🌙 **Dark Theme** - Easy on the eyes

## Quick Start

### Development Mode

```bash
# Install dependencies
cd web
npm install

# Start development server (with API proxy)
npm run dev
```

The app will be available at `http://localhost:3000`

**Note:** Make sure the API backend is running on `http://localhost:8000` first!

### Production Build

```bash
# Build for production
npm run build

# Preview production build
npm run preview
```

The build output will be in `web/dist/` and can be served by any static file server.

## Project Structure

```
web/
├── src/
│   ├── components/           # React components
│   │   ├── Dashboard.jsx     # Main control panel
│   │   ├── Login.jsx         # Login page
│   │   └── ScheduleManager.jsx  # Schedule management
│   ├── services/
│   │   └── api.js            # API client
│   ├── styles/               # Component styles
│   ├── App.jsx               # Main app component
│   └── index.jsx             # Entry point
├── package.json
└── vite.config.js            # Vite configuration
```

## Default Credentials

**Username:** `admin`
**Password:** `admin123`

⚠️ **Change the password immediately after first login!**

## API Integration

The frontend communicates with the FastAPI backend via the `/api` proxy.

- **Dev mode:** Proxies `/api` → `http://localhost:8000/api`
- **Production:** Configure your web server to proxy `/api` to the backend

### Example nginx config:

```nginx
location /api {
    proxy_pass http://localhost:8000/api;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection 'upgrade';
    proxy_set_header Host $host;
    proxy_cache_bypass $http_upgrade;
}

location / {
    root /path/to/lightshowpi/web/dist;
    try_files $uri $uri/ /index.html;
}
```

## Components

### Dashboard
- Real-time lightshow status
- Start/Stop/Skip controls
- Upcoming scheduled events
- Quick navigation to schedules

### Schedule Manager
- Create, edit, delete schedules
- Enable/disable schedules with toggle
- Set start/stop times
- Select days of week
- Visual feedback for active schedules

## Development

### Adding New Features

1. Create component in `src/components/`
2. Add API methods to `src/services/api.js`
3. Create styles in `src/styles/`
4. Import and use in App or Dashboard

### Tech Stack

- **React 18** - UI framework
- **Vite** - Build tool and dev server
- **Vanilla CSS** - Styling (no framework needed)
- **Fetch API** - HTTP requests

## Troubleshooting

### API Connection Issues

If you see "Network error" or connection failures:

1. Check that the API backend is running (`cd api && python main.py`)
2. Verify the API is on port 8000
3. Check browser console for CORS errors

### Authentication Issues

If login fails:

1. Check credentials (default: admin/admin123)
2. Clear localStorage: `localStorage.clear()`
3. Check API logs for authentication errors

### Build Issues

```bash
# Clear node_modules and reinstall
rm -rf node_modules package-lock.json
npm install

# Clear vite cache
rm -rf node_modules/.vite
```

## Future Enhancements

Potential additions for future versions:

- WebSocket support for real-time updates
- Song upload interface
- Analytics dashboard
- Test mode for individual channels
- Multi-client management UI
- Theme customization
- Mobile-optimized layout

## License

BSD License - Same as LightShowPi
