"use client";

import { useState, useRef } from "react";
import { useImportCsv } from "@/lib/hooks";
import type { ImportResult } from "@/lib/api";

export default function ImportPage() {
  const fileRef = useRef<HTMLInputElement>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const importCsv = useImportCsv();

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;

    try {
      const data = await importCsv.mutateAsync(file);
      setResult(data);
    } catch {
      // error handled by mutation
    }
  }

  return (
    <>
      <header className="sticky top-0 z-50 bg-background/80 backdrop-blur-xl border-b border-border px-4 py-3">
        <div className="max-w-7xl mx-auto">
          <h1 className="text-xl font-display text-text-primary">
            Import CSV
          </h1>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-6">
        {/* Upload area */}
        <div
          onClick={() => fileRef.current?.click()}
          className="card-glass p-8 text-center cursor-pointer hover:bg-surface-elevated/50 transition-colors"
        >
          <input
            ref={fileRef}
            type="file"
            accept=".csv"
            onChange={handleFile}
            className="hidden"
          />
          <UploadIcon />
          <p className="text-text-primary font-medium mt-3">
            {importCsv.isPending
              ? "Importing..."
              : "Click to upload Robinhood CSV"}
          </p>
          <p className="text-xs text-text-muted mt-1">
            SHA-256 fingerprinting ensures safe re-imports (no duplicates)
          </p>
        </div>

        {importCsv.isError && (
          <div className="card-glass p-4 border border-danger/30 bg-danger/5">
            <p className="text-sm text-danger">
              Import failed: {importCsv.error?.message}
            </p>
          </div>
        )}

        {/* Result */}
        {result && (
          <div className="card-glass p-4 space-y-3">
            <h2 className="text-sm font-semibold text-text-primary">
              Import Results
            </h2>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <ResultStat label="Total Rows" value={result.total_rows} />
              <ResultStat
                label="New"
                value={result.new_rows}
                accent
              />
              <ResultStat
                label="Duplicates Skipped"
                value={result.duplicates_skipped}
              />
              <ResultStat
                label="Errors"
                value={result.errors}
                danger={result.errors > 0}
              />
            </div>

            {result.error_details.length > 0 && (
              <div className="mt-3">
                <p className="text-xs text-text-muted mb-1">Error details:</p>
                <ul className="text-xs text-danger space-y-1">
                  {result.error_details.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {/* Help text */}
        <div className="card-glass p-4">
          <h3 className="text-sm font-medium text-text-primary mb-2">
            How to export from Robinhood
          </h3>
          <ol className="text-xs text-text-secondary space-y-1 list-decimal list-inside">
            <li>Open Robinhood web or app</li>
            <li>Go to Account &gt; Statements &gt; Transaction History</li>
            <li>Select date range and download CSV</li>
            <li>Upload the CSV file here</li>
          </ol>
        </div>
      </main>
    </>
  );
}

function ResultStat({
  label,
  value,
  accent,
  danger,
}: {
  label: string;
  value: number;
  accent?: boolean;
  danger?: boolean;
}) {
  return (
    <div className="text-center">
      <p className="text-xs text-text-muted">{label}</p>
      <p
        className={`text-lg font-mono font-semibold ${
          danger ? "text-danger" : accent ? "text-accent" : "text-text-primary"
        }`}
      >
        {value}
      </p>
    </div>
  );
}

function UploadIcon() {
  return (
    <svg
      className="w-10 h-10 mx-auto text-text-muted"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
    >
      <path
        d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
