import type { Metadata } from "next";
import { cookies } from "next/headers";
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

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const cookieStore = await cookies();
  const hasAuthToken = Boolean(cookieStore.get("access_token")?.value || cookieStore.get("token")?.value);

  return (
    <html lang="en" className={`${poppins.variable} h-full`}>
      <body className="h-full overflow-hidden bg-background text-foreground font-sans antialiased">
        <div className="flex h-dvh flex-col overflow-hidden">
          <AppNavbar isAuthenticated={hasAuthToken} />
          <main className="min-h-0 flex-1 overflow-hidden">{children}</main>
        </div>
        <Toaster position="top-right" richColors closeButton />
      </body>
    </html>
  );
}
