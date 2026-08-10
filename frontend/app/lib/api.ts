/** Backend API base URL — set NEXT_PUBLIC_API_URL on Railway/Vercel for production. */
export const API =
  (typeof process !== "undefined" &&
    process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "")) ||
  "http://127.0.0.1:8000";
