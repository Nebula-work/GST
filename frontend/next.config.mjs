/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Static HTML export -> `out/`. nginx serves it directly (no Node in prod).
  output: "export",
};

export default nextConfig;
