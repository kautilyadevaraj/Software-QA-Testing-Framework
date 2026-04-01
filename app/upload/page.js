"use client";
import React, { useState, useEffect} from 'react';
import { 
  ShieldCheck, Upload, FileText, LogOut, Play, 
  Ticket, CheckCircle, Info, Users, Link as LinkIcon, Files 
} from 'lucide-react';

export default function UploadPage() {
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

  const [isVerified, setIsVerified] = useState(false);
  const [isLaunched, setIsLaunched] = useState(false);

  const handleLogout = async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.replace("/login");
  };

  const handleLaunch = () => {
    setIsLaunched(true);
  };

  const handleVerify = () => {
    setIsVerified(true);
  };

  return (
    <div className="min-h-screen bg-white text-slate-900 font-sans pb-20">
      {/* Navigation */}
      <nav className="flex items-center justify-between border-b border-slate-100 px-8 py-4 bg-white sticky top-0 z-50">
        <div className="flex items-center space-x-2">
          <div className="h-7 w-7 bg-black rounded flex items-center justify-center">
            <ShieldCheck className="text-white w-4 h-4" />
          </div>
          <span className="font-bold tracking-tight text-lg text-black">QA.core</span>
        </div>
        <button onClick={handleLogout} className="flex items-center space-x-2 text-slate-600 hover:text-red-600 transition-colors text-sm font-bold px-3 py-2 rounded-lg hover:bg-red-50 cursor-pointer">
          <LogOut className="w-4 h-4" />
          <span>Logout</span>
        </button>
      </nav>

      <main className="max-w-4xl mx-auto pt-12 px-6">
        <div className="mb-10">
          <h1 className="text-3xl font-black text-black tracking-tight">Project Configuration</h1>
          <p className="text-slate-700 mt-2 font-bold text-sm">Fill in the details and verify the URL to enable the final upload.</p>
        </div>

        <div className="space-y-8">
          {/* Section 1: Project Identity */}
          <section className="grid grid-cols-1 md:grid-cols-2 gap-6 p-6 border border-slate-100 rounded-2xl bg-white shadow-sm ring-1 ring-slate-200">
            <div className="space-y-2">
              <label className="text-xs font-black uppercase tracking-widest text-slate-700">Project Name</label>
              <input type="text" placeholder="e.g. E-Commerce Core" className="w-full border border-slate-200 p-3 rounded-xl text-sm font-medium text-black placeholder:text-slate-400 focus:ring-4 focus:ring-blue-50 focus:border-blue-400 outline-none transition-all bg-white shadow-inner" />
            </div>
            <div className="space-y-2">
              <label className="text-xs font-black uppercase tracking-widest text-slate-700">Testing Team</label>
              <div className="relative flex items-center">
                <Users className="absolute left-3 w-4 h-4 text-slate-600" />
                <input type="text" placeholder="Names of testers..." className="w-full border border-slate-200 p-3 pl-10 rounded-xl text-sm font-medium text-black placeholder:text-slate-400 focus:ring-4 focus:ring-blue-50 focus:border-blue-400 outline-none transition-all bg-white shadow-inner" />
              </div>
            </div>
            <div className="md:col-span-2 space-y-2">
              <label className="text-xs font-black uppercase tracking-widest text-slate-700">Project Description</label>
              <textarea placeholder="Brief summary of project scope..." rows={2} className="w-full border border-slate-200 p-3 rounded-xl text-sm font-medium text-black placeholder:text-slate-400 focus:ring-4 focus:ring-blue-50 focus:border-blue-400 outline-none transition-all resize-none bg-white shadow-inner" />
            </div>
          </section>

          {/* Section 2: URL & Playwright Launch */}
          <section className="p-6 border border-slate-100 rounded-2xl bg-white shadow-md shadow-slate-100 ring-1 ring-slate-200">
            <label className="text-xs font-black uppercase tracking-widest text-slate-700 block mb-3">Target URL Verification</label>
            <div className="flex flex-col gap-4">
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <LinkIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-600" />
                  <input type="url" placeholder="https://your-app.com" className="w-full border border-slate-200 p-3 pl-10 rounded-xl text-sm font-bold text-black outline-none focus:border-blue-600 focus:ring-4 focus:ring-blue-50 transition-all" />
                </div>
                <button onClick={handleLaunch} className="bg-blue-600 text-white px-6 py-3 rounded-xl font-black flex items-center space-x-2 hover:bg-blue-700 transition-all shadow-lg shadow-blue-100 active:scale-95 cursor-pointer">
                  <Play className="w-4 h-4 fill-current" />
                  <span>Launch</span>
                </button>
              </div>

              {isLaunched && (
                <div className="flex items-center gap-3 animate-in fade-in slide-in-from-top-2 duration-500 bg-blue-50/40 p-4 rounded-xl border border-blue-100">
                  <span className="text-sm font-black text-blue-900">Reviewing Instance:</span>
                  <button onClick={() => alert('Ticket Raised')} className="flex items-center space-x-1.5 bg-white border border-slate-200 px-3 py-1.5 rounded-lg text-xs font-black hover:bg-red-50 hover:text-red-600 transition-all text-slate-800 shadow-sm cursor-pointer">
                    <Ticket className="w-3 h-3" />
                    <span>Raise Ticket</span>
                  </button>
                  <button onClick={handleVerify} className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-black transition-all cursor-pointer ${isVerified ? 'bg-green-600 text-white shadow-md' : 'bg-white border border-slate-200 text-green-700 hover:bg-green-50 shadow-sm'}`}>
                    <CheckCircle className="w-3 h-3" />
                    <span>{isVerified ? 'Verified' : 'Verify'}</span>
                  </button>
                </div>
              )}
            </div>
          </section>

          {/* Section 3: Document Uploads */}
          <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {[
              { label: "BRD", required: true, multiple: true },
              { label: "Credentials", required: true, multiple: false },
              { label: "Swagger Docs", required: true, multiple: false },
              { label: "FSD / WSB", required: false, multiple: true },
              { label: "Assumptions", required: false, multiple: false }
            ].map((doc, idx) => (
              <div key={idx} className="border border-slate-100 p-4 rounded-2xl bg-white hover:border-blue-300 transition-all group shadow-sm hover:shadow-md ring-1 ring-slate-200">
                <div className="flex justify-between items-start mb-3">
                  <div className="flex flex-col">
                    <span className="text-[10px] font-black uppercase tracking-wider text-slate-800 group-hover:text-blue-600">
                      {doc.label} {doc.required && <span className="text-red-600">*</span>}
                    </span>
                    {doc.multiple && <span className="text-[8px] font-bold text-blue-500 flex items-center gap-1 mt-0.5"><Files className="w-2 h-2"/> Multi-upload</span>}
                  </div>
                  <FileText className="w-4 h-4 text-slate-500" />
                </div>
                <label className="cursor-pointer flex flex-col items-center justify-center border-2 border-dashed border-slate-200 rounded-lg p-4 hover:bg-slate-50 hover:border-blue-200 transition-all bg-slate-50/20">
                  <Upload className="w-4 h-4 text-slate-600 mb-1" />
                  <span className="text-[10px] font-black text-slate-700">Attach {doc.multiple ? 'Files' : 'File'}</span>
                  <input type="file" className="hidden" multiple={doc.multiple} />
                </label>
              </div>
            ))}
          </section>

          {/* Section 4: Final Submission */}
          <div className="pt-6 border-t border-slate-100 flex flex-col items-center">
            {!isVerified && (
              <p className="text-xs text-red-600 font-black mb-4 flex items-center gap-1.5 bg-red-50/50 px-4 py-2 rounded-full border border-red-100 animate-pulse">
                <Info className="w-3.5 h-3.5" />
                URL Verification Required to Proceed
              </p>
            )}
            <button
              disabled={!isVerified}
              className={`w-full max-w-md py-4 rounded-2xl font-black tracking-widest uppercase transition-all flex items-center justify-center space-x-3 ${
                isVerified 
                ? 'bg-black text-white hover:scale-[1.02] active:scale-95 shadow-xl shadow-slate-200 hover:shadow-slate-300 cursor-pointer' 
                : 'bg-slate-100 text-slate-400 cursor-not-allowed border border-slate-200'
              }`}
            >
              <Upload className="w-5 h-5" />
              <span>Complete Ingestion</span>
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}