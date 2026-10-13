/** @type {import('next').NextConfig} */
const isDev = process.env.NODE_ENV === "development";
const backend = process.env.BACKEND_URL ?? "http://localhost:8000";

// Production builds are a static export served by FastAPI on the same origin.
// In `next dev`, /api/* is proxied to the backend instead.
const nextConfig = isDev
  ? {
      reactStrictMode: true,
      async rewrites() {
        return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
      },
    }
  : {
      reactStrictMode: true,
      output: "export",
      images: { unoptimized: true },
    };

export default nextConfig;
