/**
 * Stage 8D — SEC/company catalyst evidence display tests.
 *
 * Covers:
 * - buildCatalystEvidenceDisplay: all path combinations
 * - ETF/crypto suppression (sec_lane_applicable=false → show=false)
 * - No raw backend codes in any returned string
 * - Decision authority note always present when showing
 * - Official catalyst card and editorial-suppressed card rendered correctly
 */

import { buildCatalystEvidenceDisplay, RAW_KEYS_BANNED } from "./intel-v3-explanation";
import type { SecCatalystEvidenceDisplay } from "./api";

function makeCat(overrides: Partial<SecCatalystEvidenceDisplay> = {}): SecCatalystEvidenceDisplay {
  return {
    sec_catalyst_found: false,
    editorial_suppressed: false,
    sec_lane_applicable: true,
    ...overrides,
  };
}

const RAW_CODES_8D = [
  "sec_catalyst_sentiment",
  "news_sentiment",
  "SUPPRESSED_INCOMPLETE",
  "USABLE_WITH_LIMITATIONS",
  "LIMITED",
  "READY",
  "MISSING",
  "THIN",
  "PARTIAL",
  "Stage 5J",
  "Stage 5K",
  "fact_kind",
];

function assertNoRawCodes(text: string) {
  for (const code of RAW_CODES_8D) {
    expect(text).not.toContain(code);
  }
  for (const key of RAW_KEYS_BANNED) {
    expect(text).not.toContain(key);
  }
}

describe("buildCatalystEvidenceDisplay", () => {
  describe("null / undefined / empty input", () => {
    it("returns show=false for null", () => {
      expect(buildCatalystEvidenceDisplay(null).show).toBe(false);
    });

    it("returns show=false for undefined", () => {
      expect(buildCatalystEvidenceDisplay(undefined).show).toBe(false);
    });
  });

  describe("ETF / non-equity suppression", () => {
    it("returns show=false when sec_lane_applicable=false", () => {
      const result = buildCatalystEvidenceDisplay(
        makeCat({ sec_lane_applicable: false, sec_catalyst_found: true })
      );
      expect(result.show).toBe(false);
    });

    it("returns show=false for ETF even when editorial suppressed", () => {
      const result = buildCatalystEvidenceDisplay(
        makeCat({ sec_lane_applicable: false, editorial_suppressed: true })
      );
      expect(result.show).toBe(false);
    });
  });

  describe("equity with no flags", () => {
    it("returns show=false when both flags are false for equity", () => {
      const result = buildCatalystEvidenceDisplay(makeCat());
      expect(result.show).toBe(false);
    });
  });

  describe("SEC catalyst found", () => {
    it("returns show=true when sec_catalyst_found=true", () => {
      const result = buildCatalystEvidenceDisplay(makeCat({ sec_catalyst_found: true }));
      expect(result.show).toBe(true);
    });

    it("populates official_catalyst when sec_catalyst_found=true", () => {
      const result = buildCatalystEvidenceDisplay(makeCat({ sec_catalyst_found: true }));
      expect(result.official_catalyst).toBeDefined();
    });

    it("does not set editorial_suppressed item when only catalyst found", () => {
      const result = buildCatalystEvidenceDisplay(makeCat({ sec_catalyst_found: true }));
      expect(result.editorial_suppressed).toBeUndefined();
    });

    it("official_catalyst title is plain-English (no raw codes)", () => {
      const result = buildCatalystEvidenceDisplay(makeCat({ sec_catalyst_found: true }));
      assertNoRawCodes(result.official_catalyst!.title);
    });

    it("official_catalyst body is plain-English", () => {
      const result = buildCatalystEvidenceDisplay(makeCat({ sec_catalyst_found: true }));
      assertNoRawCodes(result.official_catalyst!.body);
    });

    it("official_catalyst includes decision_authority_note", () => {
      const result = buildCatalystEvidenceDisplay(makeCat({ sec_catalyst_found: true }));
      expect(result.official_catalyst!.decision_authority_note).toBeTruthy();
      assertNoRawCodes(result.official_catalyst!.decision_authority_note);
    });

    it("official_catalyst decision_authority_note does not claim Buy/Hold/Trim/Sell authority", () => {
      const result = buildCatalystEvidenceDisplay(makeCat({ sec_catalyst_found: true }));
      const note = result.official_catalyst!.decision_authority_note;
      // Must acknowledge it did NOT determine the action
      expect(note.toLowerCase()).toMatch(/did not determine|did not decide|not.*decide/);
    });

    it("official_catalyst includes limitation_note", () => {
      const result = buildCatalystEvidenceDisplay(makeCat({ sec_catalyst_found: true }));
      expect(result.official_catalyst!.limitation_note).toBeTruthy();
    });

    it("official_catalyst includes source_label with SEC reference", () => {
      const result = buildCatalystEvidenceDisplay(makeCat({ sec_catalyst_found: true }));
      expect(result.official_catalyst!.source_label.toLowerCase()).toMatch(/sec|official|filing/);
    });
  });

  describe("editorial suppressed", () => {
    it("returns show=true when editorial_suppressed=true", () => {
      const result = buildCatalystEvidenceDisplay(makeCat({ editorial_suppressed: true }));
      expect(result.show).toBe(true);
    });

    it("populates editorial_suppressed item", () => {
      const result = buildCatalystEvidenceDisplay(makeCat({ editorial_suppressed: true }));
      expect(result.editorial_suppressed).toBeDefined();
    });

    it("does not set official_catalyst when only editorial suppressed", () => {
      const result = buildCatalystEvidenceDisplay(makeCat({ editorial_suppressed: true }));
      expect(result.official_catalyst).toBeUndefined();
    });

    it("editorial_suppressed title is plain-English", () => {
      const result = buildCatalystEvidenceDisplay(makeCat({ editorial_suppressed: true }));
      assertNoRawCodes(result.editorial_suppressed!.title);
    });

    it("editorial_suppressed body is plain-English", () => {
      const result = buildCatalystEvidenceDisplay(makeCat({ editorial_suppressed: true }));
      assertNoRawCodes(result.editorial_suppressed!.body);
    });
  });

  describe("both flags set", () => {
    it("returns both items when both flags true", () => {
      const result = buildCatalystEvidenceDisplay(
        makeCat({ sec_catalyst_found: true, editorial_suppressed: true })
      );
      expect(result.show).toBe(true);
      expect(result.official_catalyst).toBeDefined();
      expect(result.editorial_suppressed).toBeDefined();
    });
  });

  describe("raw key leak guard", () => {
    it("no raw backend codes in any string field", () => {
      const result = buildCatalystEvidenceDisplay(
        makeCat({ sec_catalyst_found: true, editorial_suppressed: true })
      );
      if (result.official_catalyst) {
        for (const val of Object.values(result.official_catalyst)) {
          assertNoRawCodes(String(val));
        }
      }
      if (result.editorial_suppressed) {
        for (const val of Object.values(result.editorial_suppressed)) {
          assertNoRawCodes(String(val));
        }
      }
    });
  });
});
