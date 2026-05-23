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

// ── Stage 8E: richer explanation fields ──────────────────────────────────────

const STAGE_8E_RAW_CODES = [
  "sec_catalyst_sentiment",
  "SEC_CATALYST_MODEL_VERSION",
  "skill_pack",
  "fact_kind",
  "READY",
  "LIMITED",
  "PARTIAL",
  "USABLE_WITH_LIMITATIONS",
  "SUPPRESSED_INCOMPLETE",
  "stage8c_sec_catalyst_sentiment",
  "Stage 5K",
  "Stage 5J",
];

function assertNoStage8ECodes(text: string) {
  for (const code of STAGE_8E_RAW_CODES) {
    expect(text).not.toContain(code);
  }
  for (const key of RAW_KEYS_BANNED) {
    expect(text).not.toContain(key);
  }
}

function makeEnrichedCat(
  overrides: Partial<import("@/lib/api").SecCatalystEvidenceDisplay> = {}
): import("@/lib/api").SecCatalystEvidenceDisplay {
  return {
    sec_catalyst_found: true,
    editorial_suppressed: false,
    sec_lane_applicable: true,
    event_summary:
      "Recent official filing activity was found. The filing appears material enough to support the sentiment evidence lane.",
    freshness_label: "Filing activity is within the relevant reporting window.",
    material_filing_label: "One recent official filing was found.",
    limitation_note: "This covers official company/SEC events only, not broad market opinion.",
    decision_authority_note:
      "This is useful context, but it does not decide Buy, Hold, Trim, or Sell by itself.",
    ...overrides,
  };
}

describe("Stage 8E: enriched catalyst explanation", () => {
  describe("usable SEC catalyst with explanation fields", () => {
    it("uses event_summary as official_catalyst body", () => {
      const result = buildCatalystEvidenceDisplay(makeEnrichedCat());
      expect(result.official_catalyst!.body).toContain("material enough");
    });

    it("includes material_filing_label in source_label", () => {
      const result = buildCatalystEvidenceDisplay(makeEnrichedCat());
      expect(result.official_catalyst!.source_label).toContain("One recent official filing");
    });

    it("includes freshness_label in limitation_note", () => {
      const result = buildCatalystEvidenceDisplay(makeEnrichedCat());
      expect(result.official_catalyst!.limitation_note).toContain(
        "within the relevant reporting window"
      );
    });

    it("uses decision_authority_note from payload field", () => {
      const result = buildCatalystEvidenceDisplay(makeEnrichedCat());
      expect(result.official_catalyst!.decision_authority_note).toContain("does not decide");
    });

    it("no raw codes in any enriched field", () => {
      const result = buildCatalystEvidenceDisplay(makeEnrichedCat());
      for (const val of Object.values(result.official_catalyst!)) {
        assertNoStage8ECodes(String(val));
      }
    });

    it("decision_authority_note does not claim authority", () => {
      const result = buildCatalystEvidenceDisplay(makeEnrichedCat());
      const note = result.official_catalyst!.decision_authority_note.toLowerCase();
      expect(note).toMatch(/does not decide|did not determine/);
    });
  });

  describe("minimal payload fallback", () => {
    it("falls back to generic body when event_summary absent", () => {
      const cat = makeCat({ sec_catalyst_found: true });
      const result = buildCatalystEvidenceDisplay(cat);
      expect(result.official_catalyst!.body).toContain("earnings reports or corporate announcements");
    });

    it("falls back to generic source_label when material_filing_label absent", () => {
      const cat = makeCat({ sec_catalyst_found: true });
      const result = buildCatalystEvidenceDisplay(cat);
      expect(result.official_catalyst!.source_label).toBe(
        "Source: Official company filings (SEC EDGAR)"
      );
    });

    it("falls back to generic limitation_note when freshness_label absent", () => {
      const cat = makeCat({ sec_catalyst_found: true });
      const result = buildCatalystEvidenceDisplay(cat);
      expect(result.official_catalyst!.limitation_note).not.toContain(
        "within the relevant reporting window"
      );
    });
  });

  describe("suppressed editorial alongside official catalyst evidence", () => {
    it("editorial body clarifies official filings were used instead", () => {
      const result = buildCatalystEvidenceDisplay(
        makeEnrichedCat({ editorial_suppressed: true })
      );
      expect(result.editorial_suppressed!.body).toContain("official company filings were used");
    });

    it("editorial body without SEC catalyst still says quality bar not met", () => {
      const result = buildCatalystEvidenceDisplay(
        makeCat({ editorial_suppressed: true, sec_catalyst_found: false })
      );
      expect(result.editorial_suppressed!.body).toContain("quality bar");
    });

    it("no raw codes in editorial body when both flags set", () => {
      const result = buildCatalystEvidenceDisplay(
        makeEnrichedCat({ editorial_suppressed: true })
      );
      assertNoStage8ECodes(result.editorial_suppressed!.body);
    });
  });

  describe("ETF / non-equity hidden state", () => {
    it("enriched catalyst still hidden for ETF (sec_lane_applicable=false)", () => {
      const result = buildCatalystEvidenceDisplay(
        makeEnrichedCat({ sec_lane_applicable: false })
      );
      expect(result.show).toBe(false);
    });
  });

  describe("raw-code leak guard for 8E fields", () => {
    it("no raw codes in any field of enriched result", () => {
      const result = buildCatalystEvidenceDisplay(
        makeEnrichedCat({ editorial_suppressed: true })
      );
      if (result.official_catalyst) {
        for (const val of Object.values(result.official_catalyst)) {
          assertNoStage8ECodes(String(val));
        }
      }
      if (result.editorial_suppressed) {
        for (const val of Object.values(result.editorial_suppressed)) {
          assertNoStage8ECodes(String(val));
        }
      }
    });
  });

  describe("no decision authority keys/phrases in enriched output", () => {
    it("official_catalyst does not contain standalone Buy/Sell/Trim/Hold as authority", () => {
      const result = buildCatalystEvidenceDisplay(makeEnrichedCat());
      const fullText = Object.values(result.official_catalyst!)
        .map(String)
        .join(" ")
        .toLowerCase();
      // The disclaimer is OK: "does not decide Buy, Hold, Trim, or Sell"
      // But no standalone authority claim
      expect(fullText).not.toMatch(/\bbuy now\b|\bsell now\b|\btrim now\b/);
    });
  });
});

// ── Stage 8F: filing-type specificity ────────────────────────────────────────

function makeEnrichedCat8F(
  overrides: Partial<import("@/lib/api").SecCatalystEvidenceDisplay> = {}
): import("@/lib/api").SecCatalystEvidenceDisplay {
  return {
    sec_catalyst_found: true,
    editorial_suppressed: false,
    sec_lane_applicable: true,
    event_summary: "Recent official filing activity was found. The filing appears material enough to support the sentiment evidence lane.",
    freshness_label: "Filing activity is within the relevant reporting window.",
    material_filing_label: "One recent official filing was found.",
    limitation_note: "This covers official company/SEC events only, not broad market opinion.",
    decision_authority_note: "This is useful context, but it does not decide Buy, Hold, Trim, or Sell by itself.",
    ...overrides,
  };
}

describe("Stage 8F: filing-type specificity", () => {
  describe("filing_type_label present on official_catalyst card", () => {
    it("passes through annual-report label when present", () => {
      const result = buildCatalystEvidenceDisplay(
        makeEnrichedCat8F({ filing_type_label: "Annual report (10-K)" })
      );
      expect(result.official_catalyst!.filing_type_label).toBe("Annual report (10-K)");
    });

    it("passes through quarterly-report label", () => {
      const result = buildCatalystEvidenceDisplay(
        makeEnrichedCat8F({ filing_type_label: "Quarterly report (10-Q)" })
      );
      expect(result.official_catalyst!.filing_type_label).toBe("Quarterly report (10-Q)");
    });

    it("passes through company-event-filing label for 8-K", () => {
      const result = buildCatalystEvidenceDisplay(
        makeEnrichedCat8F({ filing_type_label: "Company event filing (8-K)" })
      );
      expect(result.official_catalyst!.filing_type_label).toBe("Company event filing (8-K)");
    });

    it("passes through multiple-filings label", () => {
      const result = buildCatalystEvidenceDisplay(
        makeEnrichedCat8F({ filing_type_label: "Multiple recent official filings" })
      );
      expect(result.official_catalyst!.filing_type_label).toBe("Multiple recent official filings");
    });

    it("passes through generic-fallback label", () => {
      const result = buildCatalystEvidenceDisplay(
        makeEnrichedCat8F({ filing_type_label: "Official company filing" })
      );
      expect(result.official_catalyst!.filing_type_label).toBe("Official company filing");
    });
  });

  describe("filing_type_label absent when not provided", () => {
    it("filing_type_label is undefined when not in input", () => {
      const result = buildCatalystEvidenceDisplay(makeEnrichedCat8F());
      expect(result.official_catalyst!.filing_type_label).toBeUndefined();
    });

    it("stage 8E fallback still works without filing_type_label", () => {
      // Existing Stage 8E behavior must be unaffected when 8F field is absent.
      const result = buildCatalystEvidenceDisplay(makeEnrichedCat8F());
      expect(result.official_catalyst!.body).toContain("material enough");
      expect(result.show).toBe(true);
    });
  });

  describe("raw-code leak guard for 8F labels", () => {
    const BACKEND_CODES_8F = [
      "sec_catalyst_sentiment",
      "READY",
      "LIMITED",
      "PARTIAL",
      "USABLE_WITH_LIMITATIONS",
      "skill_pack",
      "fact_kind",
      "stage8f_filing_type_v1",
      "stage8f_filing_type_contract_version",
    ];

    it("no raw backend codes in annual-report label", () => {
      const result = buildCatalystEvidenceDisplay(
        makeEnrichedCat8F({ filing_type_label: "Annual report (10-K)" })
      );
      const label = result.official_catalyst!.filing_type_label ?? "";
      for (const code of BACKEND_CODES_8F) {
        expect(label).not.toContain(code);
      }
    });

    it("no raw backend codes in multiple-filings label", () => {
      const result = buildCatalystEvidenceDisplay(
        makeEnrichedCat8F({ filing_type_label: "Multiple recent official filings" })
      );
      const label = result.official_catalyst!.filing_type_label ?? "";
      for (const code of BACKEND_CODES_8F) {
        expect(label).not.toContain(code);
      }
    });
  });

  describe("ETF / non-equity hidden state unchanged", () => {
    it("enriched catalyst with filing_type_label still hidden for ETF", () => {
      const result = buildCatalystEvidenceDisplay(
        makeEnrichedCat8F({
          sec_lane_applicable: false,
          filing_type_label: "Annual report (10-K)",
        })
      );
      expect(result.show).toBe(false);
      expect(result.official_catalyst).toBeUndefined();
    });
  });

  describe("filing_type_label not set on editorial_suppressed card", () => {
    it("editorial card never has filing_type_label", () => {
      const result = buildCatalystEvidenceDisplay(
        makeEnrichedCat8F({
          editorial_suppressed: true,
          filing_type_label: "Annual report (10-K)",
        })
      );
      // filing_type_label should not bleed onto the editorial card.
      expect((result.editorial_suppressed as Record<string, unknown>)?.filing_type_label)
        .toBeUndefined();
    });
  });
});
