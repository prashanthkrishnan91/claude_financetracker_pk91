"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { createPortal } from "react-dom";
import { cn, formatCurrency } from "@/lib/utils";
import {
  usePortfolioSummary,
  useCashBalance,
  useSetCash,
  useDepositPlan,
  useDecisionOutcomes,
  useCreateDecisionMemoryLog,
  useDecisionMemoryLogs,
  useEvaluateDecisionMemoryLog,
  useUpdateDecisionMemoryLog,
  useDecisionPerformanceInsights,
  useDeployV3Plan,
  useDeployV3Readiness,
} from "@/lib/hooks";
import type {
  AdaptiveBlock,
  AllocationExclusion,
  DepositPlanResult,
  DepositRecommendation,
  ActualDecisionItem,
  DecisionLogEntry,
  DecisionMemoryLog,
  RegimeBlock,
} from "@/lib/api";
import { InlineLoader } from "@/components/ui/Spinner";
import { Spinner } from "@/components/ui/Spinner";
import { DeployV3Panel } from "@/components/cards/DeployV3Panel";
import { DeployV3ReadinessPanel } from "@/components/cards/DeployV3ReadinessPanel";
import { DeployV3TargetSetupPanel } from "@/components/cards/DeployV3TargetSetupPanel";
import { mapDeployV3ToStep2 } from "@/lib/deploy-v3-step2-mapper";
import { buildInitialActualDecisions, buildRecommendationSnapshotWithContext, dedupeDecisionLogsForDisplay, deriveExecutionStatus, getDecisionLogSessionKey } from "@/lib/decision-log";
import type { ExecutionStatus } from "@/lib/decision-log";
import { buildDeployV3DecisionSnapshot, buildDeployV3InitialActualDecisions, buildDeployV3SessionKey } from "@/lib/deploy-v3-decision-log";
import type { DeployV3PlanResponse } from "@/lib/api";
