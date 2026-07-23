"use client";

import dynamic from "next/dynamic";
import "swagger-ui-react/swagger-ui.css";

const SwaggerUI = dynamic(() => import("swagger-ui-react"), { ssr: false });

export default function ApiDocsPage() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-bold tracking-tight">API Documentation</h1>
      </div>
      <div className="rounded-xl border bg-white p-6 shadow-sm swagger-ui-container">
        <SwaggerUI url="/openapi.json" />
      </div>
    </div>
  );
}
