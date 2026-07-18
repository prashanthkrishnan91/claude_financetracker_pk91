/**
 * Cert secret safety — recreated from the retired PaycheckPlanPreviewContract
 * test (Stage 12E) after the legacy paycheck-plan surface was deleted.
 *
 * The runtime cert secret must only ever exist server-side inside
 * src/app/api/advisor/paycheck-plan/preview/route.ts. No other file under
 * src/ may reference the header or the env var, or the secret could leak
 * into client-bundled code.
 */

import fs from "fs";
import path from "path";

const SRC_ROOT = path.join(__dirname, "..");

/** Only file allowed to reference the cert secret (server-side proxy route). */
const ALLOWED_FILE = path.join(
  SRC_ROOT,
  "app",
  "api",
  "advisor",
  "paycheck-plan",
  "preview",
  "route.ts",
);

// Forbidden strings are assembled at runtime so this test file itself never
// contains the literal tokens it scans for.
const FORBIDDEN_ENV_VAR = ["FINANCE", "RUNTIME", "CERT", "SECRET"].join("_");
const FORBIDDEN_HEADER = ["X", "Finance", "Runtime", "Cert", "Secret"].join("-");

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...walk(full));
    } else if (entry.isFile()) {
      out.push(full);
    }
  }
  return out;
}

describe("cert secret never leaks outside the server-side preview route", () => {
  const allFiles = walk(SRC_ROOT);
  const scannedFiles = allFiles.filter(
    (f) => path.resolve(f) !== path.resolve(ALLOWED_FILE),
  );

  it("scans a non-trivial set of files under src/", () => {
    expect(scannedFiles.length).toBeGreaterThan(10);
  });

  it("the allowed server-side proxy route still exists", () => {
    expect(fs.existsSync(ALLOWED_FILE)).toBe(true);
  });

  it(`no file under src/ (except the preview route) contains the ${FORBIDDEN_ENV_VAR} env var`, () => {
    const offenders = scannedFiles.filter((f) =>
      fs.readFileSync(f, "utf-8").includes(FORBIDDEN_ENV_VAR),
    );
    expect(offenders).toEqual([]);
  });

  it(`no file under src/ (except the preview route) contains the ${FORBIDDEN_HEADER} header`, () => {
    const offenders = scannedFiles.filter((f) =>
      fs.readFileSync(f, "utf-8").includes(FORBIDDEN_HEADER),
    );
    expect(offenders).toEqual([]);
  });
});
