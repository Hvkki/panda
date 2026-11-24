export const APP_NAME = "Panda";
// Server functions live next to the app on Vercel (app/api). Override for local development.
export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";
