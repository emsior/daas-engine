import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  images: { unoptimized: true },
  // treść DPA jest czytana z pliku w runtime: wymuś dołączenie do paczki standalone
  outputFileTracingIncludes: { "/dpa": ["./src/content/**/*"] },
};

export default nextConfig;
