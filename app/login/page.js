"use client";
import { useState, useEffect } from "react";
import Link from "next/link";
import { ShieldCheck, ArrowRight, AlertCircle } from "lucide-react";

export default function Login() {
  useEffect(() => {
    const handlePageShow = (event) => {
      if (event.persisted) {
        window.location.reload();
      }
    };

    window.addEventListener("pageshow", handlePageShow);

    return () => {
      window.removeEventListener("pageshow", handlePageShow);
    };
  }, []);



  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [showSignupLink, setShowSignupLink] = useState(false);

  const handleLogin = async () => {
    setError("");
    setShowSignupLink(false);

    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });

    const data = await res.json();

    if (res.ok) {
      window.location.href = "/upload";
    } else {
      setError(data.error);
      if (data.code === "USER_MISSING") {
        setShowSignupLink(true);
      }
    }
  };

  return (
    <div className="min-h-screen bg-white flex flex-col items-center justify-center px-6">
      {/* Logo Area */}
      <div className="mb-8 flex flex-col items-center">
        <div className="h-12 w-12 bg-black rounded-xl flex items-center justify-center mb-4 shadow-xl shadow-slate-200">
          <ShieldCheck className="text-white w-7 h-7" />
        </div>
        {/* Darkened text to black for better visibility */}
        <h1 className="text-2xl font-bold tracking-tight text-black">Welcome back</h1>
        {/* Darkened subtext to slate-700 */}
        <p className="text-slate-700 text-sm mt-1">Enter your credentials to access QA.core</p>
      </div>

      <div className="w-full max-w-[350px] space-y-4">
        {/* Error Message Section */}
        {error && (
          <div className="flex items-start space-x-2 bg-red-50 border border-red-100 p-3 rounded-lg animate-in fade-in zoom-in duration-200">
            <AlertCircle className="w-4 h-4 text-red-500 mt-0.5" />
            <div className="flex-1">
              <p className="text-sm text-red-600 font-medium">{error}</p>
              {showSignupLink && (
                <Link href="/signup" className="text-xs text-red-700 underline font-bold mt-1 block cursor-pointer">
                  Click here to create an account
                </Link>
              )}
            </div>
          </div>
        )}

        <div className="space-y-2">
          {/* Darkened label to slate-600 */}
          <label className="text-xs font-semibold uppercase tracking-wider text-slate-600 ml-1">Email Address</label>
          <input
            type="email"
            placeholder="name@company.com"
            onChange={(e) => setEmail(e.target.value)}
            /* Darkened border and placeholder text color */
            className="w-full border border-slate-300 p-3 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-400 transition-all text-sm placeholder:text-slate-500 text-slate-900"
          />
        </div>

        <div className="space-y-2">
          <div className="flex justify-between items-center ml-1">
            {/* Darkened label to slate-600 */}
            <label className="text-xs font-semibold uppercase tracking-wider text-slate-600">Password</label>
          </div>
          <input
            type="password"
            placeholder="••••••••"
            onChange={(e) => setPassword(e.target.value)}
            /* Darkened border and placeholder text color */
            className="w-full border border-slate-300 p-3 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-400 transition-all text-sm placeholder:text-slate-500 text-slate-900"
          />
        </div>

        <button
          onClick={handleLogin}
          className="w-full bg-slate-900 text-white font-semibold py-3 rounded-xl hover:bg-slate-800 transition-all flex items-center justify-center space-x-2 group shadow-lg shadow-slate-200 cursor-pointer"
        >
          <span>Sign In</span>
          <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
        </button>

        <p className="text-center text-sm text-slate-700 pt-4">
          Don&apos;t have an account?{" "}
          <Link href="/signup" className="text-blue-600 font-medium hover:underline cursor-pointer">
            Sign up
          </Link>
        </p>
      </div>
    </div>
  );
}