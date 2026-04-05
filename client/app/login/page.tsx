"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowRight, Eye, EyeOff } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, login } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);

  useEffect(() => {
    const handlePageShow = (event: PageTransitionEvent) => {
      if (event.persisted) {
        window.location.reload();
      }
    };

    window.addEventListener("pageshow", handlePageShow);
    return () => window.removeEventListener("pageshow", handlePageShow);
  }, []);

  const handleLogin = async () => {
    if (!email || !password) {
      toast.error("Enter email and password.");
      return;
    }

    try {
      await login(email, password);
      router.replace("/projects");
      return;
    } catch (error) {
      if (error instanceof ApiError) {
        toast.error(error.message || "Unable to login right now.");
        return;
      }

      toast.error("Unable to login right now.");
    }
  };

  return (
    <div className="flex h-[calc(100vh-4rem)] items-center justify-center overflow-hidden px-4 py-4 sm:px-6">
      <Card className="w-full max-w-md bg-white">
        <CardHeader className="space-y-2">
          <CardTitle className="text-2xl text-black">Welcome Back</CardTitle>
          <CardDescription>Sign in to continue with SQAT.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              placeholder="name@company.com"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="password">Password</Label>
            <div className="relative">
              <Input
                id="password"
                type={showPassword ? "text" : "password"}
                placeholder="Enter your password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="pr-11"
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => setShowPassword((state) => !state)}
                className="absolute right-1 top-1/2 h-8 w-8 -translate-y-1/2"
                aria-label={showPassword ? "Hide password" : "Show password"}
              >
                {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </Button>
            </div>
          </div>

          <Button onClick={handleLogin} className="w-full">
            Sign In
            <ArrowRight className="h-4 w-4" />
          </Button>

          <p className="text-center text-sm text-black/70">
            Don&apos;t have an account?{" "}
            <Link href="/signup" className="text-[#2a63f5] hover:underline">
              Sign up
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
