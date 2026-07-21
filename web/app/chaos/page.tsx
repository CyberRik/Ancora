import { Placeholder } from "@/components/placeholder";

export default function ChaosPage() {
  return (
    <Placeholder title="Chaos Lab" phase="Phase 5">
      Inject failures (kill worker, drop API, GPU OOM, infra restarts) and watch
      automatic recovery with measured RTO and invariant assertions.
    </Placeholder>
  );
}
