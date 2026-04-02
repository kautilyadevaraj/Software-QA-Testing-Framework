import type { Metadata } from "next";
import { Poppins } from "next/font/google";
import { Toaster } from "sonner";
import { AppNavbar } from "@/components/app-navbar";
import "./globals.css";

const poppins = Poppins({
  variable: "--font-poppins",
  weight: ["500", "600", "700"],
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "SQAT | Software QA Testing Framework",
  description: "Software QA automation platform for full-stack testing workflows.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${poppins.variable} h-full`}>
      <body className="min-h-full bg-background text-foreground font-sans antialiased">
        <div className="flex min-h-screen flex-col">
          <AppNavbar />
          <main className="flex-1">{children}</main>
        </div>
        <Toaster position="top-right" richColors closeButton />
      </body>
    </html>
  );
}