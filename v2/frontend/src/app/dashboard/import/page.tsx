"use client";

import { useState, useRef } from "react";
import { useImportCsv, useImportPdf } from "@/lib/hooks";
import type { ImportResult, PdfImportResult } from "@/lib/api";

export default function ImportPage() {
  const csvRef = useRef<HTMLInputElement>(null);
  const pdfRef = useRef<HTMLInputElement>(null);
  const [csvResult, setCsvResult] = useState<ImportResult | null>(null);
  const [pdfResult, setPdfResult] = useState<PdfImportResult | null>(null);
  const importCsv = useImportCsv();
  const importPdf = useImportPdf();

  async function handleCsvFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setPdfResult(null);
    try {
      const data = await importCsv.mutateAsync(file);
      setCsvResult(data);
    } catch {
      // error handled by mutation
    }
  }

  async function handlePdfFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setCsvResult(null);
    try {
      const data = await importPdf.mutateAsync(file);
      setPdfResult(data);
    } catch {
      // error handled by mutation
    }
  }

  return (
    <>
      <header className="sticky top-0 z-50 bg-background/80 backdrop-blur-xl border-b border-border px-4 py-3">
        <div className="max-w-7xl mx-auto">
          <h1 className="text-xl font-display text-text-primary">Import</h1>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-6">
        {/* CSV Upload */}
        <div
          onClick={() => csvRef.current?.click()}
          className="card-glass p-8 text-center cursor-pointer hover:bg-surface-elevated/50 transition-colors"
        >
          <input
            ref={csvRef}
            type="file"
            accept=".csv"
            onChange={handleCsvFile}
            className="hidden"
          />
          <UploadIcon />
          <p className="text-text-primary font-medium mt-3">
            {importCsv.isPending
              ? "Importing CSV..."
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

        {csvResult && (
          <div className="card-glass p-4 space-y-3">
            <h2 className="text-sm font-semibold text-text-primary">
              CSV Import Results
            </h2>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <ResultStat label="Total Rows" value={csvResult.total_rows} />
              <ResultStat label="New" value={csvResult.new_rows} accent />
              <ResultStat label="Duplicates Skipped" value={csvResult.duplicates_skipped} />
              <ResultStat label="Errors" value={csvResult.errors} danger={csvResult.errors > 0} />
            </div>
            {csvResult.error_details.length > 0 && (
              <div className="mt-3">
                <p className="text-xs text-text-muted mb-1">Error details:</p>
                <ul className="text-xs text-danger space-y-1">
                  {csvResult.error_details.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {/* Divider */}
        <div className="flex items-center gap-3">
          <hr className="flex-1 border-border/50" />
          <span className="text-xs text-text-muted uppercase tracking-wide">or</span>
          <hr className="flex-1 border-border/50" />
        </div>

        {/* PDF Upload */}
        <div
          onClick={() => pdfRef.current?.click()}
          className="card-glass p-8 text-center cursor-pointer hover:bg-surface-elevated/50 transition-colors border border-dashed border-border"
        >
          <input
            ref={pdfRef}
            type="file"
            accept=".pdf"
            onChange={handlePdfFile}
            className="hidden"
          />
          <PdfIcon />
          <p className="text-text-primary font-medium mt-3">
            {importPdf.isPending
              ? "Parsing PDF..."
              : "Click to upload Robinhood Crypto PDF"}
          </p>
          <p className="text-xs text-text-muted mt-1">
            Parses crypto holdings (BTC, ETH, XRP, SOL…) from monthly statement PDFs
          </p>
        </div>

        {importPdf.isError && (
          <div className="card-glass p-4 border border-danger/30 bg-danger/5">
            <p className="text-sm text-danger">
              PDF import failed: {importPdf.error?.message}
            </p>
          </div>
        )}

        {pdfResult && (
          <div className="card-glass p-4 space-y-3">
            <h2 className="text-sm font-semibold text-text-primary">
              PDF Import Results
            </h2>
            <div className="grid grid-cols-3 gap-3">
              <ResultStat
                label="Tickers Found"
                value={pdfResult.tickers_found.length}
                accent
              />
              <ResultStat label="Updated" value={pdfResult.positions_updated} />
              <ResultStat label="Created" value={pdfResult.positions_created} accent />
            </div>
            {pdfResult.tickers_found.length > 0 && (
              <p className="text-xs text-text-muted">
                Found: {pdfResult.tickers_found.join(", ")}
              </p>
            )}
            {pdfResult.errors.length > 0 && (
              <div className="mt-2">
                <p className="text-xs text-text-muted mb-1">Errors:</p>
                <ul className="text-xs text-danger space-y-1">
                  {pdfResult.errors.map((e, i) => <li key={i}>{e}</li>)}
                </ul>
              </div>
            )}
          </div>
        )}

        {/* Help text */}
        <div className="card-glass p-4 space-y-4">
          <div>
            <h3 className="text-sm font-medium text-text-primary mb-2">
              How to export from Robinhood (CSV)
            </h3>
            <ol className="text-xs text-text-secondary space-y-1 list-decimal list-inside">
              <li>Open Robinhood web or app</li>
              <li>Go to Account &gt; Statements &gt; Transaction History</li>
              <li>Select date range and download CSV</li>
              <li>Upload the CSV file above</li>
            </ol>
          </div>
          <hr className="border-border/50" />
          <div>
            <h3 className="text-sm font-medium text-text-primary mb-2">
              How to get Robinhood Crypto PDF statement
            </h3>
            <ol className="text-xs text-text-secondary space-y-1 list-decimal list-inside">
              <li>Open Robinhood web or app</li>
              <li>Go to Account &gt; Statements &gt; Monthly Statements</li>
              <li>Download a Crypto monthly statement (PDF)</li>
              <li>Upload the PDF above — crypto holdings are extracted automatically</li>
            </ol>
            <p className="text-xs text-text-muted mt-2">
              Note: avg cost is not in the PDF. Update positions manually after import if needed.
            </p>
          </div>
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

function PdfIcon() {
  return (
    <svg
      className="w-10 h-10 mx-auto text-text-muted"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
    >
      <path
        d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <polyline points="14 2 14 8 20 8" />
      <line x1="9" y1="13" x2="15" y2="13" />
      <line x1="9" y1="17" x2="15" y2="17" />
      <line x1="9" y1="9" x2="11" y2="9" />
    </svg>
  );
}
