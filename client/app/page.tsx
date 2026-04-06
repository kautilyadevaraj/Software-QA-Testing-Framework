import Link from "next/link";
import { cookies } from "next/headers";
import { Activity, CheckCircle2, Play, Sparkles, Terminal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default async function Home() {
  const cookieStore = await cookies();
  const hasAuthToken = Boolean(cookieStore.get("access_token")?.value || cookieStore.get("token")?.value);

  return (
    <div className="mx-auto h-[calc(100vh-4rem)] w-full max-w-6xl overflow-hidden px-4 py-8 sm:px-6">
      <section className="grid h-full items-center gap-8 lg:grid-cols-[1.1fr_0.9fr]">
        <div className="space-y-6">
          <p className="inline-flex items-center gap-2 rounded-full border border-[#2a63f5]/30 bg-[#2a63f5]/10 px-4 py-1 text-xs tracking-widest text-[#2a63f5]">
            <Sparkles className="h-4 w-4" />
            SOFTWARE QA TESTING FRAMEWORK
          </p>
          <h1 className="text-4xl font-semibold leading-tight text-black sm:text-5xl">
            Autonomous QA pipelines built for speed.
          </h1>
          <p className="max-w-xl text-base leading-relaxed text-black/70">
            Upload your product documents and web app URL, then SQAT drafts high-level test scenarios. A tester walks
            through the app once, we track DOM-level navigation and interactions, and then generate Playwright E2E test
            cases from that journey.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Button asChild size="lg">
              <Link href="/projects">
                <Play className="h-4 w-4" />
                Go to Projects
              </Link>
            </Button>
            <Button asChild variant="outline" size="lg">
              <Link href={hasAuthToken ? "/projects" : "/login"}>
                <Terminal className="h-4 w-4" />
                Open Workspace
              </Link>
            </Button>
          </div>
        </div>

        <Card className="bg-white">
          <CardHeader>
            <CardTitle className="flex items-center justify-between text-black">
              Quick Status
              <span className="rounded-full bg-[#2a63f5]/10 px-3 py-1 text-xs text-[#2a63f5]">LIVE</span>
            </CardTitle>
            <CardDescription>Current execution signals from your workspace</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {[
              { label: "Checkout", status: "Passed", tone: "text-[#2a63f5]" },
              { label: "API Contract", status: "Running", tone: "text-black" },
              { label: "Auth Edge", status: "Queued", tone: "text-black/60" },
            ].map((item) => (
              <div
                key={item.label}
                className="flex items-center justify-between rounded-lg border border-black/10 bg-white px-3 py-2"
              >
                <div className="flex items-center gap-2 text-sm text-black">
                  <Activity className={`h-4 w-4 ${item.tone}`} />
                  {item.label}
                </div>
                <span className={`text-xs ${item.tone}`}>{item.status}</span>
              </div>
            ))}

            <div className="rounded-lg border border-[#2a63f5]/25 bg-[#2a63f5]/10 px-3 py-2 text-sm text-black">
              <p className="flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4 text-[#2a63f5]" />
                Coverage hit 92% after the latest execution.
              </p>
            </div>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
