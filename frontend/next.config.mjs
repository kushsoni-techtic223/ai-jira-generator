/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  ...(process.env.DESKTOP_BUILD === "1" ? { output: "standalone" } : {}),
};

export default nextConfig;

