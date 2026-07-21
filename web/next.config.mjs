/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Standalone output makes the Docker image small (only the server + traced deps).
  output: "standalone",
  eslint: {
    // Lint is run explicitly via `pnpm lint` / CI, not implicitly during build.
    ignoreDuringBuilds: true,
  },
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080",
  },
};

export default nextConfig;
