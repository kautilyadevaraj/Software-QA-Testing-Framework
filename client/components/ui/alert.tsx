import * as React from "react";
import { cn } from "@/lib/utils";

function Alert({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      role="alert"
      className={cn(
        "w-full rounded-lg border border-[#2a63f5]/25 bg-[#2a63f5]/10 px-4 py-3 text-sm text-black",
        className
      )}
      {...props}
    />
  );
}

function AlertTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h5 className={cn("mb-1 font-medium leading-none tracking-tight", className)} {...props} />;
}

function AlertDescription({ className, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  return <div className={cn("text-sm", className)} {...props} />;
}

export { Alert, AlertTitle, AlertDescription };
