"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AlertCircle, ArrowRight, Check, Eye, EyeOff } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, signup } from "@/lib/api";

function getPasswordError(password: string) {
  if (password.length < 8 || password.length > 18) {
    return "Password must be 8 to 18 characters long.";
  }
  if (!/[A-Z]/.test(password)) {
    return "Password must include at least one uppercase character.";
  }
  if (!/[a-z]/.test(password)) {
    return "Password must include at least one lowercase character.";
  }
  if (!/[0-9]/.test(password)) {
    return "Password must include at least one number.";
  }
  if (!/[^A-Za-z0-9]/.test(password)) {
    return "Password must include at least one special character.";
  }
  return null;
}

export default function SignupPage() {
  const router = useRouter();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [error, setError] = useState("");

  const isEmailValid = /^[^\s@]+@[^\s@]+\.[a-zA-Z]{2,}$/.test(email);
  const hasLength = password.length >= 8 && password.length <= 18;
  const hasUpper = /[A-Z]/.test(password);
  const hasLower = /[a-z]/.test(password);
  const hasNumber = /[0-9]/.test(password);
  const hasSpecial = /[^A-Za-z0-9]/.test(password);
  const passwordsMatch = password.length > 0 && password === confirmPassword;

  const passwordChecks = [
    { label: "8-18", valid: hasLength },
    { label: "Upper", valid: hasUpper },
    { label: "Lower", valid: hasLower },
    { label: "Number", valid: hasNumber },
    { label: "Special", valid: hasSpecial },
  ];

  const handleSignup = async () => {
    setError("");

    if (!isEmailValid) {
      setError("Please enter a valid email address.");
      return;
    }

    const passwordError = getPasswordError(password);
    if (passwordError) {
      setError(passwordError);
      return;
    }

    if (!passwordsMatch) {
      setError("Password and Confirm Password must match.");
      return;
    }

    try {
      await signup(email, password);
      router.replace("/projects");
      return;
    } catch (error) {
      if (error instanceof ApiError) {
        setError(error.message || "Signup failed.");
        return;
      }

      setError("Signup failed.");
    }
  };

  return (
    <div className="flex h-[calc(100vh-4rem)] items-center justify-center overflow-hidden px-4 py-3 sm:px-6">
      <Card className="w-full max-w-lg bg-white">
        <CardHeader className="space-y-2">
          <CardTitle className="text-2xl text-black">Create your SQAT account</CardTitle>
          <CardDescription>Set up your workspace with a secure password.</CardDescription>
        </CardHeader>

        <CardContent className="space-y-4">
          {error ? (
            <Alert>
              <AlertTitle className="flex items-center gap-2">
                <AlertCircle className="h-4 w-4" />
                Signup failed
              </AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}

          <div className="space-y-2">
            <Label htmlFor="signup-email">Email</Label>
            <Input
              id="signup-email"
              type="email"
              placeholder="name@company.com"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="signup-password">Password</Label>
            <div className="relative">
              <Input
                id="signup-password"
                type={showPassword ? "text" : "password"}
                placeholder="Create password"
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

          <div className="space-y-2">
            <Label htmlFor="signup-confirm-password">Confirm Password</Label>
            <div className="relative">
              <Input
                id="signup-confirm-password"
                type={showConfirmPassword ? "text" : "password"}
                placeholder="Confirm password"
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                className="pr-11"
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => setShowConfirmPassword((state) => !state)}
                className="absolute right-1 top-1/2 h-8 w-8 -translate-y-1/2"
                aria-label={showConfirmPassword ? "Hide confirm password" : "Show confirm password"}
              >
                {showConfirmPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </Button>
            </div>
          </div>

          <div className="rounded-lg border border-black/15 bg-white p-3">
            <p className="mb-2 text-xs uppercase tracking-wider text-black/70">Constraints</p>
            <div className="flex flex-wrap gap-2">
              {passwordChecks.map((rule) => (
                <span
                  key={rule.label}
                  className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs ${
                    rule.valid
                      ? "border-[#2a63f5]/35 bg-[#2a63f5]/10 text-[#2a63f5]"
                      : "border-black/20 bg-white text-black/70"
                  }`}
                >
                  <Check className="h-3 w-3" />
                  {rule.label}
                </span>
              ))}
              <span
                className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs ${
                  passwordsMatch
                    ? "border-[#2a63f5]/35 bg-[#2a63f5]/10 text-[#2a63f5]"
                    : "border-black/20 bg-white text-black/70"
                }`}
              >
                <Check className="h-3 w-3" />
                Match
              </span>
            </div>
          </div>

          <Button onClick={handleSignup} className="w-full">
            Create Account
            <ArrowRight className="h-4 w-4" />
          </Button>

          <p className="text-center text-sm text-black/70">
            Already have an account?{" "}
            <Link href="/login" className="text-[#2a63f5] hover:underline">
              Log in
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
