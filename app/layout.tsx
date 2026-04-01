import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "QA.core | Autonomous Testing Framework",
  description: "The ultimate framework for full-stack QA engineering and automated test suites.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full selection:bg-blue-100 selection:text-blue-900`}
    >
      <body 
        className="min-h-full bg-white text-slate-900 font-sans antialiased flex flex-col"
      >
        {children}
      </body>
    </html>
  );
}