import React from 'react';
import Link from 'next/link'; // Import Link for navigation
import { Play, Activity, ShieldCheck, Terminal, ChevronRight } from 'lucide-react';

export default function Home() {
  return (
    <div className="min-h-screen bg-white text-slate-900 selection:bg-blue-100">
      {/* Top Navigation */}
      <nav className="flex items-center justify-between border-b border-slate-100 px-8 py-4">
        <div className="flex items-center space-x-2">
          <div className="h-8 w-8 bg-black rounded flex items-center justify-center">
            <ShieldCheck className="text-white w-5 h-5" />
          </div>
          <span className="font-bold tracking-tight text-xl">QA.core</span>
        </div>
        <div className="flex items-center space-x-6 text-sm font-medium">
          <a href="/login" className="hover:text-blue-600 transition-colors cursor-pointer">Log in</a>
          <a href="/signup" className="bg-slate-900 text-white px-4 py-2 rounded-lg hover:bg-slate-800 transition-all cursor-pointer">
            Get Started
          </a>
        </div>
      </nav>

      {/* Hero / Dashboard View */}
      <main className="max-w-6xl mx-auto pt-20 px-6">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 items-center">
          
          {/* Left Side: Copy */}
          <div className="space-y-6">
            <div className="inline-flex items-center space-x-2 bg-blue-50 text-blue-600 px-3 py-1 rounded-full text-xs font-semibold uppercase tracking-wider">
              <span>v1.0 is now live</span>
            </div>
            <h1 className="text-6xl font-extrabold tracking-tight leading-tight">
              Automated testing <br /> 
              <span className="text-slate-400">reimagined.</span>
            </h1>
            <p className="text-lg text-slate-500 max-w-md leading-relaxed">
              The ultimate framework for full-stack QA engineering. Ingest docs, generate suites, and deploy autonomous QA agents in minutes.
            </p>
            <div className="flex items-center space-x-4">
              {/* Wrapped in Link to go to /upload */}
              <Link href="/upload">
                <button className="flex items-center space-x-2 bg-blue-600 text-white px-6 py-3 rounded-xl font-semibold hover:bg-blue-700 transition-all shadow-lg shadow-blue-200 cursor-pointer">
                  <Play className="w-4 h-4 fill-current" />
                  <span>Run First Suite</span>
                </button>
              </Link>
              
              <button className="flex items-center space-x-2 border border-slate-200 px-6 py-3 rounded-xl font-semibold hover:bg-slate-50 transition-all cursor-pointer">
                <Terminal className="w-4 h-4" />
                <span>View Docs</span>
              </button>
            </div>
          </div>

          {/* Right Side: Visual Placeholder */}
          <div className="relative">
            <div className="absolute -inset-1 bg-gradient-to-r from-blue-100 to-indigo-100 rounded-2xl blur opacity-30"></div>
            <div className="relative bg-white border border-slate-200 rounded-2xl shadow-2xl p-6 overflow-hidden">
              <div className="flex items-center justify-between mb-6">
                <div className="flex space-x-1.5">
                  <div className="w-3 h-3 bg-red-400 rounded-full"></div>
                  <div className="w-3 h-3 bg-amber-400 rounded-full"></div>
                  <div className="w-3 h-3 bg-green-400 rounded-full"></div>
                </div>
                <div className="text-[10px] font-mono text-slate-400">~/projects/qa-framework</div>
              </div>
              
              <div className="space-y-4">
                {[
                  { label: 'E2E User Flow', status: 'Passed', color: 'text-green-500', bg: 'bg-green-50' },
                  { label: 'API Edge Cases', status: 'Running', color: 'text-blue-500', bg: 'bg-blue-50' },
                  { label: 'Latency Stress Test', status: 'Pending', color: 'text-slate-400', bg: 'bg-slate-50' },
                ].map((item, i) => (
                  <div key={i} className={`flex items-center justify-between p-3 rounded-lg border border-slate-100 ${item.bg}`}>
                    <div className="flex items-center space-x-3">
                      <Activity className={`w-4 h-4 ${item.color}`} />
                      <span className="text-sm font-medium">{item.label}</span>
                    </div>
                    <span className={`text-[10px] font-bold uppercase tracking-wider ${item.color}`}>{item.status}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}