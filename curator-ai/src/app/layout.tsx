import type { Metadata } from "next";
import { Inter, Manrope } from "next/font/google";
import { AppChrome } from "@/components/AppChrome";
import { BackgroundScene } from "@/components/three/BackgroundScene";
import { ToastProvider } from "@/components/ToastProvider";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
});

const manrope = Manrope({
  subsets: ["latin"],
  variable: "--font-manrope",
});

export const metadata: Metadata = {
  title: "ListenFirst Social Intelligence",
  description:
    "ListenFirst social intelligence workspace for profile discovery, validation, and analysis.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`dark ${inter.variable} ${manrope.variable} h-full antialiased`}
    >
      <head>
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0&display=swap"
        />
      </head>
      <body className="min-h-full text-foreground">
        {/* Animated gradient + lazy Three.js scene, behind all page content. */}
        <div className="lf-aurora" aria-hidden />
        <BackgroundScene />
        <ToastProvider>
          <AppChrome>{children}</AppChrome>
        </ToastProvider>
      </body>
    </html>
  );
}
