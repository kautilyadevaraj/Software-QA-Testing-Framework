"use client";
import { useState } from "react";
import Link from "next/link";
import { ShieldCheck, ArrowRight, AlertCircle, CheckCircle2, Check } from "lucide-react";

export default function Signup() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  // Email Validation Logic (@xxx.com format)
  const isEmailValid = /^[^\s@]+@[^\s@]+\.[a-zA-Z]{2,}$/.test(email);

  // Password Logic
  const hasMinLength = password.length >= 8;
  const hasUpper = /[A-Z]/.test(password);
  const hasNumber = /[0-9]/.test(password);
  const hasSymbol = /[!@#$%^&*]/.test(password);
  const isPasswordValid = hasMinLength && hasUpper && hasNumber && hasSymbol;

  const validatePassword = (pass) => {
    if (pass.length < 8) return "Min 8 characters required";
    if (!/[A-Z]/.test(pass)) return "At least one uppercase letter required";
    if (!/[0-9]/.test(pass)) return "At least one number required";
    if (!/[!@#$%^&*]/.test(pass)) return "At least one special character required";
    return null;
  };

  const handleSignup = async () => {
    setError("");
    
    if (!isEmailValid) {
      setError("Please enter a valid email address (e.g. name@company.com)");
      return;
    }

    const passError = validatePassword(password);
    if (passError) {
      setError(passError);
      return;
    }

    const res = await fetch("/api/auth/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });

    const data = await res.json();

    if (res.ok) {
      window.location.href = "/upload";
    } else {
      setError(data.error);
    }
  };

  return (
    <div className="min-h-screen bg-white flex flex-col items-center justify-center px-6">
      {/* Logo Area */}
      <div className="mb-8 flex flex-col items-center text-center">
        <div className="h-12 w-12 bg-black rounded-xl flex items-center justify-center mb-4 shadow-xl shadow-slate-200">
          <ShieldCheck className="text-white w-7 h-7" />
        </div>
        <h1 className="text-2xl font-bold tracking-tight text-black">Create your account</h1>
        <p className="text-slate-700 text-sm mt-1 font-medium">Start building your QA testing framework today</p>
      </div>

      <div className="w-full max-w-[350px] space-y-4">
        {/* Error Message */}
        {error && (
          <div className="flex items-start space-x-2 bg-red-50 border border-red-100 p-3 rounded-lg animate-in fade-in zoom-in duration-200">
            <AlertCircle className="w-4 h-4 text-red-500 mt-0.5" />
            <p className="text-sm text-red-600 font-medium leading-tight">{error}</p>
          </div>
        )}

        <div className="space-y-2">
          <label className="text-xs font-bold uppercase tracking-wider text-slate-600 ml-1">Email Address</label>
          <div className="relative flex items-center">
            <input
              type="email"
              placeholder="name@company.com"
              onChange={(e) => setEmail(e.target.value)}
              className={`w-full border p-3 pr-10 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-100 transition-all duration-300 text-sm placeholder:text-slate-500 text-slate-900 ${
                isEmailValid ? "border-green-500 bg-green-50/30" : "border-slate-300 focus:border-blue-400"
              }`}
            />
            {isEmailValid && (
              <Check className="absolute right-3 w-4 h-4 text-green-600 animate-in fade-in zoom-in duration-300" />
            )}
          </div>
        </div>

        <div className="space-y-2">
          <label className="text-xs font-bold uppercase tracking-wider text-slate-600 ml-1">Password</label>
          <div className="relative flex items-center">
            <input
              type="password"
              placeholder="••••••••"
              onChange={(e) => setPassword(e.target.value)}
              className={`w-full border p-3 pr-10 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-100 transition-all duration-300 text-sm placeholder:text-slate-500 text-slate-900 ${
                isPasswordValid ? "border-green-500 bg-green-50/30" : "border-slate-300 focus:border-blue-400"
              }`}
            />
            {isPasswordValid && (
              <Check className="absolute right-3 w-4 h-4 text-green-600 animate-in fade-in zoom-in duration-300" />
            )}
          </div>
          {/* Password Hint */}
          <p className={`text-[10px] ml-1 flex items-center gap-1 font-bold transition-colors duration-300 ${
            isPasswordValid ? "text-green-600" : "text-slate-500"
          }`}>
            <CheckCircle2 className={`w-3 h-3 ${isPasswordValid ? "text-green-600" : "text-slate-400"}`} />
            Needs 8+ chars, uppercase, number, and special character
          </p>
        </div>

        <button
          onClick={handleSignup}
          className="w-full bg-slate-900 text-white font-bold py-3.5 rounded-xl hover:bg-slate-800 transition-all flex items-center justify-center space-x-2 group shadow-lg shadow-slate-200 cursor-pointer"
        >
          <span>Create Account</span>
          <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
        </button>

        <p className="text-center text-sm text-slate-700 pt-4 font-medium">
          Don&apos;t have an account?{" "}
          <Link href="/login" className="text-blue-700 font-bold hover:underline cursor-pointer">
            Login
          </Link>
        </p>
      </div>
    </div>
  );
}