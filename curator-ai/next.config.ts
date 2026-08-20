import type { NextConfig } from "next";

/**
 * Static export.
 *
 * This app is a single-page client that talks to the Python API in the browser
 * — there is no server-side data fetching left, so there is nothing for a
 * Node runtime to do. Exporting to plain files means:
 *
 *   • AWS Amplify can host it. Amplify Hosting's compute service supports
 *     Next.js 12–15; this app is on 16, so the SSR path fails the build with
 *     "Failed to find the deploy-manifest.json file". Static hosting has no
 *     framework-version constraint at all.
 *   • It is served straight from CloudFront — no Lambda, no cold starts.
 *
 * If server-side rendering is ever genuinely needed, this is the line to
 * remove — but note that every page currently authenticates with a bearer
 * token held in localStorage, which a server cannot read.
 */
const nextConfig: NextConfig = {
  output: "export",

  // Next's default image loader needs a server to resize on request. Static
  // export has none, so images are emitted as-is.
  images: {
    unoptimized: true,
    remotePatterns: [
      {
        protocol: "https",
        hostname: "lh3.googleusercontent.com",
        pathname: "/aida-public/**",
      },
    ],
  },

  // Emit /results/index.html rather than /results.html. S3 + CloudFront resolve
  // a directory to its index document, so links keep working without any
  // rewrite rules on the hosting side.
  trailingSlash: true,
};

export default nextConfig;
