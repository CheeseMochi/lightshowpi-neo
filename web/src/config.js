/**
 * Build-time settings (Vite env vars, set when running `npm run build`).
 *
 * VITE_PUBLIC_API_BASE  where the Home page reads show data (default /api/public)
 * VITE_ADMIN_URL        where the gear icon goes (default /admin)
 * VITE_SHOW_NAME        the title on the Home page
 */
export const ADMIN_URL = import.meta.env.VITE_ADMIN_URL || '/admin';
export const SHOW_NAME = import.meta.env.VITE_SHOW_NAME || 'Holiday Light Show';
