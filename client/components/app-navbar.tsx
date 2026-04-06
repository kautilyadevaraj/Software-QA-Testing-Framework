"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { logout } from "@/lib/api";

export function AppNavbar() {
  const pathname = usePathname();
  const router = useRouter();

  const isWorkspacePage = pathname.startsWith("/upload") || pathname.startsWith("/projects");

  const handleLogout = async () => {
    await logout();
    router.replace("/login");
  };

  return (
    <nav className="sticky top-0 z-50 border-b border-black/10 bg-[#2a63f5]">
      <div className="mx-auto flex h-16 w-full max-w-6xl items-center justify-between px-4 sm:px-6">
        <Link href="/" className="group inline-flex items-center gap-2">
          <span className="text-xl font-semibold tracking-wide text-white">SQAT</span>
        </Link>

        <div className="flex items-center gap-2">
          {isWorkspacePage ? (
            <Button
              variant="outline"
              onClick={handleLogout}
              className="border-red-400 bg-red-50 text-red-700 hover:border-red-500 hover:bg-red-100 hover:text-red-800"
            >
              <LogOut className="h-4 w-4" />
              Logout
            </Button>
          ) : pathname === "/login" ? (
            <Button asChild variant="outline" className="border-white/45 bg-transparent text-white hover:bg-white/15">
              <Link href="/signup">Sign up</Link>
            </Button>
          ) : pathname === "/signup" ? (
            <Button asChild variant="outline" className="border-white/45 bg-transparent text-white hover:bg-white/15">
              <Link href="/login">Log in</Link>
            </Button>
          ) : (
            <>
              <Button asChild variant="ghost" className="text-white hover:bg-white/15 hover:text-white">
                <Link href="/login">Log in</Link>
              </Button>
              <Button asChild className="bg-black text-white hover:bg-black/90">
                <Link href="/signup">Get Started</Link>
              </Button>
            </>
          )}
        </div>
      </div>
    </nav>
  );
}
