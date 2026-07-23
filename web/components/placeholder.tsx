import { ArrowRight } from "lucide-react";
import { ButtonLink, Card, PageHeader } from "@/components/ui";

/**
 * A section that is planned but not built.
 *
 * The previous copy ("scaffolded but not yet implemented") read as an unfinished
 * app. This says the same true thing as a roadmap entry, and — more usefully —
 * points at the shipped screen that covers the nearest need, so the visit is not
 * a dead end.
 */
export function Placeholder({
  title,
  phase,
  children,
  seeInstead,
}: {
  title: string;
  phase: string;
  children?: React.ReactNode;
  seeInstead?: { href: string; label: string };
}) {
  return (
    <div className="max-w-2xl space-y-6">
      <PageHeader eyebrow={`Planned · ${phase}`} title={title} />
      <Card className="border-dashed p-6">
        <p className="text-sm leading-relaxed text-muted-foreground">
          {children ?? "This screen is on the roadmap. See docs/IMPLEMENTATION-PLAN.md."}
        </p>
        {seeInstead && (
          <ButtonLink href={seeInstead.href} className="mt-4">
            {seeInstead.label}
            <ArrowRight className="h-3.5 w-3.5" />
          </ButtonLink>
        )}
      </Card>
    </div>
  );
}
