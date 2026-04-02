"use client";

import { useEffect, useState } from "react";
import { CheckCircle, FileText, Files, Info, Link as LinkIcon, Play, Ticket, Upload, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function UploadPage() {
  const [isVerified, setIsVerified] = useState(false);
  const [isLaunched, setIsLaunched] = useState(false);

  useEffect(() => {
    const handlePageShow = (event: PageTransitionEvent) => {
      if (event.persisted) {
        window.location.reload();
      }
    };

    window.addEventListener("pageshow", handlePageShow);
    return () => window.removeEventListener("pageshow", handlePageShow);
  }, []);

  return (
    <div className="mx-auto min-h-[calc(100vh-4rem)] w-full max-w-5xl px-4 py-10 sm:px-6">
      <div className="mb-8">
        <h1 className="text-3xl font-semibold text-black">Project Configuration</h1>
        <p className="mt-2 text-sm text-black/70">
          Fill in project metadata, launch verification, and upload your QA inputs.
        </p>
      </div>

      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>Project Identity</CardTitle>
            <CardDescription>Foundational details required to initialize SQAT intake.</CardDescription>
          </CardHeader>
          <CardContent className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="project-name">Project Name</Label>
              <Input id="project-name" type="text" placeholder="e.g. E-Commerce Core" />
            </div>

            <div className="space-y-2">
              <Label htmlFor="testing-team">Testing Team</Label>
              <div className="relative">
                <Users className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-black/60" />
                <Input id="testing-team" type="text" placeholder="Names of testers..." className="pl-10" />
              </div>
            </div>

            <div className="space-y-2 md:col-span-2">
              <Label htmlFor="project-description">Project Description</Label>
              <textarea
                id="project-description"
                rows={3}
                placeholder="Brief summary of project scope..."
                className="flex w-full resize-none rounded-md border border-black/20 bg-white px-3 py-2 text-sm text-black placeholder:text-black/45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2a63f5] focus-visible:ring-offset-2 focus-visible:ring-offset-white"
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Target URL Verification</CardTitle>
            <CardDescription>Launch and validate your target before final ingestion.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-col gap-2 sm:flex-row">
              <div className="relative flex-1">
                <LinkIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-black/60" />
                <Input type="url" placeholder="https://your-app.com" className="pl-10" />
              </div>
              <Button onClick={() => setIsLaunched(true)} className="sm:min-w-32">
                <Play className="h-4 w-4" />
                Launch
              </Button>
            </div>

            {isLaunched ? (
              <div className="flex flex-wrap items-center gap-2 rounded-lg border border-[#2a63f5]/25 bg-[#2a63f5]/10 p-3">
                <span className="text-sm text-black">Reviewing instance:</span>
                <Button type="button" variant="outline" size="sm" onClick={() => alert("Ticket Raised") }>
                  <Ticket className="h-4 w-4" />
                  Raise Ticket
                </Button>
                <Button
                  type="button"
                  variant={isVerified ? "default" : "outline"}
                  size="sm"
                  onClick={() => setIsVerified(true)}
                >
                  <CheckCircle className="h-4 w-4" />
                  {isVerified ? "Verified" : "Verify"}
                </Button>
              </div>
            ) : null}
          </CardContent>
        </Card>

        <section className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {[
            { label: "BRD", required: true, multiple: true },
            { label: "Credentials", required: true, multiple: false },
            { label: "Swagger Docs", required: true, multiple: false },
            { label: "FSD / WBS", required: false, multiple: true },
            { label: "Assumptions", required: false, multiple: false },
          ].map((doc) => (
            <Card key={doc.label} className="bg-white">
              <CardHeader className="pb-3">
                <CardTitle className="text-sm text-black">
                  {doc.label}
                  {doc.required ? <span className="ml-1 text-[#2a63f5]">*</span> : null}
                </CardTitle>
                <CardDescription className="text-xs">
                  {doc.multiple ? (
                    <span className="inline-flex items-center gap-1 text-[#2a63f5]">
                      <Files className="h-3 w-3" />
                      Multi-upload enabled
                    </span>
                  ) : (
                    "Single file upload"
                  )}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <label className="flex cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-black/20 bg-white p-4 text-center hover:border-[#2a63f5]">
                  <Upload className="mb-2 h-4 w-4 text-black/65" />
                  <span className="text-xs text-black/65">Attach {doc.multiple ? "Files" : "File"}</span>
                  <input type="file" className="hidden" multiple={doc.multiple} />
                </label>
              </CardContent>
            </Card>
          ))}
        </section>

        <div className="flex flex-col items-center gap-3 rounded-xl border border-black/15 bg-white p-6">
          {!isVerified ? (
            <p className="inline-flex items-center gap-2 rounded-full border border-[#2a63f5]/25 bg-[#2a63f5]/10 px-4 py-2 text-xs text-black">
              <Info className="h-4 w-4" />
              URL verification required to proceed
            </p>
          ) : null}

          <Button disabled={!isVerified} size="lg" className="w-full max-w-md">
            <FileText className="h-4 w-4" />
            Complete Ingestion
          </Button>
        </div>
      </div>
    </div>
  );
}
