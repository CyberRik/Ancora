import { Placeholder } from "@/components/placeholder";

export default function HistoryPage() {
  return (
    <Placeholder
      title="History"
      phase="Phase 4"
      seeInstead={{ href: "/runs", label: "See per-run history" }}
    >
      A scrubber over the full event history, with deterministic replay from any point. Until it
      lands, every run page already carries its own attempt timeline and the recovery view that
      explains each wait.
    </Placeholder>
  );
}
