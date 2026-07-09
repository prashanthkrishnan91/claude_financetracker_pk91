import { PaycheckPlanPreviewCard } from "@/components/cards/PaycheckPlanPreviewCard";

export default function PaycheckPlanPage() {
  return (
    <div className="max-w-xl mx-auto p-4 space-y-4">
      <h1 className="text-lg font-semibold text-text-primary">Paycheck Plan Preview</h1>
      <PaycheckPlanPreviewCard />
    </div>
  );
}
